"""Testes para apostas/alertas/paises_banidos.py."""
from __future__ import annotations

import pytest

from apostas.alertas.paises_banidos import jogo_banido


def test_bloqueia_por_pais_russia():
    banido, razao = jogo_banido("Russia", "Premier League", "Rodina Moscow", "Orenburg")
    assert banido is True
    assert "Russia" in razao or "russia" in razao.lower()


def test_bloqueia_por_pais_case_insensitive():
    assert jogo_banido("RUSSIA", None, None, None)[0] is True
    assert jogo_banido("russia", None, None, None)[0] is True
    assert jogo_banido(" Russia ", None, None, None)[0] is True


def test_bloqueia_belarus():
    assert jogo_banido("Belarus", None, None, None)[0] is True


def test_nao_bloqueia_portugal():
    banido, _ = jogo_banido("Portugal", "Liga Portugal", "Benfica", "Porto")
    assert banido is False


def test_bloqueia_quando_pais_vazio_mas_equipa_russa():
    """Sofascore pode nao devolver category.name — cair no match da equipa."""
    banido, razao = jogo_banido(None, "Premier League", "Rodina Moscow", "Orenburg")
    assert banido is True
    assert "russa" in razao.lower()


def test_bloqueia_fcsk_moscow():
    assert jogo_banido(None, None, "CSKA Moscow", "Zenit")[0] is True


def test_bloqueia_quando_so_fora_e_russa():
    assert jogo_banido(None, "Taca Russa", "Benfica", "Spartak Moscow")[0] is True


def test_nao_bloqueia_sem_match():
    banido, _ = jogo_banido("Spain", "LaLiga", "Barcelona", "Real Madrid")
    assert banido is False


def test_estado_live_tem_campo_pais():
    """EstadoLive deve ter o campo `pais` para o filtro poder actuar."""
    from apostas.ingestao.sofascore_live import EstadoLive
    e = EstadoLive(event_id=1, home="A", away="B", liga="X",
                   minuto=0, estado="1H",
                   marcador_casa=0, marcador_fora=0)
    assert hasattr(e, "pais")
    assert e.pais is None
