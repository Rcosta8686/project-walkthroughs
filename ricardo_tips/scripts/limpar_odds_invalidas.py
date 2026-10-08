"""Script: limpar_odds_invalidas.py

Remove linhas de OddsFecho e OddsCorrentes com odd <= 1.0 (gravadas por
engano a partir de valores em branco nos CSVs football-data).

Correr uma vez após o fix em football_data_uk.py._upsert_odd.

Uso:
    python scripts/limpar_odds_invalidas.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select

from apostas.utils.db import abrir_sessao
from apostas.utils.schema import OddsCorrentes, OddsFecho


def main() -> None:
    with abrir_sessao() as s:
        total_fecho = s.scalar(select(func.count(OddsFecho.id)).where(OddsFecho.odd <= 1.0))
        total_corr = s.scalar(select(func.count(OddsCorrentes.id)).where(OddsCorrentes.odd <= 1.0))

        print(f"Odds de fecho inválidas (odd <= 1.0): {total_fecho}")
        print(f"Odds correntes inválidas (odd <= 1.0): {total_corr}")

        if total_fecho == 0 and total_corr == 0:
            print("Nada a limpar.")
            return

        s.execute(delete(OddsFecho).where(OddsFecho.odd <= 1.0))
        s.execute(delete(OddsCorrentes).where(OddsCorrentes.odd <= 1.0))

    print(f"✓ Removidas {total_fecho + total_corr} linhas.")


if __name__ == "__main__":
    main()
