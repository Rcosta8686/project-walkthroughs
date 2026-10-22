"""Backfill Nivel 3: arbitro + stats avancadas (passes longos, duels,
interceptions, tackles, offsides, pass_accuracy_final_third).

Varre event_ids com `arbitro=NULL` e re-corre `/event/{id}` + /statistics
do Sofascore para preencher nas duas linhas (casa + fora).

Uso:
  python scripts/backfill_stats_nivel3.py                # 500 por default
  python scripts/backfill_stats_nivel3.py --limite 2000
  python scripts/backfill_stats_nivel3.py --dias 180
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apostas.utils.tempo import agora_utc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select, update  # noqa: E402

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.ingestao.sofascore_live import _get_json  # noqa: E402
from apostas.modelos.historico_equipas import (  # noqa: E402
    _extrair_stats_equipa, _unescape,
)
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402

log = get_logger(__name__)


def _dict_para_update(stats, era_casa: bool) -> dict:
    """Converte _StatsEquipa num dict para o `update()`."""
    return {
        "passes_longos_total": stats.passes_longos_total,
        "passes_longos_pct": stats.passes_longos_pct,
        "duels_won_pct": stats.duels_won_pct,
        "interceptions": stats.interceptions,
        "tackles_total": stats.tackles_total,
        "offsides": stats.offsides,
        "pass_accuracy_final_third": stats.pass_accuracy_final_third,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=500)
    parser.add_argument("--dias", type=int, default=None)
    args = parser.parse_args()

    with abrir_sessao() as s:
        q = (
            select(EstatisticaEquipaJogo.sofascore_event_id)
            .where(EstatisticaEquipaJogo.arbitro.is_(None))
            .distinct()
            .order_by(EstatisticaEquipaJogo.data_jogo.desc())
            .limit(args.limite)
        )
        if args.dias:
            limiar = agora_utc() - timedelta(days=args.dias)
            q = q.where(EstatisticaEquipaJogo.data_jogo >= limiar)
        event_ids = list(s.scalars(q).all())

    print(f"\n╔ Backfill Nivel 3 — {len(event_ids)} eventos ╗\n")
    processados = 0
    actualizados = 0
    for eid in event_ids:
        try:
            ej = _get_json(f"/event/{eid}")
            stats_json = _get_json(f"/event/{eid}/statistics") or {}
        except FlaresolverrIndisponivel:
            print("⚠ Flaresolverr OFF — para e re-corre.")
            break
        if ej is None or "event" not in ej:
            processados += 1
            continue
        ev = ej["event"]
        arbitro_obj = ev.get("referee")
        arbitro = None
        if isinstance(arbitro_obj, dict):
            raw = arbitro_obj.get("name")
            arbitro = _unescape(raw) if raw else None

        stats_h = _extrair_stats_equipa(stats_json, "home")
        stats_a = _extrair_stats_equipa(stats_json, "away")
        hid = (ev.get("homeTeam") or {}).get("id")
        aid = (ev.get("awayTeam") or {}).get("id")

        with abrir_sessao() as s:
            for team_id, stats_eq, era_casa in (
                (hid, stats_h, True), (aid, stats_a, False),
            ):
                if not team_id:
                    continue
                values = _dict_para_update(stats_eq, era_casa)
                if arbitro:
                    values["arbitro"] = arbitro
                res = s.execute(
                    update(EstatisticaEquipaJogo)
                    .where(
                        EstatisticaEquipaJogo.sofascore_event_id == eid,
                        EstatisticaEquipaJogo.sofascore_team_id == team_id,
                    )
                    .values(**values)
                )
                if res.rowcount:
                    actualizados += res.rowcount
        processados += 1
        if processados % 25 == 0:
            print(f"  [{processados}/{len(event_ids)}] +{actualizados} linhas")
        time.sleep(0.3)

    print(f"\n╔ TOTAL: {processados} eventos · {actualizados} linhas ╗\n")


if __name__ == "__main__":
    main()
