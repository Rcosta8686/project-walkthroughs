"""Relatorio CLV semanal (M14) — agregar snapshots pre-match + odds de
fecho e calcular closing line value por mercado/liga/momento.

Correr:
    venv\\Scripts\\python.exe scripts\\mostrar_clv.py
    venv\\Scripts\\python.exe scripts\\mostrar_clv.py --dias 7 --telegram

CLV > 0 significa que apostaste a preco melhor do que o fecho justo.
E o indicador mais rapido para saber se o scanner tem vantagem real
(100-200 apostas chega; lucro precisa de 500+).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from apostas.modelos import clv_tracker  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import OddsSnapshotPrelive  # noqa: E402


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=7,
                        help="Analisar snapshots dos ultimos N dias (default 7)")
    parser.add_argument("--telegram", action="store_true",
                        help="Enviar resumo para Telegram")
    args = parser.parse_args()

    desde = datetime.utcnow() - timedelta(days=args.dias)

    # Agrupa snapshots por (event, mercado, linha, lado)
    with abrir_sessao() as s:
        snaps = s.scalars(
            select(OddsSnapshotPrelive)
            .where(OddsSnapshotPrelive.timestamp >= desde)
            .order_by(OddsSnapshotPrelive.timestamp.asc())
        ).all()
        # Materializa fora da sessao
        rows = [{
            "event_id": sn.sofascore_event_id,
            "mercado": sn.mercado,
            "linha": sn.linha,
            "lado": sn.lado,
            "odd": sn.odd,
            "casa": sn.casa,
            "momento": sn.momento,
            "timestamp": sn.timestamp,
        } for sn in snaps]

    print(f"\n{'=' * 78}")
    print(f"  RELATORIO CLV — ultimos {args.dias} dias")
    print(f"{'=' * 78}\n")

    if not rows:
        print(f"⚠ 0 snapshots nos ultimos {args.dias} dias.")
        print("  Correr pre_live_diario.py para comecar a popular dados.\n")
        return

    print(f"{len(rows)} snapshots totais.\n")

    # Para cada sinal emitido (momento == pre_live_12h ou abertura),
    # procura snapshot "fecho" do mesmo (event, mercado, linha, lado) e calcula CLV
    sinais_emitidos = [r for r in rows if r["momento"] in ("pre_live_12h", "abertura")]
    print(f"{len(sinais_emitidos)} sinais pre-match emitidos.\n")

    por_mercado: dict[str, list[float]] = defaultdict(list)
    por_liga_marker: dict[str, list[float]] = defaultdict(list)  # casa como proxy liga

    n_com_clv = 0
    for r in sinais_emitidos:
        clv_res = clv_tracker.calcular_clv_sinal(
            sofascore_event_id=r["event_id"],
            mercado=r["mercado"], linha=r["linha"], lado=r["lado"],
            odd_apostada=r["odd"],
        )
        if clv_res and clv_res.momento_usado != r["momento"]:
            # Snapshot de fecho (ou posterior) existe
            n_com_clv += 1
            por_mercado[r["mercado"]].append(clv_res.clv_vs_justa)

    if n_com_clv == 0:
        print("⚠ Nenhum sinal tem ainda snapshot de 'fecho' para calcular CLV.")
        print("  Aguarda que os jogos atingam a janela de fecho e volta a correr.\n")
        return

    print(f"{n_com_clv}/{len(sinais_emitidos)} sinais com CLV calculado.\n")

    print(f"{'─' * 78}")
    print(f"  CLV POR MERCADO (vs odd fecho justa)")
    print(f"{'─' * 78}")
    totais_clv = []
    for mercado, clvs in sorted(por_mercado.items()):
        media = _mean(clvs)
        if media is None:
            continue
        totais_clv.extend(clvs)
        pct_pos = sum(1 for c in clvs if c > 0) / len(clvs) * 100
        marker = "✅" if media > 0 else ("⚠" if media > -0.02 else "❌")
        print(f"  {marker}  {mercado:<16} n={len(clvs):4d}  "
              f"CLV {media*100:+6.2f}%  (positivo {pct_pos:.0f}%)")

    print(f"\n{'─' * 78}")
    media_total = _mean(totais_clv)
    if media_total is not None:
        pct_pos_total = sum(1 for c in totais_clv if c > 0) / len(totais_clv) * 100
        veredicto = ("✅ scanner com edge" if media_total > 0.01
                     else "⚠ sem edge clara" if media_total > -0.01
                     else "❌ scanner nao tem edge")
        print(f"  TOTAL: n={len(totais_clv)}  CLV medio {media_total*100:+6.2f}%  "
              f"(positivo {pct_pos_total:.0f}%)")
        print(f"  {veredicto}")
    print(f"{'─' * 78}\n")

    if args.telegram and media_total is not None:
        try:
            from apostas.alertas.telegram_cliente import cliente  # noqa: PLC0415
            msg = (f"📊 *RELATORIO CLV — ultimos {args.dias}d*\n"
                   f"n={len(totais_clv)} · CLV {media_total*100:+.2f}% · "
                   f"positivo {pct_pos_total:.0f}%\n{veredicto}")
            cliente().enviar(msg)
            print("✓ Relatorio enviado para Telegram.")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠ Falha envio Telegram: {exc}")


if __name__ == "__main__":
    main()
