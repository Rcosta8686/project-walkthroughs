"""Migracao Fase 8 (venue) + Fase 10 (ausencias). Idempotente.

Adiciona a coluna `venue` a `estatistica_equipa_jogo` se nao existe.
Nao altera dados antigos — fica NULL ate o backfill correr. SQLite
nao suporta ADD COLUMN com index implicitamente, portanto adicionamos
apenas a coluna; o index da tabela ja esta na definicao do schema.
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

from sqlalchemy import inspect, text  # noqa: E402

from apostas.utils.db import get_engine  # noqa: E402


def main() -> None:
    engine = get_engine()
    insp = inspect(engine)

    cols = {c["name"] for c in insp.get_columns("estatistica_equipa_jogo")}
    print("\n=== Migracao Fase 8 + 10: colunas em estatistica_equipa_jogo ===\n")
    with engine.begin() as conn:
        if "venue" in cols:
            print("  ✓ venue (ja existe)")
        else:
            print("  + venue  (adiciona)")
            conn.execute(text(
                "ALTER TABLE estatistica_equipa_jogo "
                "ADD COLUMN venue VARCHAR(100)"
            ))
            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_estatistica_equipa_jogo_venue "
                    "ON estatistica_equipa_jogo (venue)"
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"    (nao criou index, nao bloqueante: {exc})")
    print("\n✅ Migracao OK. Backfill de venues antigos:")
    print("   venv\\Scripts\\python.exe scripts\\backfill_venue.py --limite 500\n")


if __name__ == "__main__":
    main()
