"""Migracao incremental: cria tabelas em falta na BD local.

Usa Base.metadata.create_all() que so cria tabelas que ainda nao existem;
nao mexe em tabelas ja criadas (dados existentes preservados). Script
seguro para correr vezes sem conta.

Uso:
    python scripts/migrar_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from apostas.utils.db import engine  # noqa: E402
from apostas.utils.schema import Base  # noqa: E402


def main() -> None:
    insp = inspect(engine)
    antes = set(insp.get_table_names())
    print(f"Tabelas antes: {len(antes)}")

    Base.metadata.create_all(engine)

    insp = inspect(engine)
    depois = set(insp.get_table_names())
    novas = depois - antes
    print(f"Tabelas depois: {len(depois)}")
    if novas:
        print(f"✓ Tabelas criadas:")
        for t in sorted(novas):
            print(f"  - {t}")
    else:
        print("✓ Nenhuma tabela nova (ja estavam todas).")


if __name__ == "__main__":
    main()
