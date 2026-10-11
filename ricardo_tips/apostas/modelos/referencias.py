"""Tabelas de referencia para refinar o modelo preditivo live.

Dois dicionarios + um helper:

  ARBITROS: {nome_normalizado: cards_por_jogo_historico}
    Fonte: media europeia + ajustes conhecidos de arbitros notoriamente
    severos ou brandos. Media de liga ~4.3 cartoes/jogo (soma amarelos
    + vermelhos * 1 de cada equipa). Valores extremos:
      - severos (5.5+): Mateu Lahoz, Tim Robinson, Michael Oliver.
      - brandos (3.0-): Danny Makkelie, Clement Turpin em jogos low-stakes.

  DERBIES: set de tuplos (equipaA, equipaB) sem ordenar — ordem casa/fora
    nao importa. Nomes como estao na DB (ex: "Benfica", "Sp Lisbon").
    Rivalidades conhecidas das Big-5 + LigaPT. Jogos entre estas equipas
    tipicamente geram ~15% mais cartoes que a media.

As entradas sao parciais (so arbitros mais frequentes/notorios). Quando
um arbitro ou par de equipas nao esta na tabela, assume-se media de liga.

Para enriquecer:
  - Scrape wikipedia dos arbitros Big-5 + estatisticas das ultimas N epocas
  - Scrape calendario / rivalidades locais
Nada disto esta automatizado ainda.
"""
from __future__ import annotations


# Media europeia Big-5 + LigaPT: ~4.3 cartoes/jogo total.
ARBITROS: dict[str, float] = {
    # ─── Premier League (severos a brandos) ─────────────────────────
    "michael oliver":    5.4,
    "anthony taylor":    5.2,
    "paul tierney":      5.6,
    "chris kavanagh":    4.8,
    "stuart attwell":    4.6,
    "simon hooper":      4.5,
    "andy madley":       4.2,
    "robert jones":      4.0,
    "jarred gillett":    4.1,
    "peter bankes":      4.3,
    "tony harrington":   4.4,
    "craig pawson":      3.8,

    # ─── La Liga ─────────────────────────────────────────────────────
    "mateu lahoz":       6.5,   # historicamente o mais severo de Espanha
    "cesar soto grado":  5.3,
    "jose maria sanchez": 5.1,
    "jesus gil manzano": 5.4,
    "ricardo de burgos": 5.0,
    "antonio mateu lahoz": 6.5,  # alias
    "melero lopez":      4.9,
    "guillermo cuadra":  4.8,
    "javier alberola":   4.7,

    # ─── Serie A ─────────────────────────────────────────────────────
    "daniele orsato":    5.2,
    "daniele doveri":    5.0,
    "marco guida":       4.9,
    "davide massa":      5.1,
    "fabio maresca":     4.8,
    "michael fabbri":    4.6,
    "maurizio mariani":  4.5,

    # ─── Bundesliga ──────────────────────────────────────────────────
    "felix zwayer":      4.4,
    "deniz aytekin":     4.6,
    "felix brych":       4.5,
    "manuel grafe":      4.1,
    "tobias stieler":    4.3,
    "sascha stegemann":  4.3,
    "harm osmers":       4.0,

    # ─── Ligue 1 ─────────────────────────────────────────────────────
    "clement turpin":    4.0,
    "benoit bastien":    4.1,
    "francois letexier":  4.3,
    "stephanie frappart": 3.9,
    "ruddy buquet":      4.4,
    "willy delajod":     4.0,

    # ─── Liga Portugal ───────────────────────────────────────────────
    "artur soares dias": 5.0,
    "joao pinheiro":     4.8,
    "luis godinho":      4.6,
    "fabio verissimo":   5.2,
    "nuno almeida":      4.7,
    "andre narciso":     4.4,
    "manuel mota":       4.5,
}

MEDIA_LIGA_CARDS = 4.3


def rate_arbitro(nome: str | None) -> float:
    """Devolve cards/90 esperados para um arbitro, ou media de liga."""
    if not nome:
        return MEDIA_LIGA_CARDS
    key = nome.lower().strip()
    return ARBITROS.get(key, MEDIA_LIGA_CARDS)


def fator_arbitro(nome: str | None) -> float:
    """Multiplicador relativo a media europeia. 1.0 = media."""
    return rate_arbitro(nome) / MEDIA_LIGA_CARDS


