"""Migracao: adiciona tabelas EstatisticaEquipaJogo + ProjecaoLive SEM
apagar dados existentes.

Correr UMA vez apos pull:
    venv\\Scripts\\python.exe scripts\\migrar_historico_equipas.py

E seguro corre-lo varias vezes: `create_all` so cria o que falta.
"""
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

from apostas.utils.db import criar_engine  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import (  # noqa: E402
    Base,
    EstatisticaEquipaJogo,
    ProjecaoLive,
)

log = get_logger(__name__)


def main() -> None:
    engine = criar_engine()
    insp = inspect(engine)

    novas = [
        EstatisticaEquipaJogo.__tablename__,
        ProjecaoLive.__tablename__,
    ]

    print("\n=== Migracao historico equipas + projecoes ===\n")

    existentes = insp.get_table_names()
    for nome in novas:
        marker = "✓ ja existe" if nome in existentes else "+ cria"
        print(f"  {marker:12s}  {nome}")

    print("\nA aplicar create_all (so cria o que falta)...")
    Base.metadata.create_all(engine)

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
