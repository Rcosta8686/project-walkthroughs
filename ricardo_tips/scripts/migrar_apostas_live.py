"""Migracao: cria tabela ApostaRealLive. Idempotente.

Executar uma vez depois de puxar a Fase 2b. A `Base.metadata.create_all`
nao mexe em tabelas que ja existem — so adiciona a nova.
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

from apostas.utils.db import get_engine  # noqa: E402
from apostas.utils.schema import ApostaRealLive, Base  # noqa: E402


def main() -> None:
    engine = get_engine()
    insp = inspect(engine)
    nome = ApostaRealLive.__tablename__
    print(f"\n=== Migracao: criar '{nome}' se nao existe ===\n")
    existentes = insp.get_table_names()
    if nome in existentes:
        print(f"  ✓ ja existe")
    else:
        print(f"  + cria")
    Base.metadata.create_all(engine)
    print("\n✅ Migracao OK (dados antigos intactos).")


if __name__ == "__main__":
    main()
