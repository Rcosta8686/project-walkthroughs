"""Tracking de Closing Line Value a partir de snapshots pré-match (M2).

Fluxo:
  1. snapshot_odds_prelive.py grava odds em varios momentos
     (abertura, +1h, pre_onzes, pos_onzes, fecho)
  2. Pre_live_diario / scanner emite sinal com odd_referencia
  3. Este modulo calcula CLV usando snapshot "fecho" (ou o mais recente)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from apostas.modelos.melhorias import clv as _clv, tirar_margem
from apostas.utils.db import abrir_sessao
from apostas.utils.schema import OddsSnapshotPrelive


MOMENTOS_FECHO = ("fecho", "pos_onzes", "pre_onzes", "+1h", "abertura")  # fallback descendente


@dataclass
class CLVResultado:
    sofascore_event_id: int
    mercado: str
    linha: float | None
    lado: str
    odd_apostada: float
    odd_fecho: float          # odd mais recente da casa de referencia
    odd_fecho_justa: float    # sem margem
    clv_vs_casa: float        # CLV usando odd_fecho (com margem)
    clv_vs_justa: float       # CLV usando odd_fecho_justa (verdadeiro CLV)
    momento_usado: str
    casa_usada: str


def _opor(lado: str) -> str | None:
    """Devolve o lado oposto para calcular prob justa binaria."""
    mapa = {"over": "under", "under": "over",
            "casa": "fora", "fora": "casa",
            "sim": "nao", "nao": "sim"}
    return mapa.get(lado)


def calcular_clv_sinal(
    sofascore_event_id: int,
    mercado: str,
    linha: float | None,
    lado: str,
    odd_apostada: float,
    casa_preferida: str | None = None,
) -> Optional[CLVResultado]:
    """Calcula CLV do sinal usando snapshots gravados.

    Procura por ordem decrescente de proximidade ao kickoff:
      1. snapshot "fecho" (ideal; -5min do kickoff)
      2. pos_onzes, pre_onzes, +1h, abertura (fallback)

    Para `odd_fecho_justa` tira margem do par (lado, lado_oposto). Se não
    houver lado oposto, usa a propria odd (menos preciso).
    """
    with abrir_sessao() as s:
        q = select(OddsSnapshotPrelive).where(
            OddsSnapshotPrelive.sofascore_event_id == sofascore_event_id,
            OddsSnapshotPrelive.mercado == mercado,
            OddsSnapshotPrelive.lado == lado,
        )
        if linha is not None:
            q = q.where(OddsSnapshotPrelive.linha == linha)
        if casa_preferida:
            q = q.where(OddsSnapshotPrelive.casa == casa_preferida)
        snaps_lado = s.scalars(q).all()
        if not snaps_lado:
            return None

        # Melhor snapshot = momento mais tardio (fecho > ... > abertura)
        def _rank(sn):
            try:
                return MOMENTOS_FECHO.index(sn.momento)
            except ValueError:
                return 999
        snaps_lado.sort(key=_rank)
        melhor = snaps_lado[0]

        # Tenta tirar margem usando lado oposto no mesmo momento/casa
        oposto = _opor(lado)
        odd_fecho = melhor.odd
        odd_fecho_justa = odd_fecho  # fallback: assume ja-justa
        if oposto:
            q_op = select(OddsSnapshotPrelive).where(
                OddsSnapshotPrelive.sofascore_event_id == sofascore_event_id,
                OddsSnapshotPrelive.mercado == mercado,
                OddsSnapshotPrelive.lado == oposto,
                OddsSnapshotPrelive.momento == melhor.momento,
                OddsSnapshotPrelive.casa == melhor.casa,
            )
            if linha is not None:
                q_op = q_op.where(OddsSnapshotPrelive.linha == linha)
            snap_op = s.scalars(q_op).first()
            if snap_op:
                probs, _margem = tirar_margem([odd_fecho, snap_op.odd])
                odd_fecho_justa = 1.0 / probs[0]

    return CLVResultado(
        sofascore_event_id=sofascore_event_id,
        mercado=mercado,
        linha=linha,
        lado=lado,
        odd_apostada=odd_apostada,
        odd_fecho=odd_fecho,
        odd_fecho_justa=odd_fecho_justa,
        clv_vs_casa=_clv(odd_apostada, odd_fecho),
        clv_vs_justa=_clv(odd_apostada, odd_fecho_justa),
        momento_usado=melhor.momento,
        casa_usada=melhor.casa,
    )


def clv_live(odd_executada: float, odd_recomendada: float) -> float | None:
    """CLV simples para apostas LIVE.

    Como nao existe snapshot "fecho" para a janela de 1-2 minutos entre
    sinal e aposta, usamos a odd que o scanner viu quando emitiu o sinal
    como proxy da linha "justa" no momento. Formula:

        clv = odd_executada / odd_recomendada - 1

    Interpretacao:
      - clv > 0 → user conseguiu odd melhor do que a recomendada (bateu
        o alvo). Significa que esperou o mercado mover a nosso favor.
      - clv ≈ 0 → entrou perto do sinal, sem edge extra.
      - clv < 0 → entrou tarde (o mercado ja tinha corrigido). Nao
        bloqueia a aposta — o modelo pode continuar certo — mas e sinal
        de atraso execucao.
    """
    if odd_recomendada is None or odd_recomendada <= 1.0:
        return None
    if odd_executada is None or odd_executada <= 1.0:
        return None
    return odd_executada / odd_recomendada - 1.0


def gravar_snapshot(
    sofascore_event_id: int,
    mercado: str,
    linha: float | None,
    lado: str,
    odd: float,
    casa: str,
    momento: str,
) -> int:
    """Grava um snapshot. Nao faz dedup — chamador decide."""
    with abrir_sessao() as s:
        snap = OddsSnapshotPrelive(
            sofascore_event_id=sofascore_event_id,
            mercado=mercado,
            linha=linha,
            lado=lado,
            odd=odd,
            casa=casa,
            momento=momento,
        )
        s.add(snap)
        s.commit()
        return snap.id
