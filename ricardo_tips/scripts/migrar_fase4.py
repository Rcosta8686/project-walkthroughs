"""Migracao Fase 4: cria FalhaAlerta (M20) + Hipotese (M21). Idempotente."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import inspect  # noqa: E402

from apostas.utils.db import get_engine  # noqa: E402
from apostas.utils.schema import Base, FalhaAlerta, Hipotese  # noqa: E402


def main() -> None:
    engine = get_engine()
    insp = inspect(engine)
    nomes = (FalhaAlerta.__tablename__, Hipotese.__tablename__)
    print("\n=== Migracao Fase 4: FalhaAlerta + Hipotese ===\n")
    existentes = insp.get_table_names()
    for nome in nomes:
        if nome in existentes:
            print(f"  ✓ {nome}  (ja existe)")
        else:
            print(f"  + {nome}  (cria)")
    Base.metadata.create_all(engine)
    print("\n✅ Migracao OK (dados antigos intactos).")


if __name__ == "__main__":
    main()
