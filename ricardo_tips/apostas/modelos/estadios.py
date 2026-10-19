"""Fase 8 — Agregacao de stats por estadio.

Alguns estadios tem particularidades (relvado pequeno, altitude, publico
hostil) que afectam as medias de golos/cantos/cards. Com volume
suficiente de jogos no mesmo venue podemos detectar efeitos.

Uso:
  perfil = perfil_estadio("Estadio da Luz", min_jogos=10)
  # → PerfilEstadio(golos_total=2.8, cards_total=4.1, ...)

Nao agrega por equipa — devolve medias cruas do venue.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from apostas.utils.db import abrir_sessao
from apostas.utils.schema import EstatisticaEquipaJogo


# Minimo de jogos de 1 venue para o perfil ser considerado util.
MIN_JOGOS_VENUE = 10


@dataclass
class PerfilEstadio:
    """Agregado de 1 venue."""
    venue: str
    n_linhas: int               # linhas da tabela (2 por jogo)
    n_jogos: int                # jogos distintos
    golos_marcados_media: float | None
    cards_amarelos_media: float | None
    cantos_media: float | None
    remates_media: float | None


def perfil_estadio(
    venue: str, *, min_jogos: int = MIN_JOGOS_VENUE,
) -> PerfilEstadio | None:
    """Devolve agregado de stats para um venue. None se nao houver volume."""
    v = (venue or "").strip()
    if not v:
        return None
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo).where(
                EstatisticaEquipaJogo.venue == v
            )
        ).all()
        for r in rows:
            s.expunge(r)
    if not rows:
        return None

    # Agrega por jogo (sofascore_event_id) para evitar contar duas vezes
    por_jogo: dict[int, list] = {}
    for r in rows:
        por_jogo.setdefault(r.sofascore_event_id, []).append(r)

    n_jogos = len(por_jogo)
    if n_jogos < min_jogos:
        return None

    totais_golos = []
    totais_cards = []
    totais_cantos = []
    totais_remates = []
    for linhas_jogo in por_jogo.values():
        if len(linhas_jogo) == 2:
            # Soma dos dois lados = total do jogo
            g = sum((l.golos_marcados or 0) for l in linhas_jogo)
            cards = sum((l.cards_amarelos or 0) for l in linhas_jogo
                        if l.cards_amarelos is not None)
            cantos = sum((l.cantos or 0) for l in linhas_jogo
                         if l.cantos is not None)
            remates = sum((l.remates or 0) for l in linhas_jogo
                          if l.remates is not None)
            totais_golos.append(g)
            if any(l.cards_amarelos is not None for l in linhas_jogo):
                totais_cards.append(cards)
            if any(l.cantos is not None for l in linhas_jogo):
                totais_cantos.append(cantos)
            if any(l.remates is not None for l in linhas_jogo):
                totais_remates.append(remates)
        else:
            # So uma linha — duplica (menos preciso mas util)
            l = linhas_jogo[0]
            totais_golos.append(2 * (l.golos_marcados or 0))

    _m = lambda vs: (sum(vs) / len(vs)) if vs else None  # noqa: E731
    return PerfilEstadio(
        venue=v,
        n_linhas=len(rows),
        n_jogos=n_jogos,
        golos_marcados_media=_m(totais_golos),
        cards_amarelos_media=_m(totais_cards),
        cantos_media=_m(totais_cantos),
        remates_media=_m(totais_remates),
    )


def comparar_com_liga(
    venue: str, liga: str, *, min_jogos: int = MIN_JOGOS_VENUE,
) -> dict | None:
    """Compara o venue com a media da liga. Devolve dict com deltas
    em percentagem para golos, cantos e cards. None se nao chegar
    volume ou liga nao coincide.
    """
    p = perfil_estadio(venue, min_jogos=min_jogos)
    if p is None:
        return None
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo).where(
                EstatisticaEquipaJogo.liga == liga
            )
        ).all()
        for r in rows:
            s.expunge(r)
    if not rows:
        return None
    por_jogo_liga: dict[int, list] = {}
    for r in rows:
        por_jogo_liga.setdefault(r.sofascore_event_id, []).append(r)
    # Agrega liga igual ao venue
    g_liga, c_liga, cards_liga = [], [], []
    for lj in por_jogo_liga.values():
        if len(lj) == 2:
            g_liga.append(sum((l.golos_marcados or 0) for l in lj))
            if any(l.cantos is not None for l in lj):
                c_liga.append(sum((l.cantos or 0) for l in lj if l.cantos is not None))
            if any(l.cards_amarelos is not None for l in lj):
                cards_liga.append(sum((l.cards_amarelos or 0) for l in lj
                                      if l.cards_amarelos is not None))
    _m = lambda vs: (sum(vs) / len(vs)) if vs else None  # noqa: E731
    g_m = _m(g_liga); c_m = _m(c_liga); cards_m = _m(cards_liga)

    def _delta_pct(venue_v, liga_v):
        if venue_v is None or liga_v is None or liga_v <= 0:
            return None
        return (venue_v / liga_v - 1) * 100

    return {
        "venue": venue, "liga": liga,
        "n_jogos_venue": p.n_jogos,
        "n_jogos_liga": len(por_jogo_liga),
        "delta_golos_pct": _delta_pct(p.golos_marcados_media, g_m),
        "delta_cantos_pct": _delta_pct(p.cantos_media, c_m),
        "delta_cards_pct": _delta_pct(p.cards_amarelos_media, cards_m),
    }
