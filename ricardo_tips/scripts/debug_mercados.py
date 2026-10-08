"""Script: debug_mercados.py

Mostra inventario de odds correntes na BD por mercado + casa, para
diagnosticar se handicap/btts/dupla foram realmente ingeridos.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import OddsCorrentes  # noqa: E402


def main():
    with abrir_sessao() as s:
        print("\n" + "=" * 80)
        print("  INVENTARIO DE ODDS CORRENTES POR MERCADO + CASA")
        print("=" * 80)

        # Por mercado
        rows = s.execute(
            select(
                OddsCorrentes.mercado,
                func.count(OddsCorrentes.id).label("n")
            ).group_by(OddsCorrentes.mercado)
        ).all()
        print("\n  Por mercado:")
        for mercado, n in rows:
            print(f"    {mercado:<15} {n:>6}")

        # Por mercado + casa
        rows = s.execute(
            select(
                OddsCorrentes.mercado,
                OddsCorrentes.casa_de_apostas,
                func.count(OddsCorrentes.id).label("n")
            ).group_by(OddsCorrentes.mercado, OddsCorrentes.casa_de_apostas)
            .order_by(OddsCorrentes.mercado, OddsCorrentes.casa_de_apostas)
        ).all()
        print("\n  Por mercado + casa:")
        print(f"    {'Mercado':<15} {'Casa':<16} {'N odds':>8}")
        for mercado, casa, n in rows:
            print(f"    {mercado:<15} {casa:<16} {n:>8}")

        # Linhas distintas de handicap
        linhas = s.scalars(
            select(OddsCorrentes.linha).where(
                OddsCorrentes.mercado == "handicap"
            ).distinct().order_by(OddsCorrentes.linha)
        ).all()
        print(f"\n  Linhas distintas de handicap: {linhas}")

        # Primeiros 5 handicaps da Pinnacle
        print("\n  Primeiros 5 handicaps Pinnacle (para ver estrutura):")
        rows = s.execute(
            select(
                OddsCorrentes.jogo_id,
                OddsCorrentes.linha,
                OddsCorrentes.lado,
                OddsCorrentes.odd,
            ).where(
                OddsCorrentes.mercado == "handicap",
                OddsCorrentes.casa_de_apostas == "pinnacle",
            ).limit(10)
        ).all()
        for jogo_id, linha, lado, odd in rows:
            print(f"    jogo={jogo_id} linha={linha} lado={lado} odd={odd}")

        print("=" * 80)


if __name__ == "__main__":
    main()
