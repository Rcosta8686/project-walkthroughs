"""Popular o Segundo Cerebro: para cada liga prioritaria, puxa a tabela
classificativa (standings), extrai todas as equipas, e para cada equipa
grava ultimos N jogos em EstatisticaEquipaJogo.

Fluxo:
  1. Le LIGAS_PRIORITARIAS (apostas/dados_referencia).
  2. Para cada liga, resolve season_id actual via /unique-tournament/{id}/seasons.
  3. Pede /unique-tournament/{id}/season/{sid}/standings/total → lista equipas.
  4. Para cada equipa, chama historico_equipas.gravar_jogo_terminado()
     para os ultimos N jogos (via /team/{id}/events/last/{page}).
  5. Idempotente — correr varias vezes nao duplica.

Uso:
    # Popular prioridade 1 (Big 5 + Portugal + Eredivisie), 20 jogos cada
    python scripts/popular_cerebros.py --prioridade 1 --n 20

    # Tudo (incluindo segundas divisoes), 15 jogos cada
    python scripts/popular_cerebros.py --n 15

    # 1 liga especifica (ex: Serie A)
    python scripts/popular_cerebros.py --liga "Serie A (ITA)" --n 20

Dependencias: flaresolverr a correr (porta 8191).
Tempo estimado: ~2-3 min por liga (18-20 equipas × 15 jogos × 1s delay).
"""
from __future__ import annotations

