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
        type=int,
        default=2024,
        help="Ano de início da época a testar (ex.: 2024 = 2024/25)",
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
        "--rho",
        type=float,
        default=0.1,
        help="Correção Dixon-Coles para resultados baixos (0 = desligado; típico ~0.1)",
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
    for modelo in modelos:
        rel = walk_forward.correr(
            modelo=modelo,
            mercado=args.mercado,
            epoca_teste=args.epoca,
            linha=linha,
            ev_minimo=args.ev_minimo,
            casa_de_apostas_ref=args.casa,
            meia_vida_dias=cfg["modelo"]["decay_meia_vida_dias"],
            peso_cantos=args.peso_cantos,
            rho_dixon_coles=args.rho,
        )
        print(fmt.formatar(rel))
        relatorios.append(rel)

    if not args.sem_dashboard:
        destino = dashboard.gerar(relatorios)
        print(f"\n→ Dashboard: {destino}")
        print("  (abre este ficheiro no browser com duplo-clique)")


if __name__ == "__main__":
    main()
