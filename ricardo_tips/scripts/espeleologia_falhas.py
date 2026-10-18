"""M20 — Espeleologia: ingere falhas do CSV live e imprime padroes.

Uso:
    python scripts/espeleologia_falhas.py                  # ingere + imprime
    python scripts/espeleologia_falhas.py --min 5          # min 5 ocorrencias
    python scripts/espeleologia_falhas.py --classificar 42 linha_pouco_fiavel
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.modelos import espeleologia  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=int, default=3,
                        help="Min ocorrencias para mostrar padrao.")
    parser.add_argument("--classificar", nargs=2, metavar=("ALERT_ID", "CATEGORIA"),
                        help="Marca falha ALERT_ID com CATEGORIA (ex. linha_pouco_fiavel).")
    parser.add_argument("--nota", default="",
                        help="Nota livre (opcional, com --classificar).")
    args = parser.parse_args()

    if args.classificar:
        aid = int(args.classificar[0])
        cat = args.classificar[1]
        ok = espeleologia.classificar_falha(aid, cat, args.nota)
        print(f"{'OK' if ok else 'FALHOU'}: alert_id={aid} categoria={cat}")
        return

    csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    n = espeleologia.ingerir_falhas_do_csv(csv_path)
    if n:
        print(f"→ {n} falhas novas ingeridas.")

    padroes = espeleologia.agregar_padroes(min_ocorrencias=args.min)
    print()
    print(espeleologia.formatar_relatorio_padroes(padroes))


if __name__ == "__main__":
    main()
