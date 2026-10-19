"""Paises banidos para apostas em casas suicas/europeias.

Jogos destes paises podem aparecer no Sofascore (que ignora
jurisdicoes), mas a bet365, Pinnacle e casas suicas tiram-nos do
mercado. Logo nao devem enfurnar o Telegram.

Alem do `pais` (que vem de `tournament.category.name` do Sofascore,
usualmente "Russia", "Belarus"...), tambem apanhamos pelo nome das
equipas, porque:
  - Sofascore encurta "Russian Premier League" para "Premier League"
    no `tournament.name` → `estado.liga` fica so "Premier League".
  - Alguns jogos chegam com `category.name` em branco.
"""
from __future__ import annotations


# Nomes de pais (case-insensitive) que nao devem gerar alertas.
PAISES_BANIDOS = frozenset({
    "russia",
    "belarus",
    "iran",
    "north korea",
    "myanmar",
})

# Palavras-chave em nomes de equipas que indicam clubes russos
# (ultima linha de defesa quando `pais` esta em branco).
# Clubes principais da Russian Premier League e FNL.
EQUIPAS_PT_RUSSIA = frozenset({
    "moscow",
    "zenit",
    "spartak",
    "cska",
    "lokomotiv",
    "dynamo moscow",
    "orenburg",
    "krasnodar",
    "rostov",
    "krylya",
    "sochi",
    "akhmat",
    "ural",
    "khimki",
    "fakel",
    "nizhny",
    "rodina",
    "baltika",
    "pari nn",
    "ufa",
    "rotor",
})


def jogo_banido(
    pais: str | None,
    liga: str | None,
    home: str | None,
    away: str | None,
) -> tuple[bool, str]:
    """True + razao textual se o jogo deve ser bloqueado.

    Verifica em cascata:
      1. `pais` em `PAISES_BANIDOS`.
      2. Nomes das equipas contem keywords de clubes russos.

    Deixa espaco para alargar com outras heuristicas (Belarus, Iran)
    se o Sofascore voltar a abreviar.
    """
    p = (pais or "").strip().lower()
    if p and any(bp in p for bp in PAISES_BANIDOS):
        return True, f"pais {pais}"

    h = (home or "").lower()
    a = (away or "").lower()
    for kw in EQUIPAS_PT_RUSSIA:
        if kw in h or kw in a:
            return True, f"equipa russa ({kw})"

    return False, ""
