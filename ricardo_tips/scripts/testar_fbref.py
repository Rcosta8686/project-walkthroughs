"""Script: testar_fbref.py

Teste rápido do scraper FBref:
  1. Puxa schedule da Premier League 2024/25
  2. Mostra xG dos primeiros 5 jogos com dados completos

Uso:
    python scripts/testar_fbref.py
    python scripts/testar_fbref.py --liga "La Liga" --ano 2024
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import fbref  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--liga", default="Premier League")
    parser.add_argument("--ano", type=int, default=2024,
                        help="Ano em que a temporada começa (ex: 2024 = 2024/25)")
    args = parser.parse_args()

    print(f"\n{'=' * 78}")
    print(f"  TESTE FBref — {args.liga} {args.ano}/{args.ano + 1}")
    print(f"{'=' * 78}")

    df = fbref.obter_schedule(args.liga, args.ano)
    if df is None:
        print("  AVISO: sem dados devolvidos. Causas possiveis:")
        print("    - FBref 403/429 (rate limit)")
        print("    - Liga/temporada sem jogos (ex: temporada futura)")
        print("    - Mudanca na estrutura do site")
        return

    print(f"  Tabela: {len(df)} linhas")
    print(f"  Colunas: {list(df.columns)[:10]}...")

    jogos = fbref.extrair_xg_por_jogo(df)
    jogos_com_xg = [j for j in jogos if j["xg_home"] is not None]
    print(f"\n  Jogos extraidos: {len(jogos)} (dos quais {len(jogos_com_xg)} com xG)")

    if jogos_com_xg:
        print(f"\n  Primeiros 5 jogos com xG:")
        print(f"  {'Date':<12} {'Home':<22} {'xG':>5} - {'xG':>5}  {'Away':<22}")
        for j in jogos_com_xg[:5]:
            print(
                f"  {str(j['date']):<12} {j['home']:<22} "
                f"{j['xg_home']:>5.2f} - {j['xg_away']:>5.2f}  {j['away']:<22}"
            )

    print(f"\n{'=' * 78}")
    print(f"  OK. Cache em dados/raw/fbref/")
    print(f"{'=' * 78}\n")


if __name__ == "__main__":
    main()