# Rivalidades conhecidas. Set de frozensets para match sem ordem casa/fora.
# Nomes como estao na DB (football-data style).
DERBIES: set[frozenset[str]] = {
    # ─── Premier League ─────────────────────────────────────────────
    frozenset(("Man United", "Man City")),       # Manchester derby
    frozenset(("Man United", "Liverpool")),      # NW England classico
    frozenset(("Arsenal", "Tottenham")),         # North London
    frozenset(("Chelsea", "Arsenal")),           # London derby
    frozenset(("Chelsea", "Tottenham")),         # London derby
    frozenset(("Chelsea", "Fulham")),            # West London
    frozenset(("Everton", "Liverpool")),         # Merseyside
    frozenset(("Newcastle", "Sunderland")),      # Tyne-Wear
    frozenset(("West Ham", "Arsenal")),          # Londres
    frozenset(("West Ham", "Chelsea")),          # Londres
    frozenset(("West Ham", "Tottenham")),        # Londres
    frozenset(("Crystal Palace", "Brighton")),   # M23
    frozenset(("Aston Villa", "Birmingham")),    # Second City

    # ─── La Liga ─────────────────────────────────────────────────────
    frozenset(("Real Madrid", "Barcelona")),     # El Clasico
    frozenset(("Real Madrid", "Ath Madrid")),    # Madrid derby
    frozenset(("Real Madrid", "Ath Bilbao")),    # historico
    frozenset(("Barcelona", "Espanol")),         # Barcelona derby
    frozenset(("Sevilla", "Betis")),             # Sevilla derby
    frozenset(("Valencia", "Villarreal")),       # Comunidade Valenciana
    frozenset(("Valencia", "Levante")),          # cidade de Valencia
    frozenset(("Sociedad", "Ath Bilbao")),       # pais basco
    frozenset(("Celta", "La Coruna")),           # Galicia

    # ─── Serie A ─────────────────────────────────────────────────────
    frozenset(("Juventus", "Inter")),            # Derby d'Italia
    frozenset(("Juventus", "Milan")),            # derby classico
    frozenset(("Inter", "Milan")),               # Milano derby
    frozenset(("Roma", "Lazio")),                # Derby della Capitale
    frozenset(("Juventus", "Torino")),           # Derby della Mole
    frozenset(("Genoa", "Sampdoria")),           # Derby della Lanterna
    frozenset(("Napoli", "Roma")),               # historico
    frozenset(("Fiorentina", "Juventus")),       # rivalidade intensa
    frozenset(("Bologna", "Fiorentina")),        # emilia-toscana

    # ─── Bundesliga ──────────────────────────────────────────────────
    frozenset(("Bayern Munich", "Dortmund")),    # Der Klassiker
    frozenset(("Dortmund", "Schalke 04")),       # Revierderby
    frozenset(("Bayern Munich", "M'gladbach")),  # ha derby ja
    frozenset(("Hertha", "Union Berlin")),       # Berlin derby
    frozenset(("Hamburg", "Werder Bremen")),     # Nord derby
    frozenset(("Stuttgart", "Karlsruhe")),       # Baden-Wurttemberg
    frozenset(("FC Koln", "M'gladbach")),        # Renania
    frozenset(("Freiburg", "Stuttgart")),        # sul Alemanha

    # ─── Ligue 1 ─────────────────────────────────────────────────────
    frozenset(("Paris SG", "Marseille")),        # Le Classique
    frozenset(("Marseille", "Lyon")),            # Olympico
    frozenset(("Marseille", "Nice")),            # Cote d'Azur
    frozenset(("Paris SG", "Lyon")),             # historico
    frozenset(("Monaco", "Nice")),               # Riviera
    frozenset(("Saint-Etienne", "Lyon")),        # Derby Rhone-Alpes
    frozenset(("Lille", "Lens")),                # Nord derby
    frozenset(("Bordeaux", "Toulouse")),         # sul-oeste

    # ─── Liga Portugal ───────────────────────────────────────────────
    frozenset(("Benfica", "Porto")),             # Classico
    frozenset(("Benfica", "Sp Lisbon")),         # Lisboa
    frozenset(("Porto", "Sp Lisbon")),           # Porto-Sporting
    frozenset(("Benfica", "Braga")),             # historico
    frozenset(("Porto", "Boavista")),            # Porto derby
    frozenset(("Benfica", "Belenenses")),        # Lisboa historico
    frozenset(("Sp Lisbon", "Belenenses")),      # Lisboa historico
}


def eh_derby(casa: str, fora: str) -> bool:
    """True se o par (casa, fora) é um derby reconhecido."""
    return frozenset((casa, fora)) in DERBIES


def fator_rivalidade(casa: str, fora: str) -> float:
    """Multiplicador cartoes para derbies (~+15%)."""
    return 1.15 if eh_derby(casa, fora) else 1.0
