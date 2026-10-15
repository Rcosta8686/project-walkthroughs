"""Testes de clv_tracker: snapshot + CLV."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apostas.modelos import clv_tracker
from apostas.utils.schema import Base, OddsSnapshotPrelive


@pytest.fixture
def engine_test(monkeypatch):
    """Engine SQLite in-memory + patch de abrir_sessao."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class _Ctx:
        def __init__(self, eng):
            self._s = Session(eng)
        def __enter__(self):
            return self._s
        def __exit__(self, *a):
            self._s.close()

    monkeypatch.setattr(clv_tracker, "abrir_sessao", lambda: _Ctx(engine))
    return engine


def _gravar(sofascore_event_id, mercado, linha, lado, odd, casa, momento):
    clv_tracker.gravar_snapshot(
        sofascore_event_id=sofascore_event_id, mercado=mercado, linha=linha,
        lado=lado, odd=odd, casa=casa, momento=momento,
    )


def test_calcula_clv_vs_casa_com_fecho_disponivel(engine_test):
    # Fecho da casa: Over 2.5 @ 1.95 (equivale a prob 51.3%)
    _gravar(123, "golos", 2.5, "over", 1.95, "Pinnacle", "fecho")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10,
    )
    assert r is not None
    assert r.odd_fecho == 1.95
    # CLV vs casa = 2.10/1.95 - 1 = 7.69%
    assert r.clv_vs_casa == pytest.approx(0.0769, abs=1e-3)
    assert r.momento_usado == "fecho"


def test_tira_margem_quando_tem_os_dois_lados(engine_test):
    # Over 2.5 @ 1.95, Under 2.5 @ 1.95 — margem perfeita 2.56%
    _gravar(123, "golos", 2.5, "over", 1.95, "Pinnacle", "fecho")
    _gravar(123, "golos", 2.5, "under", 1.95, "Pinnacle", "fecho")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10,
    )
    assert r is not None
    # Odd fecho justa = 1 / 0.5 = 2.00
    assert r.odd_fecho_justa == pytest.approx(2.00, abs=1e-3)
    # CLV vs justa = 2.10/2.00 - 1 = 5%
    assert r.clv_vs_justa == pytest.approx(0.05, abs=1e-3)


def test_prefere_fecho_sobre_abertura(engine_test):
    _gravar(123, "golos", 2.5, "over", 2.10, "Pinnacle", "abertura")
    _gravar(123, "golos", 2.5, "over", 1.90, "Pinnacle", "fecho")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10,
    )
    assert r.momento_usado == "fecho"
    assert r.odd_fecho == 1.90


def test_fallback_para_abertura_se_sem_fecho(engine_test):
    _gravar(123, "golos", 2.5, "over", 2.10, "Pinnacle", "abertura")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10,
    )
    assert r.momento_usado == "abertura"


def test_devolve_none_quando_sem_snapshots(engine_test):
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=999, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10,
    )
    assert r is None


def test_filtra_por_casa_preferida(engine_test):
    _gravar(123, "golos", 2.5, "over", 2.00, "SpieleWerk", "fecho")
    _gravar(123, "golos", 2.5, "over", 1.90, "Pinnacle", "fecho")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=2.5, lado="over",
        odd_apostada=2.10, casa_preferida="Pinnacle",
    )
    assert r.casa_usada == "Pinnacle"
    assert r.odd_fecho == 1.90


def test_matching_por_linha(engine_test):
    _gravar(123, "golos", 2.5, "over", 1.90, "Pinnacle", "fecho")
    _gravar(123, "golos", 1.5, "over", 1.30, "Pinnacle", "fecho")
    r = clv_tracker.calcular_clv_sinal(
        sofascore_event_id=123, mercado="golos", linha=1.5, lado="over",
        odd_apostada=1.35,
    )
    assert r.linha == 1.5
    assert r.odd_fecho == 1.30
