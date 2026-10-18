"""Backfill da tabela EstatisticaEquipaJogo a partir do Sofascore.

Para cada equipa alvo, puxa os ultimos N jogos terminados e chama
historico_equipas.gravar_jogo_terminado(event_id) — append-only, seguro
correr varias vezes.

Equipas alvo (por ordem):
  1. Equipas ja presentes na tabela (actualiza quem ja la esta).
  2. Equipas na tabela Equipa com sofascore_team_id (se tivermos).
  3. --team-id <id> explicito via CLI.

Uso:
    python scripts/backfill_historico_equipas.py --n 20
    python scripts/backfill_historico_equipas.py --team-id 2673
    python scripts/backfill_historico_equipas.py --n 10 --max-equipas 50

Depende: flaresolverr a correr. Pode demorar (varios minutos para 50 equipas).
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

from sqlalchemy import select  # noqa: E402

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.ingestao.sofascore_live import _get_json  # noqa: E402
from apostas.modelos import historico_equipas  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402

log = get_logger(__name__)


def _ids_equipas_conhecidas() -> list[tuple[int, str]]:
    """Devolve (sofascore_team_id, nome) distintos da tabela historico."""
    with abrir_sessao() as s:
        rows = s.execute(
            select(
                EstatisticaEquipaJogo.sofascore_team_id,
                EstatisticaEquipaJogo.nome_equipa,
            )
            .distinct()
        ).all()
    return [(r[0], r[1]) for r in rows]


def _ultimos_eventos_equipa(team_id: int, max_eventos: int) -> list[int]:
    """Devolve ids dos ultimos max_eventos eventos TERMINADOS da equipa."""
    ids: list[int] = []
    # Sofascore pagina 0=mais recente, 1=anterior, ...
    for pag in range(0, 3):  # 3 paginas * ~15 = 45 eventos possiveis
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
            if (status.get("code") or 0) != 100:
                continue
            eid = ev.get("id")
            if eid:
                ids.append(int(eid))
            if len(ids) >= max_eventos:
                break
        time.sleep(0.3)  # throttle
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20,
                        help="Numero de jogos passados por equipa (default 20).")
    parser.add_argument("--max-equipas", type=int, default=100,
                        help="Maximo de equipas a processar (default 100).")
    parser.add_argument("--team-id", type=int, default=None,
                        help="Processar apenas 1 equipa (sofascore_team_id).")
    args = parser.parse_args()

    if args.team_id:
        alvos = [(args.team_id, f"team_{args.team_id}")]
    else:
        alvos = _ids_equipas_conhecidas()[: args.max_equipas]
        if not alvos:
            print("Nenhuma equipa conhecida ainda na tabela historico.")
            print("Para popular do zero, usa --team-id <id> ou corre")
            print("primeiro o resolver_sugestoes_live.py (que adiciona")
            print("equipas automaticamente dos jogos vistos).")
            return

    print(f"\n=== Backfill historico equipas ({len(alvos)} equipas, "
          f"{args.n} jogos cada) ===\n")

    total_adic = 0
    total_eventos = 0
    for i, (tid, nome) in enumerate(alvos, 1):
        print(f"[{i}/{len(alvos)}] {nome} (id={tid})...")
        try:
            event_ids = _ultimos_eventos_equipa(tid, args.n)
        except FlaresolverrIndisponivel as exc:
            print(f"⚠ Flaresolverr OFF: {exc}")
            print("Para continuar: `docker start flaresolverr`")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("team %s events falhou: %s", tid, exc)
            continue
        print(f"    {len(event_ids)} eventos terminados encontrados")
        for eid in event_ids:
            try:
                n_add = historico_equipas.gravar_jogo_terminado(eid)
                total_adic += n_add
                total_eventos += 1
            except FlaresolverrIndisponivel:
                print(f"⚠ Flaresolverr caiu durante event {eid}")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("gravar event %s falhou: %s", eid, exc)
            time.sleep(0.3)
        time.sleep(1.0)  # entre equipas

    print(f"\n✅ Backfill terminado: {total_adic} linhas novas em "
          f"{total_eventos} eventos processados.")


if __name__ == "__main__":
    main()
