"""Migracao Fase 8 (venue) + Fase 10 (ausencias) + Nivel 3 (stats
avancadas + arbitro). Idempotente.

Adiciona colunas em `estatistica_equipa_jogo`:
  venue, arbitro, passes_longos_total, passes_longos_pct,
  duels_won_pct, interceptions, tackles_total, offsides,
  pass_accuracy_final_third

Nao altera dados antigos — ficam NULL ate backfill correr. SQLite
nao suporta ADD COLUMN com index implicitamente, portanto adicionamos
apenas a coluna + CREATE INDEX separado.
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
    print("\n=== Migracao Fase 8 + 10 + Nivel 3: colunas em estatistica_equipa_jogo ===\n")

    # (nome_coluna, ddl, index_opcional)
    adicoes = [
        ("venue",                     "VARCHAR(100)", True),
        ("arbitro",                   "VARCHAR(100)", True),
        ("passes_longos_total",       "INTEGER",      False),
        ("passes_longos_pct",         "FLOAT",        False),
        ("duels_won_pct",             "FLOAT",        False),
        ("interceptions",             "INTEGER",      False),
        ("tackles_total",             "INTEGER",      False),
        ("offsides",                  "INTEGER",      False),
        ("pass_accuracy_final_third", "FLOAT",        False),
    ]

    with engine.begin() as conn:
        for nome, ddl, criar_index in adicoes:
            if nome in cols:
                print(f"  ✓ {nome} (ja existe)")
                continue
            print(f"  + {nome}  (adiciona)")
            conn.execute(text(
                f"ALTER TABLE estatistica_equipa_jogo ADD COLUMN {nome} {ddl}"
            ))
            if criar_index:
                try:
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_estatistica_equipa_jogo_{nome} "
                        f"ON estatistica_equipa_jogo ({nome})"
                    ))
                except Exception as exc:  # noqa: BLE001
                    print(f"    (nao criou index, nao bloqueante: {exc})")

    print("\n✅ Migracao OK. Backfill opcional:")
    print("   venv\\Scripts\\python.exe scripts\\backfill_venue.py --limite 500")
    print("   venv\\Scripts\\python.exe scripts\\backfill_stats_nivel3.py --limite 500\n")


if __name__ == "__main__":
    main()
