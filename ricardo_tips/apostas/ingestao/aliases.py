"""Aliases de nomes de equipas entre fontes de dados.

A Sportmonks e o football-data.co.uk usam variantes diferentes para a
mesma equipa (ex.: "Manchester United" vs "Man United"). Esta tabela
canoniza-os para que a reconciliação na BD funcione sem duplicados.

Nome canónico = o que a Sportmonks usa. Alias = o nome na outra fonte.

Para adicionar uma equipa nova:
    1. Põe o nome canónico (Sportmonks) como chave.
    2. Dentro, uma lista por fonte com as variantes conhecidas.
"""

from __future__ import annotations

FONTE_SPORTMONKS = "sportmonks"
FONTE_FOOTBALL_DATA = "football_data"


# nome_canonico → fonte → [aliases]
ALIASES: dict[str, dict[str, list[str]]] = {
    # Premier League
    "Manchester United": {FONTE_FOOTBALL_DATA: ["Man United", "Man Utd"]},
    "Manchester City":   {FONTE_FOOTBALL_DATA: ["Man City"]},

    # La Liga
    "Atletico Madrid": {FONTE_FOOTBALL_DATA: ["Ath Madrid", "Atl Madrid"]},
    "Real Sociedad":   {FONTE_FOOTBALL_DATA: ["Sociedad"]},
    "Real Betis":      {FONTE_FOOTBALL_DATA: ["Betis"]},

    # Serie A
    "AC Milan": {FONTE_FOOTBALL_DATA: ["Milan"]},
    "AS Roma":  {FONTE_FOOTBALL_DATA: ["Roma"]},

    # Bundesliga
    "Borussia Dortmund":   {FONTE_FOOTBALL_DATA: ["Dortmund"]},
    "Bayer Leverkusen":    {FONTE_FOOTBALL_DATA: ["Leverkusen"]},
    "Eintracht Frankfurt": {FONTE_FOOTBALL_DATA: ["Ein Frankfurt"]},
    "SC Freiburg":         {FONTE_FOOTBALL_DATA: ["Freiburg"]},

    # Ligue 1
    "Paris Saint Germain": {FONTE_FOOTBALL_DATA: ["Paris SG", "PSG"]},

    # Liga Portugal
    "Sporting CP": {FONTE_FOOTBALL_DATA: ["Sp Lisbon", "Sporting"]},
    "SC Braga":    {FONTE_FOOTBALL_DATA: ["Braga"]},
    "Vitoria SC":  {FONTE_FOOTBALL_DATA: ["Guimaraes", "V Guimaraes"]},
}


def nome_canonico_de(alias: str, fonte: str) -> str | None:
    """Dado um alias e a sua fonte, devolve o nome canónico (ou None)."""
    for canonico, por_fonte in ALIASES.items():
        if alias in por_fonte.get(fonte, []):
            return canonico
    return None


def aliases_de(nome_canonico: str, fonte: str) -> list[str]:
    """Variantes conhecidas desse nome canónico nessa fonte."""
    return ALIASES.get(nome_canonico, {}).get(fonte, [])
