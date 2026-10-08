"""Script: correr_backtest_softs.py

Backtest da estratégia Pinnacle-fair-vs-soft-books em dados históricos.

Uso:
    python scripts/correr_backtest_softs.py
    python scripts/correr_backtest_softs.py --epoca 2022,2023,2024
    python scripts/correr_backtest_softs.py --ligas "Premier League,La Liga"
    python scripts/correr_backtest_softs.py --softs "B365,WH" --ev-minimo 0.05
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.backtest import backtest_softs  # noqa: E402
from apostas.utils.config import load_config  # noqa: E402


def _formatar(rel) -> str:
    linhas = ["═" * 70]
    linhas.append(f"  BACKTEST PINNACLE→SOFTS — época {rel.epoca_teste}")
    linhas.append("═" * 70)
    linhas.append(f"  Mercado:        {rel.mercado}")
    linhas.append(f"  Fair:           {rel.casa_fair}")
    linhas.append(f"  Soft books:     {', '.join(rel.casas_soft)}")
    if rel.mercado == "golos":
        linhas.append(f"  Linha:          Over/Under {rel.linha} golos")
    linhas.append(f"  EV mínimo:      {rel.ev_minimo * 100:.1f}%")
    linhas.append("")
    if rel.n_apostas == 0:
        linhas.append("  ⚠ Zero apostas — nenhuma divergência passou o limiar.")
        linhas.append("═" * 70)
        return "\n".join(linhas)
    linhas.append(f"  Apostas:        {rel.n_apostas}")
    linhas.append(
        f"  Ganhas:         {rel.n_vitorias} ({rel.hit_rate * 100:.1f}%)"
    )
    linhas.append(f"  Stake total:    {rel.stake_total:.2f}")
    linhas.append(f"  Lucro:          {rel.lucro:+.2f}")
    linhas.append(f"  ROI:            {rel.roi * 100:+.2f}%")
    linhas.append("")
    # Por soft book
    linhas.append("  ── Por soft book ──────────────────────────────────")
    por_casa: dict[str, list] = {}
    for a in rel.apostas:
        por_casa.setdefault(a.casa_soft, []).append(a)
    for casa, aps in sorted(por_casa.items()):
        n = len(aps)
        vit = sum(1 for a in aps if a.resultado == "ganho")
        lucro = sum(a.lucro for a in aps)
        stake = sum(a.stake for a in aps)
        roi = lucro / stake if stake else 0
        linhas.append(
            f"  {casa:<10} | {n:>4} apostas | hit {vit / n * 100:5.1f}% "
            f"| ROI {roi * 100:+6.2f}% | lucro {lucro:+.2f}"
        )
    # Por lado
    linhas.append("  ── Por lado ───────────────────────────────────────")
    for lado in ("over", "under", "casa", "empate", "fora"):
        aps = [a for a in rel.apostas if a.lado == lado]
        if not aps:
            continue
        n = len(aps)
        vit = sum(1 for a in aps if a.resultado == "ganho")
        lucro = sum(a.lucro for a in aps)
        stake = sum(a.stake for a in aps)
        roi = lucro / stake if stake else 0
        linhas.append(
            f"  {lado:<10} | {n:>4} apostas | hit {vit / n * 100:5.1f}% "
            f"| ROI {roi * 100:+6.2f}% | lucro {lucro:+.2f}"
        )
    linhas.append("═" * 70)
    return "\n".join(linhas)


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Backtest Pinnacle→softs.")
    parser.add_argument(
        "--epoca", default="2024",
        help="Época(s) a testar, separadas por vírgula (ex: '2022,2023,2024').",
    )
    parser.add_argument("--linha", type=float, default=2.5,
                        help="Linha Over/Under (só usado para --mercado golos)")
    parser.add_argument(
        "--mercado", choices=("golos", "1x2"), default="golos",
        help="'golos' (Over/Under) ou '1x2' (resultado final). "
             "Para 1x2, usar `--softs B365,BW` (as casas 1x2 do CSV).",
    )
    parser.add_argument(
        "--ev-minimo", type=float,
        default=cfg["value_betting"]["ev_minimo"],
        help="EV mínimo para considerar aposta (default do config).",
    )
    parser.add_argument(
        "--softs", default="B365",
        help="Soft books (nomes football-data) separados por vírgula. "
             "Para Over/Under 2.5, só B365 existe nos CSVs (as outras softs "
             "— WH, BW, VC — só têm odds 1X2).",
    )
    parser.add_argument(
        "--fair", default="Pinnacle_Closing",
        choices=("Pinnacle", "Pinnacle_Closing"),
        help="Casa a usar como fair-price. 'Pinnacle' (opening) captura o "
             "line move: se Pinnacle fechou mais alto, mas B365 ficou nas "
             "odds iniciais, há edge. 'Pinnacle_Closing' (default) compara "
             "closing-vs-closing — mais conservador.",
    )
    parser.add_argument(
        "--ligas", default=None,
        help="Ligas (nomes) separadas por vírgula.",
    )
    args = parser.parse_args()

    epocas = [int(e.strip()) for e in args.epoca.split(",")]
    casas_soft = tuple(c.strip() for c in args.softs.split(","))
    ligas_filtro = (
        [l.strip() for l in args.ligas.split(",")] if args.ligas else None
    )

    relatorios = []
    for epoca in epocas:
        rel = backtest_softs.correr(
            epoca_teste=epoca,
            linha=args.linha,
            ev_minimo=args.ev_minimo,
            casa_fair=args.fair,
            casas_soft=casas_soft,
            ligas_nomes=ligas_filtro,
            mercado=args.mercado,
        )
        print(_formatar(rel))
        relatorios.append(rel)

    if len(relatorios) > 1:
        print("\n" + "═" * 70)
        print("  RESUMO MULTI-ÉPOCAS")
        print("═" * 70)
        tot_a = sum(r.n_apostas for r in relatorios)
        tot_s = sum(r.stake_total for r in relatorios)
        tot_l = sum(r.lucro for r in relatorios)
        tot_v = sum(r.n_vitorias for r in relatorios)
        for r in relatorios:
            print(
                f"  {r.epoca_teste} | {r.n_apostas:>4} apostas "
                f"| ROI {r.roi * 100:+6.2f}% | hit {r.hit_rate * 100:5.1f}%"
            )
        print(
            f"  TOTAL| {tot_a:>4} apostas "
            f"| ROI {(tot_l / tot_s if tot_s else 0) * 100:+6.2f}% "
            f"| hit {(tot_v / tot_a if tot_a else 0) * 100:5.1f}%"
        )
        print("═" * 70)


if __name__ == "__main__":
    main()
