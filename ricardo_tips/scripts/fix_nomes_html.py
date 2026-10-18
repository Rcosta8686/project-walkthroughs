"""Corrige nomes de equipas/ligas na tabela EstatisticaEquipaJogo que
foram gravados com entidades HTML brutas (ex: 'Brighton &amp; Hove Albion').

Correr UMA vez apos pull do fix:
    venv\\Scripts\\python.exe scripts\\fix_nomes_html.py

E seguro — e idempotente. Nao toca em nomes ja limpos.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select  # noqa: E402

from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402


def main() -> None:
    print("\n=== Fix nomes HTML em EstatisticaEquipaJogo ===\n")
    actualizados_equipa = 0
    actualizados_liga = 0
    total = 0

    with abrir_sessao() as s:
        rows = s.scalars(select(EstatisticaEquipaJogo)).all()
        total = len(rows)
        for r in rows:
            if r.nome_equipa and "&" in r.nome_equipa and ";" in r.nome_equipa:
                limpo = html.unescape(r.nome_equipa)
                if limpo != r.nome_equipa:
                    r.nome_equipa = limpo
                    actualizados_equipa += 1
            if r.liga and "&" in r.liga and ";" in r.liga:
                limpo = html.unescape(r.liga)
                if limpo != r.liga:
                    r.liga = limpo
                    actualizados_liga += 1

    print(f"Linhas analisadas: {total}")
    print(f"Nomes de equipa corrigidos: {actualizados_equipa}")
    print(f"Nomes de liga corrigidos:   {actualizados_liga}")
    print("\n✅ Limpeza terminada.")


if __name__ == "__main__":
    main()
