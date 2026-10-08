"""Script: listar_equipas.py

Imprime:
  1. Equipas por liga na BD (nome canónico)
  2. Equipas da Odds API cujo nome (depois do _ALIAS_ODDS_API) não casa
     com nenhuma equipa na BD — e sugere o mais parecido.

Serve para completar `_ALIAS_ODDS_API` em `apostas/ingestao/odds_api.py`.

Uso:
    python scripts/listar_equipas.py
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from apostas.ingestao import odds_api as oa
from apostas.utils.db import abrir_sessao
from apostas.utils.schema import Equipa, Liga


def main() -> None:
    print("=" * 78)
    print("  EQUIPAS NA BD POR LIGA")
    print("=" * 78)

    with abrir_sessao() as s:
        ligas = s.scalars(select(Liga).order_by(Liga.nome)).all()
        for liga in ligas:
            equipas = s.scalars(
                select(Equipa).where(Equipa.liga_id == liga.id).order_by(Equipa.nome)
            ).all()
            print(f"\n── {liga.nome} ({len(equipas)} equipas) ──")
            nomes = [e.nome for e in equipas]
            # 4 colunas
            cols = 4
            for i in range(0, len(nomes), cols):
                linha = nomes[i:i + cols]
                print("  " + "".join(f"{n:<24}" for n in linha))

    print("\n" + "=" * 78)
    print("  EQUIPAS DA ODDS API QUE NÃO CASAM")
    print("=" * 78)

    for nome_liga, sport_key in oa.LIGAS_ODDS_API.items():
        try:
            jogos = oa._puxar_odds_sport(sport_key, "pinnacle", "eu", "producao",
                                         oa.ResultadoOddsApi())
        except Exception as exc:
            print(f"\n{nome_liga}: ERRO — {exc}")
            continue

        with abrir_sessao() as s:
            liga = s.scalar(select(Liga).where(Liga.nome == nome_liga))
            if liga is None:
                continue
            nomes_bd = [e.nome for e in s.scalars(
                select(Equipa).where(Equipa.liga_id == liga.id)
            ).all()]

        falhas: list[tuple[str, str, list[str]]] = []
        nomes_vistos = set()
        for j in jogos:
            for raw_name in (j.get("home_team", ""), j.get("away_team", "")):
                if not raw_name or raw_name in nomes_vistos:
                    continue
                nomes_vistos.add(raw_name)
                canon = oa._canoniza(raw_name)
                if canon in nomes_bd:
                    continue
                sugeridos = difflib.get_close_matches(canon, nomes_bd, n=3, cutoff=0.4)
                falhas.append((raw_name, canon, sugeridos))

        if not falhas:
            print(f"\n✓ {nome_liga}: todas as equipas Odds API casam.")
            continue
        print(f"\n── {nome_liga} ({len(falhas)} sem match) ──")
        for raw, canon, sug in falhas:
            sug_str = f"  ↝ parecidos: {sug}" if sug else "  ↝ (nenhuma similar na BD)"
            print(f"  Odds API: {raw!r}")
            print(f"    depois do alias:  {canon!r}")
            print(sug_str)

    print("\n" + "=" * 78)
    print("  COMO CORRIGIR")
    print("=" * 78)
    print("""
Para cada equipa sem match, edita `apostas/ingestao/odds_api.py` e adiciona
uma entrada em _ALIAS_ODDS_API:

    "Nome como vem da Odds API": "Nome como está na BD",

Se a equipa simplesmente não existe na BD (ex. recém-promovida), football-data
ainda não tem dela. Nesse caso, não há aliase a fazer — ignora.
""")


if __name__ == "__main__":
    main()
