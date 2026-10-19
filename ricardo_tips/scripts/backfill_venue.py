"""Backfill venue nas linhas antigas de EstatisticaEquipaJogo.

Fase 8: a coluna `venue` foi adicionada depois do backfill original.
Este script apanha event_ids distintos com venue=NULL e vai ao
Sofascore buscar `event.venue.name`, actualizando ambas as linhas
(casa + fora) do jogo.

Uso:
    python scripts/backfill_venue.py                  # 500 jogos por default
    python scripts/backfill_venue.py --limite 2000
    python scripts/backfill_venue.py --dias 180       # so jogos recentes
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select, update  # noqa: E402

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.ingestao.sofascore_live import _get_json  # noqa: E402
from apostas.modelos.historico_equipas import _unescape  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402

log = get_logger(__name__)


def _venue_do_event(ev: dict) -> str | None:
    v = ev.get("venue") or {}
    if not isinstance(v, dict):
        return None
    nome = v.get("name")
    if not nome and isinstance(v.get("stadium"), dict):
        nome = v["stadium"].get("name")
    return _unescape(nome) if nome else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=500,
                        help="Max event_ids a processar (default 500).")
    parser.add_argument("--dias", type=int, default=None,
                        help="So jogos nos ultimos N dias.")
    args = parser.parse_args()

    with abrir_sessao() as s:
        q = (
            select(EstatisticaEquipaJogo.sofascore_event_id)
            .where(EstatisticaEquipaJogo.venue.is_(None))
            .distinct()
            .order_by(EstatisticaEquipaJogo.data_jogo.desc())
            .limit(args.limite)
        )
        if args.dias:
            limiar = datetime.utcnow() - timedelta(days=args.dias)
            q = q.where(EstatisticaEquipaJogo.data_jogo >= limiar)
        event_ids = [r for r in s.scalars(q).all()]

    print(f"\n╔ Backfill venue — {len(event_ids)} eventos ╗\n")
    actualizados = 0
    processados = 0
    for eid in event_ids:
        try:
            ej = _get_json(f"/event/{eid}")
        except FlaresolverrIndisponivel:
            print("⚠ Flaresolverr OFF — para e re-corre.")
            break
        if ej is None or "event" not in ej:
            processados += 1
            continue
        venue = _venue_do_event(ej["event"])
        if venue:
            with abrir_sessao() as s:
                res = s.execute(
                    update(EstatisticaEquipaJogo)
                    .where(EstatisticaEquipaJogo.sofascore_event_id == eid)
                    .values(venue=venue)
                )
                if res.rowcount:
                    actualizados += res.rowcount
        processados += 1
        if processados % 25 == 0:
            print(f"  [{processados}/{len(event_ids)}] +{actualizados} linhas actualizadas")
        time.sleep(0.3)

    print(f"\n╔ TOTAL: {processados} eventos processados · "
          f"{actualizados} linhas com venue ╗\n")


if __name__ == "__main__":
    main()
