"""Testes para apostas/alertas/filtros_mercado.py — M18 + M19."""
from __future__ import annotations

from datetime import datetime, timedelta

from apostas.alertas import filtros_mercado as fm
from apostas.utils.tempo import agora_utc


def _bookmaker(key: str, mercado: str, outcomes: list[dict]) -> dict:
    return {
        "key": key,
        "markets": [{"key": mercado, "outcomes": outcomes}],
    }


def _bm_totals(key: str, point: float, over_odd: float, under_odd: float) -> dict:
    return _bookmaker(key, "totals", [
        {"name": "Over", "price": over_odd, "point": point},
        {"name": "Under", "price": under_odd, "point": point},
    ])


def test_passa_com_duas_casas_suicas_e_pinnacle_fiavel():
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        _bm_totals("bet365", 2.5, 1.88, 1.92),
        _bm_totals("pinnacle", 2.5, 2.02, 1.95),  # margem OK
    ]
    r = fm.avaliar(bms, "totals", "over", "under", 2.5)
    assert r.passa
    assert r.melhor_casa == "betano"
    assert r.melhor_odd == 1.95
    assert r.pior_odd == 1.88
    assert r.diferenca_pior == 0.07
    assert r.margem_mercado is not None
    assert r.margem_mercado < fm.MARGEM_MAX_FIAVEL


def test_bloqueia_margem_alta():
    """Pinnacle com margem 15% (= 1/odds muito altas) → bloqueia."""
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        _bm_totals("bet365", 2.5, 1.88, 1.92),
        # Pinnacle 1.80 + 1.80 → margem (1/1.80 + 1/1.80 − 1) = 11%
        _bm_totals("pinnacle", 2.5, 1.80, 1.80),
    ]
    r = fm.avaliar(bms, "totals", "over", "under", 2.5)
    assert not r.passa
    assert "margem" in r.razao_bloqueio.lower()


def test_bloqueia_odd_stale():
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        _bm_totals("bet365", 2.5, 1.88, 1.92),
        _bm_totals("pinnacle", 2.5, 2.02, 1.95),
    ]
    r = fm.avaliar(
        bms, "totals", "over", "under", 2.5,
        timestamp_leitura=agora_utc() - timedelta(seconds=120),
    )
    assert not r.passa
    assert "stale" in r.razao_bloqueio.lower()


def test_bloqueia_so_uma_casa_suica():
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        # bet365 nao oferece over 2.5
    ]
    r = fm.avaliar(bms, "totals", "over", "under", 2.5)
    assert not r.passa
    assert "casa" in r.razao_bloqueio.lower()


def test_flag_verificar_quando_ev_muito_alto():
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        _bm_totals("bet365", 2.5, 1.88, 1.92),
        _bm_totals("pinnacle", 2.5, 2.02, 1.95),
    ]
    r = fm.avaliar(
        bms, "totals", "over", "under", 2.5,
        ev_bruto=0.20,  # 20% EV = suspeito
    )
    assert r.passa  # ainda passa
    assert r.flag_verificar


def test_formatar_melhor_casa():
    bms = [
        _bm_totals("betano", 2.5, 1.95, 1.85),
        _bm_totals("bet365", 2.5, 1.88, 1.92),
        _bm_totals("pinnacle", 2.5, 2.02, 1.95),
    ]
    r = fm.avaliar(bms, "totals", "over", "under", 2.5)
    s = fm.formatar_melhor_casa(r)
    assert "Betano" in s
    assert "1.95" in s
    assert "bet365" in s


def test_outcome_sem_point_para_btts():
    """BTTS: point e None."""
    bms = [
        _bookmaker("betano", "btts", [
            {"name": "Yes", "price": 1.80},
            {"name": "No", "price": 1.95},
        ]),
        _bookmaker("bet365", "btts", [
            {"name": "Yes", "price": 1.75},
            {"name": "No", "price": 2.00},
        ]),
        _bookmaker("pinnacle", "btts", [
            {"name": "Yes", "price": 1.85},
            {"name": "No", "price": 1.98},
        ]),
    ]
    r = fm.avaliar(bms, "btts", "yes", "no", None)
    assert r.passa
    assert r.melhor_casa == "betano"
