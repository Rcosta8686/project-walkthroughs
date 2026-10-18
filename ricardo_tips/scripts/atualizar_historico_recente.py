"""Atualiza o historico com jogos TERMINADOS mais recentes da epoca
corrente de cada liga ja populada.

popular_cerebros.py usa /team/{id}/events/last/{pag} que em certos
casos devolve so jogos da epoca anterior. Este script usa
/unique-tournament/{tid}/season/{sid}/events/last/{pag} que devolve
cronologicamente os ultimos jogos (incluindo da epoca corrente).

Idempotente — correr quantas vezes quiser.

Uso:
    # Default: todas as ligas prioritarias, ate 50 jogos recentes por liga
    python scripts/atualizar_historico_recente.py

    # So uma liga
    python scripts/atualizar_historico_recente.py --liga "Serie A (ITA)"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.dados_referencia.ligas_prioritarias import (  # noqa: E402
    LIGAS_PRIORITARIAS, LigaPrioritaria,
)
from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.ingestao.sofascore_live import _get_json  # noqa: E402
from apostas.modelos import historico_equipas  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def _season_actual(tournament_id: int) -> int | None:
    for path in (
        f"/unique-tournament/{tournament_id}/seasons",
        f"/tournament/{tournament_id}/seasons",
    ):
        try:
            data = _get_json(path)
        except Exception:  # noqa: BLE001
            continue
        if data is None:
            continue
        seasons = data.get("seasons") or []
        if seasons:
            return seasons[0].get("id")
    return None


def _eventos_terminados_recentes(tournament_id: int, season_id: int,
                                   max_pags: int = 3) -> list[int]:
    """IDs de eventos terminados da epoca corrente, mais recentes primeiro."""
    ids: list[int] = []
    for pag in range(0, max_pags):
        data = _get_json(
            f"/unique-tournament/{tournament_id}/season/{season_id}/events/last/{pag}"
        )
        if data is None:
            break
        eventos = data.get("events") or []
        if not eventos:
            break
        for ev in eventos:
            status = ev.get("status") or {}
            code = status.get("code") or 0
            desc = str(status.get("description") or "").lower()
            if code not in (100, 110, 120) and not any(
                t in desc for t in ("ended", "finished", "ft", "aet", "pen")
            ):
                continue
            eid = ev.get("id")
            if eid:
                ids.append(int(eid))
        time.sleep(0.3)
    return ids


def _atualizar_liga(liga: LigaPrioritaria) -> dict:
    print(f"\n━━━ {liga.nome} ━━━")
    season_id = _season_actual(liga.sofascore_id)
    if season_id is None:
        print(f"  ⚠ Nao consegui obter a epoca para {liga.nome}")
        return {"eventos": 0, "linhas_novas": 0}

    event_ids = _eventos_terminados_recentes(liga.sofascore_id, season_id)
    print(f"  → {len(event_ids)} eventos terminados recentes encontrados")

    linhas = 0
    for i, eid in enumerate(event_ids, 1):
        try:
            n_add = historico_equipas.gravar_jogo_terminado(eid)
            linhas += n_add
            if i % 10 == 0:
                print(f"    processados {i}/{len(event_ids)} eventos, "
                      f"+{linhas} linhas novas")
        except FlaresolverrIndisponivel:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("gravar event %s: %s", eid, exc)
        time.sleep(0.25)
    print(f"  ✓ +{linhas} linhas novas")
    return {"eventos": len(event_ids), "linhas_novas": linhas}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--liga", default=None)
    args = parser.parse_args()

    if args.liga:
        alvos = [l for l in LIGAS_PRIORITARIAS if l.nome == args.liga]
        if not alvos:
            print(f"⚠ Liga '{args.liga}' nao esta em LIGAS_PRIORITARIAS.")
            sys.exit(1)
    else:
        alvos = sorted(LIGAS_PRIORITARIAS, key=lambda l: (l.prioridade, l.nome))

    print(f"\n╔ Atualizar historico com epoca corrente — {len(alvos)} ligas ╗\n")

    total_eventos = 0
    total_linhas = 0
    for liga in alvos:
        try:
            r = _atualizar_liga(liga)
        except FlaresolverrIndisponivel:
            print("\n⚠ Flaresolverr OFF. docker start flaresolverr e re-corre.")
            break
        total_eventos += r["eventos"]
        total_linhas += r["linhas_novas"]
        time.sleep(1.0)

    print(f"\n╔ TOTAL: {total_eventos} eventos, +{total_linhas} linhas novas ╗\n")


if __name__ == "__main__":
    main()
