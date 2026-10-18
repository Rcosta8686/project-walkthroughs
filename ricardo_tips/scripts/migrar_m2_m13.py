"""Migracao incremental: adiciona tabelas OddsSnapshotPrelive, EquipaPerfil,
BancaRegisto SEM mexer nos dados existentes.

Correr UMA vez apos pull:
    venv\\Scripts\\python.exe scripts\\migrar_m2_m13.py

E seguro corrê-lo varias vezes: `create_all` so cria o que falta.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from apostas.utils.db import get_engine  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import (  # noqa: E402
    Base,
    BancaRegisto,
    EquipaPerfil,
    OddsSnapshotPrelive,
)

log = get_logger(__name__)


def main() -> None:
    engine = get_engine()
    insp = inspect(engine)

    novas = [
        OddsSnapshotPrelive.__tablename__,
        EquipaPerfil.__tablename__,
        BancaRegisto.__tablename__,
    ]

    print("\n=== Migracao M2+M13: tabelas novas (sem perder dados) ===\n")

    existentes = insp.get_table_names()
    for nome in novas:
        marker = "✓ ja existe" if nome in existentes else "+ cria"
        print(f"  {marker:12s}  {nome}")

    print("\nA aplicar create_all (so cria o que falta)...")
    Base.metadata.create_all(engine)

    # Verifica depois
    insp = inspect(engine)
    criadas = insp.get_table_names()
    print("\nTabelas na BD apos migracao:")
    for nome in sorted(criadas):
        marker = "★" if nome in novas else " "
        print(f"  {marker} {nome}")

    todas_ok = all(n in criadas for n in novas)
    if todas_ok:
        print("\n✅ Migracao OK. Dados antigos intactos.")
    else:
        print("\n⚠ Alguma tabela nao foi criada. Verificar schema.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
