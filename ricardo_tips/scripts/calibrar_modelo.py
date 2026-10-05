"""Script: calibrar_modelo.py

Grid-search sobre o peso dos cantos e rho de Dixon-Coles para encontrar
a combinação com melhor ROI na época indicada. Útil antes de passar a
dinheiro real.

Uso:
    python scripts/calibrar_modelo.py
    python scripts/calibrar_modelo.py --epoca 2024 --linha 2.5
    python scripts/calibrar_modelo.py --pesos 0.3,0.5,0.7 --rhos 0.1,0.15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.modelos import cross_val  # noqa: E402
from apostas.utils.config import load_config  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def _lista_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoca", type=int, default=2024)
    parser.add_argument("--linha", type=float, default=2.5)
    parser.add_argument("--ev-minimo", type=float, default=cfg["value_betting"]["ev_minimo"])
    parser.add_argument("--pesos", type=_lista_floats,
                        default=[0.1, 0.3, 0.5, 0.7, 1.0],
                        help="Lista de peso_cantos separados por vírgula")
    parser.add_argument("--rhos", type=_lista_floats,
                        default=[0.0, 0.05, 0.10, 0.15],
                        help="Lista de rho Dixon-Coles separados por vírgula")
    args = parser.parse_args()

    log.info(
        "Grid-search: %d combos (%d pesos × %d rhos)",
        len(args.pesos) * len(args.rhos), len(args.pesos), len(args.rhos),
    )

    resultados = cross_val.grid_search(
        epoca_teste=args.epoca,
        pesos_cantos=args.pesos,
        rhos=args.rhos,
        linha=args.linha,
        ev_minimo=args.ev_minimo,
        meia_vida_dias=cfg["modelo"]["decay_meia_vida_dias"],
    )
    print(cross_val.formatar_tabela(resultados))


if __name__ == "__main__":
    main()
