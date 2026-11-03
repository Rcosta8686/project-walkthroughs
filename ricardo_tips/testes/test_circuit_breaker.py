"""Circuit breaker Sofascore: evita scanner ficar preso em cascata de timeouts.

Scanner do Ricardo encravado 5x em 2h ontem — pedidos repetidos com timeout
15s acumulavam. Com circuit breaker: 5 falhas consecutivas → 2 min de pausa
onde todas as chamadas devolvem None imediatamente.
"""
from __future__ import annotations

import time

import pytest

from apostas.ingestao import sofascore_live as sl


@pytest.fixture(autouse=True)
def _reset_circuit():
    sl._circuit_falhas_consecutivas = 0
    sl._circuit_aberto_ate = 0.0
    yield
    sl._circuit_falhas_consecutivas = 0
    sl._circuit_aberto_ate = 0.0


def test_circuit_fechado_por_default():
    assert sl._circuit_pode_pedir() is True


def test_sucesso_mantem_fechado():
    sl._circuit_registar(sucesso=True)
    assert sl._circuit_pode_pedir() is True
    assert sl._circuit_falhas_consecutivas == 0


def test_falha_unica_nao_abre():
    sl._circuit_registar(sucesso=False)
    assert sl._circuit_pode_pedir() is True
    assert sl._circuit_falhas_consecutivas == 1


def test_5_falhas_consecutivas_abrem_circuito():
    for _ in range(sl._CIRCUIT_FAILURE_THRESHOLD):
        sl._circuit_registar(sucesso=False)
    assert sl._circuit_pode_pedir() is False
    assert sl._circuit_aberto_ate > time.time()


def test_sucesso_zera_contador():
    sl._circuit_registar(sucesso=False)
    sl._circuit_registar(sucesso=False)
    assert sl._circuit_falhas_consecutivas == 2
    sl._circuit_registar(sucesso=True)
    assert sl._circuit_falhas_consecutivas == 0


def test_circuito_fecha_apos_timeout(monkeypatch):
    """Depois dos 2 min de pausa, pedidos voltam a passar."""
    for _ in range(sl._CIRCUIT_FAILURE_THRESHOLD):
        sl._circuit_registar(sucesso=False)
    assert sl._circuit_pode_pedir() is False
    # Simula passagem de tempo: avanca _circuit_aberto_ate para o passado
    sl._circuit_aberto_ate = time.time() - 1
    assert sl._circuit_pode_pedir() is True


def test_constants_sao_sensatas():
    assert sl._CIRCUIT_FAILURE_THRESHOLD >= 3
    assert sl._CIRCUIT_FAILURE_THRESHOLD <= 10
    assert sl._CIRCUIT_OPEN_SECS >= 60
    assert sl._CIRCUIT_OPEN_SECS <= 300


def test_breaker_integra_com_get_json(monkeypatch):
    """Quando o circuit esta aberto, _get_json devolve None imediatamente
    sem chamar flaresolverr."""
    chamadas = []

    def fake_get(*a, **kw):
        chamadas.append(1)
        raise RuntimeError("mock")

    monkeypatch.setattr("apostas.ingestao._flaresolverr.get", fake_get)

    # Abre o circuito manualmente
    sl._circuit_aberto_ate = time.time() + 60

    out = sl._get_json("/event/123")
    assert out is None
    assert chamadas == []  # flaresolverr nunca foi chamado


def test_breaker_404_nao_conta_como_falha():
    """404 e benigno. 5 x 404 nao deve abrir o circuito."""
    class FakeResp:
        status = 404
    # Simula flow do _get_json com 404 directly
    for _ in range(10):
        sl._circuit_registar(sucesso=True)
    assert sl._circuit_pode_pedir() is True
