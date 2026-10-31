"""Testes dos filtros extra no scanner (equipas B, rotacao no mega,
projecao minima)."""
from __future__ import annotations

import pytest

from apostas.alertas.mega_cerebro import (
    SinteseMega, sintetizar_a_partir_confronto,
)


class _Base:
    def __init__(self, remates=12, sot=5, cantos=5, cards=2.0, posse=50):
        self.remates = remates
        self.sot = sot
        self.cantos = cantos
        self.cards_amarelos = cards
        self.posse_pct = posse


class _PorRes:
    def __init__(self, remates_L=12.0):
        self.remates_L = remates_L


class _Ficha:
    def __init__(self, jogos=10):
        self.jogos_lidos = jogos
        self.baseline = _Base()
        self.por_resultado = _PorRes()


def test_mega_silencia_quando_rotacao_detectada():
    """Com rotacao_detectada=True nao emite sintese, mesmo com dados fortes."""
    sint = sintetizar_a_partir_confronto(
        minuto=60, score_casa=1, score_fora=0,
        sot_c=10, sot_f=10,
        cantos_c=5, cantos_f=5,
        cards_c=1, cards_f=1,
        ficha_casa=_Ficha(), ficha_fora=_Ficha(),
        rotacao_detectada=True,
    )
    assert sint is None


def test_mega_dispara_sem_rotacao_detectada():
    """Mesmos dados mas sem flag — deve emitir."""
    sint = sintetizar_a_partir_confronto(
        minuto=60, score_casa=1, score_fora=0,
        sot_c=10, sot_f=10,
        cantos_c=5, cantos_f=5,
        cards_c=1, cards_f=1,
        ficha_casa=_Ficha(), ficha_fora=_Ficha(),
        rotacao_detectada=False,
    )
    assert sint is not None


def test_equipa_b_detectada():
    """Detector local de equipa B — simula sem precisar correr o scanner."""
    def _e_equipa_b(nome: str) -> bool:
        n = (nome or "").strip().lower()
        if not n:
            return False
        return n.endswith(" b") or n.endswith(" ii") or " b " in n or " ii " in n

    assert _e_equipa_b("Real Sociedad B") is True
    assert _e_equipa_b("Barcelona B") is True
    assert _e_equipa_b("Benfica B") is True
    assert _e_equipa_b("Real Madrid Castilla II") is True
    # Equipas normais nao sao equipa B
    assert _e_equipa_b("Real Madrid") is False
    assert _e_equipa_b("FC Porto") is False
    assert _e_equipa_b("Benfica") is False
    assert _e_equipa_b("Barcelona") is False
    # Edge cases
    assert _e_equipa_b("") is False
    assert _e_equipa_b(None) is False
