"""Script: correr_backtest.py

Corre o backtest walk-forward sobre os dados históricos e imprime um
relatório em texto no terminal.

Uso:
    python scripts/correr_backtest.py
    python scripts/correr_backtest.py --modelo gap --linha 2.5
    python scripts/correr_backtest.py --modelo poisson --linha 2.5
    python scripts/correr_backtest.py --epoca 2024 --linha 1.5

Correr sempre que:
  - Mudas parâmetros do modelo em config.yaml
  - Adicionas dados mais recentes (depois de atualizar_dados.py)
  - Experimentas uma nova feature
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.backtest import relatorio as fmt  # noqa: E402
from apostas.backtest import walk_forward  # noqa: E402
from apostas.dashboard import gerador as dashboard  # noqa: E402
from apostas.utils.config import load_config  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Backtest walk-forward.")
    parser.add_argument(
        "--modelo",
        choices=walk_forward.MODELOS_DISPONIVEIS,
        default=None,
        help="Modelo a correr. Se omitido, corre AMBOS e compara no dashboard.",
    )
    parser.add_argument(
        "--mercado",
        choices=walk_forward.MERCADOS_DISPONIVEIS,
        default="golos",
        help="Mercado: golos ou cantos (default: golos)",
    )
    parser.add_argument(
        "--sem-dashboard",
        action="store_true",
        help="Não gerar HTML; só imprimir no terminal.",
    )
    parser.add_argument(
        "--epoca",
        default="2024",
        help="Ano de início da época a testar (ex.: 2024 = 2024/25). "
             "Para múltiplas épocas, separar por vírgulas: '2022,2023,2024'.",
    )
    parser.add_argument(
        "--linha",
        type=float,
        default=2.5,
        help="Linha de golos (1.5, 2.5, 3.5)",
    )
    parser.add_argument(
        "--ev-minimo",
        type=float,
        default=cfg["value_betting"]["ev_minimo"],
        help="EV mínimo para considerar uma aposta",
    )
    parser.add_argument(
        "--casa",
        default="Pinnacle_Closing",
        help="Casa de apostas de referência (ex.: Pinnacle_Closing, Avg_Closing, B365). "
             "Pinnacle é o 'sharp book' — benchmark académico de edge real.",
    )
    parser.add_argument(
        "--peso-cantos",
        type=float,
        default=0.5,
        help="Peso dos cantos vs remates no modelo GAP (0..1)",
    )
    parser.add_argument(
        "--metrica",
        choices=("shots_corners", "xg"),
        default="shots_corners",
        help="Métrica do GAP: shots_corners (football-data) ou xg (Understat).",
    )
    parser.add_argument(
        "--rho",
        type=float,
        default=0.1,
        help="Correção Dixon-Coles para resultados baixos (0 = desligado; típico ~0.1)",
    )
    parser.add_argument(
        "--calibracao",
        choices=("nenhuma", "platt", "isotonic"),
        default="nenhuma",
        help="Calibração das probabilidades do modelo. 'platt' (sigmoid 2-param) "
             "ou 'isotonic' (não-paramétrico). Fit nos primeiros %% da época "
             "(ver --frac-cal), aplica aos restantes jogos.",
    )
    parser.add_argument(
        "--frac-cal",
        type=float,
        default=0.4,
        help="Fração dos jogos da época para fitar o calibrador (default 0.4).",
    )
    parser.add_argument(
        "--ligas",
        default=None,
        help="Lista de nomes de ligas separados por vírgula (ex.: "
             "'Premier League,La Liga,Serie A,Ligue 1') para restringir o "
             "backtest. Default: todas as ligas da BD.",
    )
    args = parser.parse_args()

    if args.mercado == "cantos":
        modelos = ["cantos"]  # único modelo para cantos
    elif args.modelo:
        modelos = [args.modelo]
    else:
        modelos = [m for m in walk_forward.MODELOS_DISPONIVEIS if m != "cantos"]

    # Default de linha para cantos é diferente
    linha = args.linha if args.linha != 2.5 or args.mercado == "golos" else 9.5

    relatorios = []
    ligas_filtro = (
        [l.strip() for l in args.ligas.split(",")] if args.ligas else None
    )
    epocas = [int(e.strip()) for e in str(args.epoca).split(",")]
    for modelo in modelos:
        for epoca in epocas:
            rel = walk_forward.correr(
                modelo=modelo,
                mercado=args.mercado,
                epoca_teste=epoca,
                linha=linha,
                ev_minimo=args.ev_minimo,
                casa_de_apostas_ref=args.casa,
                meia_vida_dias=cfg["modelo"]["decay_meia_vida_dias"],
                peso_cantos=args.peso_cantos,
                rho_dixon_coles=args.rho,
                metrica_gap=args.metrica,
                calibracao=args.calibracao,
                frac_calibracao=args.frac_cal,
                ligas_nomes=ligas_filtro,
            )
            print(fmt.formatar(rel))
            relatorios.append(rel)

    if len(relatorios) > 1:
        print("\n" + "═" * 70)
        print("  RESUMO MULTI-ÉPOCAS")
        print("═" * 70)
        total_apostas = sum(r.n_apostas for r in relatorios)
        total_stake = sum(r.stake_total for r in relatorios)
        total_lucro = sum(r.lucro for r in relatorios)
        total_vitorias = sum(r.n_vitorias for r in relatorios)
        roi_agg = total_lucro / total_stake if total_stake else 0.0
        hit_agg = total_vitorias / total_apostas if total_apostas else 0.0
        for r in relatorios:
            print(
                f"  {r.modelo:<8} {r.epoca_teste} | {r.n_apostas:>4} apostas "
                f"| ROI {r.roi * 100:+6.2f}% | hit {r.hit_rate * 100:5.1f}%"
            )
        print(
            f"  {'TOTAL':<8} ------ | {total_apostas:>4} apostas "
            f"| ROI {roi_agg * 100:+6.2f}% | hit {hit_agg * 100:5.1f}%"
        )
        print("═" * 70)

    if not args.sem_dashboard:
        destino = dashboard.gerar(relatorios)
        print(f"\n→ Dashboard: {destino}")
        print("  (abre este ficheiro no browser com duplo-clique)")


if __name__ == "__main__":
    main()