import argparse
import html as _html
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
    LIGAS_PRIORITARIAS, LigaPrioritaria, ligas_de_prioridade,
)
from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.ingestao.sofascore_live import _get_json  # noqa: E402
from apostas.modelos import historico_equipas  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def _season_actual(tournament_id: int) -> int | None:
    """Devolve o id da epoca mais recente (primeira na lista Sofascore).

    Tenta /unique-tournament/{id}/seasons primeiro. Fallback para
    /tournament/{id}/seasons (algumas ligas usam o endpoint sem 'unique-').
    """
    paths = [
        f"/unique-tournament/{tournament_id}/seasons",
        f"/tournament/{tournament_id}/seasons",
    ]
    for path in paths:
        try:
            data = _get_json(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("Seasons via %s falhou: %s", path, exc)
            continue
        if data is None:
            continue
        seasons = data.get("seasons") or []
        if seasons:
            return seasons[0].get("id")
    return None


def _equipas_da_liga(tournament_id: int, season_id: int) -> list[tuple[int, str]]:
    """Lista equipas da classificacao: (team_id, nome).

    Robusto: tenta standings/total; se vier vazio, cai para listar equipas
    dos eventos recentes (events/last/0 do torneio).
    """
    data = _get_json(
        f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
    )
    equipas: dict[int, str] = {}
    if data:
        for row in data.get("standings") or []:
            for item in row.get("rows") or []:
                team = item.get("team") or {}
                tid = team.get("id")
                if tid and tid not in equipas:
                    equipas[tid] = _html.unescape(team.get("name") or f"team_{tid}")
    if equipas:
        return list(equipas.items())

    # Fallback: eventos recentes
    data = _get_json(
        f"/unique-tournament/{tournament_id}/season/{season_id}/events/last/0"
    )
    if data:
        for ev in data.get("events") or []:
            for lado in ("homeTeam", "awayTeam"):
                team = ev.get(lado) or {}
                tid = team.get("id")
                if tid and tid not in equipas:
                    equipas[tid] = _html.unescape(team.get("name") or f"team_{tid}")
    return list(equipas.items())


def _ultimos_eventos_equipa(team_id: int, max_eventos: int) -> list[int]:
    """IDs dos ultimos max_eventos eventos TERMINADOS da equipa."""
    ids: list[int] = []
    for pag in range(0, 3):
        if len(ids) >= max_eventos:
            break
        data = _get_json(f"/team/{team_id}/events/last/{pag}")
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
            if len(ids) >= max_eventos:
                break
        time.sleep(0.3)
    return ids


def _popular_liga(liga: LigaPrioritaria, n_jogos: int) -> dict:
    """Popula uma liga. Devolve stats {equipas, jogos_novos, eventos_processados}."""
    print(f"\n━━━ {liga.nome} (prioridade {liga.prioridade}, {liga.pais}) ━━━")
    season_id = _season_actual(liga.sofascore_id)
    if season_id is None:
        print(f"  ⚠ Nao consegui obter a epoca para {liga.nome}")
        return {"equipas": 0, "jogos_novos": 0, "eventos_processados": 0}

    equipas = _equipas_da_liga(liga.sofascore_id, season_id)
    print(f"  → {len(equipas)} equipas encontradas na classificacao")

    stats = {"equipas": len(equipas), "jogos_novos": 0, "eventos_processados": 0}
    for i, (team_id, team_nome) in enumerate(equipas, 1):
        print(f"  [{i}/{len(equipas)}] {team_nome} (id={team_id})")
        try:
            event_ids = _ultimos_eventos_equipa(team_id, n_jogos)
        except FlaresolverrIndisponivel as exc:
            print(f"⚠ Flaresolverr OFF: {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("team %s events falhou: %s", team_id, exc)
            continue
        for eid in event_ids:
            try:
                n_add = historico_equipas.gravar_jogo_terminado(eid)
                stats["jogos_novos"] += n_add
                stats["eventos_processados"] += 1
            except FlaresolverrIndisponivel:
                print(f"⚠ Flaresolverr caiu durante event {eid}")
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("gravar event %s falhou: %s", eid, exc)
            time.sleep(0.3)
        time.sleep(0.8)  # entre equipas

    print(f"  ✓ {liga.nome}: {stats['eventos_processados']} eventos processados, "
          f"+{stats['jogos_novos']} linhas novas")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15,
                        help="Numero de jogos passados por equipa (default 15).")
    parser.add_argument("--prioridade", type=int, default=None, choices=(1, 2),
                        help="So ligas desta prioridade (1=foco, 2=secundario).")
    parser.add_argument("--liga", default=None,
                        help="So esta liga (nome exacto, ex: 'Serie A (ITA)').")
    args = parser.parse_args()

    if args.liga:
        alvos = [l for l in LIGAS_PRIORITARIAS if l.nome == args.liga]
        if not alvos:
            print(f"⚠ Liga '{args.liga}' nao esta em LIGAS_PRIORITARIAS.")
            print("Opcoes:")
            for l in LIGAS_PRIORITARIAS:
                print(f"  - {l.nome}")
            sys.exit(1)
    elif args.prioridade is not None:
        alvos = ligas_de_prioridade(args.prioridade)
    else:
        alvos = sorted(LIGAS_PRIORITARIAS, key=lambda l: (l.prioridade, l.nome))

    print(f"\n╔════════════════════════════════════════════════════╗")
    print(f"║ Popular Segundo Cerebro — {len(alvos)} ligas, {args.n} jogos/equipa")
    print(f"╚════════════════════════════════════════════════════╝")

    total = {"equipas": 0, "jogos_novos": 0, "eventos_processados": 0}
    # Early-abort: se 2 ligas seguidas falham a obter a epoca, o Cloudflare
    # bloqueou — nao vale a pena martelar as restantes a consumir cooldown.
    falhas_consecutivas = 0
    for liga in alvos:
        try:
            s = _popular_liga(liga, args.n)
        except FlaresolverrIndisponivel:
            print("\n⚠ Flaresolverr indisponivel. Para continuar:")
            print("    docker start flaresolverr")
            print("    (e re-correr este script — ja saltara o que foi gravado)")
            break
        total["equipas"] += s["equipas"]
        total["jogos_novos"] += s["jogos_novos"]
        total["eventos_processados"] += s["eventos_processados"]
        if s["equipas"] == 0 and s["eventos_processados"] == 0:
            falhas_consecutivas += 1
        else:
            falhas_consecutivas = 0
        if falhas_consecutivas >= 2:
            print("\n⚠ 2 ligas consecutivas sem dados — Cloudflare bloqueou o IP.")
            print("    Esperar 1-4 horas antes de nova tentativa.")
            print("    docker restart flaresolverr tambem pode ajudar.")
            print("    O que ja foi gravado nao se perde (idempotente).")
            break
        time.sleep(4.0)  # entre ligas — folga para o Cloudflare nao desconfiar

    print(f"\n╔════════════════════════════════════════════════════╗")
    print(f"║ TOTAL: {total['equipas']} equipas | "
          f"{total['eventos_processados']} eventos | "
          f"+{total['jogos_novos']} linhas novas")
    print(f"╚════════════════════════════════════════════════════╝")


if __name__ == "__main__":
    main()
