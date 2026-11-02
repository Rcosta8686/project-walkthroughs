"""Filtro de fichas fracas: ambas as equipas precisam de >= 5 jogos.

Caso real: /saude reporta 61% das equipas na BD com <5 jogos. Scanner
nao deve emitir sinais quando qualquer uma das equipas nao tem base
historica suficiente — projeccao e lotaria.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    scn._cache_n_jogos_ficha.clear()
    yield
    scn._cache_n_jogos_ficha.clear()


def test_min_jogos_ficha_default():
    assert scn._MIN_JOGOS_FICHA == 5


# ─── _n_jogos_ficha ──────────────────────────────────────────────────────

def test_sem_team_id_devolve_zero():
    assert scn._n_jogos_ficha(None) == 0
    assert scn._n_jogos_ficha(0) == 0


def test_usa_cache_apos_primeira_chamada(monkeypatch):
    chamadas = []

    def fake_gerar(team_id, n_jogos=20):
        chamadas.append(team_id)
        return SimpleNamespace(n_jogos=12)

    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha", fake_gerar,
    )
    assert scn._n_jogos_ficha(100) == 12
    assert scn._n_jogos_ficha(100) == 12
    assert chamadas == [100]  # so uma chamada apesar de 2 chamadas da api


def test_ficha_none_devolve_zero(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: None,
    )
    assert scn._n_jogos_ficha(123) == 0


def test_excepcao_devolve_zero(monkeypatch):
    def boom(team_id, n_jogos=20):
        raise RuntimeError("DB down")

    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha", boom,
    )
    assert scn._n_jogos_ficha(777) == 0


# ─── _ambas_fichas_fortes ────────────────────────────────────────────────

def test_ambas_sem_id_e_fraco():
    assert scn._ambas_fichas_fortes(None, None) is False
    assert scn._ambas_fichas_fortes(1, None) is False
    assert scn._ambas_fichas_fortes(None, 2) is False


def test_ambas_com_mais_de_5_jogos_e_forte(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=10),
    )
    assert scn._ambas_fichas_fortes(100, 200) is True


def test_uma_com_poucos_jogos_e_fraco(monkeypatch):
    def fake(team_id, n_jogos=20):
        return SimpleNamespace(n_jogos=10 if team_id == 100 else 2)

    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha", fake,
    )
    assert scn._ambas_fichas_fortes(100, 200) is False
    # cache limpa antes para ter certeza do outro lado
    scn._cache_n_jogos_ficha.clear()
    assert scn._ambas_fichas_fortes(200, 100) is False


def test_exactamente_5_jogos_passa(monkeypatch):
    """5 >= 5 — limite inclusivo."""
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=5),
    )
    assert scn._ambas_fichas_fortes(100, 200) is True


def test_4_jogos_falha(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=4),
    )
    assert scn._ambas_fichas_fortes(100, 200) is False


def test_reset_do_cache_apaga(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=15),
    )
    scn._n_jogos_ficha(42)
    assert 42 in scn._cache_n_jogos_ficha
    scn._resetar_blacklist_cache()
    assert 42 not in scn._cache_n_jogos_ficha
