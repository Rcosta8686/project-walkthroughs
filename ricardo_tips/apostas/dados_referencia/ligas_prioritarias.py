"""Ligas prioritarias para o Segundo Cerebro.

Escolhidas pelo Ricardo (2026-10-18):
  - Big 5 (EPL, LaLiga, Serie A, Bundesliga, Ligue 1) — so 1a divisao do
    frances (Ligue 2 foi omitida).
  - Segundas divisoes de ENG, ESP, ITA, GER, POR, HOL.
  - Portugal 1a Liga.
  - Holanda Eredivisie + Eerste Divisie.

Nao incluidas (fora do foco):
  - MLS, Brasileirao, Championship Scottish, Ligue 2, Belga, Turca...
  - Youth / U23 / Regionais (Oberliga, Regionalliga): ja bloqueadas no scanner.

Cada entrada tem:
  - nome: nome canonico (coincide com Sofascore quando possivel)
  - sofascore_id: id do tournament no Sofascore (ja em LIGAS_SOFASCORE_IDS)
  - prioridade: 1 = foco maximo, 2 = secundario (so popular se houver tempo)
  - pais: 2-letter code informativo

Para alterar: edita esta lista. Scripts popular_cerebros.py le daqui.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LigaPrioritaria:
    nome: str
    sofascore_id: int
    prioridade: int  # 1 = foco maximo, 2 = secundario
    pais: str


LIGAS_PRIORITARIAS: tuple[LigaPrioritaria, ...] = (
    # ─── 1a divisao Big 5 + Portugal + Holanda (prioridade 1) ──────────
    LigaPrioritaria("Premier League (ENG)",     17, 1, "ENG"),
    LigaPrioritaria("LaLiga",                    8, 1, "ESP"),
    LigaPrioritaria("Serie A (ITA)",            23, 1, "ITA"),
    LigaPrioritaria("Bundesliga",               35, 1, "GER"),
    LigaPrioritaria("Ligue 1 (FRA)",            34, 1, "FRA"),
    LigaPrioritaria("Liga Portugal",           238, 1, "POR"),
    LigaPrioritaria("Eredivisie",               37, 1, "NED"),

    # ─── 2a divisao (prioridade 2) ────────────────────────────────────
    LigaPrioritaria("Championship (ENG)",       18, 2, "ENG"),
    LigaPrioritaria("LaLiga 2 (Hypermotion)",   54, 2, "ESP"),
    LigaPrioritaria("Serie B (ITA)",            53, 2, "ITA"),
    LigaPrioritaria("Bundesliga 2",             44, 2, "GER"),
    LigaPrioritaria("Liga Portugal 2",         239, 2, "POR"),
    LigaPrioritaria("Eerste Divisie",          130, 2, "NED"),
)


def ligas_de_prioridade(prioridade: int) -> list[LigaPrioritaria]:
    """Devolve so as ligas com a prioridade pedida (1 ou 2)."""
    return [l for l in LIGAS_PRIORITARIAS if l.prioridade == prioridade]


def todas_ligas() -> list[LigaPrioritaria]:
    """Todas, ordenadas por prioridade."""
    return sorted(LIGAS_PRIORITARIAS, key=lambda l: (l.prioridade, l.nome))


def sofascore_ids_prioritarias() -> list[int]:
    """So os IDs Sofascore (util para filtros do scanner)."""
    return [l.sofascore_id for l in LIGAS_PRIORITARIAS]


_SUBSTRINGS_PRIORIDADE_1 = tuple(
    l.nome.lower() for l in LIGAS_PRIORITARIAS if l.prioridade == 1
) + (
    "premier league", "la liga", "serie a", "bundesliga 1",
    "primeira liga", "ligue 1", "eredivisie",
)


def nome_e_prioridade_1(liga_nome: str) -> bool:
    """True se o nome da liga bate numa das 7 ligas top (prioridade 1).

    Faz match case-insensitive por substring para funcionar com variantes
    de nome ("Serie A" vs "Serie A (ITA)" vs "Serie A - Italia"). Nao bate
    com '2. Bundesliga', 'Serie B', 'LaLiga 2', 'Championship' etc.
    """
    if not liga_nome:
        return False
    n = liga_nome.lower().strip()
    # Exclusoes explicitas (2a divisao): se contiver estes markers, nao e p1
    for marker in ("2.", " 2", "2 ", " ii", "segunda", "championship",
                   "serie b", "ligue 2", "liga portugal 2", "eerste",
                   "women", "feminin", "sub-", "u16", "u17", "u18",
                   "u19", "u20", "u21", "u23", "youth", "reserve"):
        if marker in n:
            return False
    for sub in _SUBSTRINGS_PRIORIDADE_1:
        if sub in n:
            return True
    return False
