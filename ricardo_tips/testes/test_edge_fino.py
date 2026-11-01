"""Filtro de edge fino: odds baixas + prob/EV marginais bloqueiam alerta.

Caso real Lecce-Genoa Over 30.5 remates (prob 79%, odd alvo 1.33) perdeu
por 1 remate. Sinais com margem fina (odd < 1.40 + prob < 82%) sao
lotarias que a variance come. Bloqueiam.
"""
from __future__ import annotations


# ─── Teste logico puro ──────────────────────────────────────────────────

def _bloqueia_analista(odd: float, prob: float) -> bool:
    """Replica a regra do scanner_live_v2 para o analista."""
    if odd <= 0:
        return False  # sem odd nao avaliamos
    return odd < 1.40 and prob < 0.82


def _bloqueia_ev(odd: float, ev: float) -> bool:
    """Replica a regra do scanner_live_v2 para sinais com Odds API."""
    if odd <= 0:
        return False
    return odd < 1.40 and ev < 0.12


# ─── Analista: edge fino ─────────────────────────────────────────────────

def test_lecce_genoa_over_30_5_bloqueado():
    """Caso real: prob 79%, odd 1.33 → edge fino, bloqueia."""
    assert _bloqueia_analista(odd=1.33, prob=0.79) is True


def test_odd_alta_passa_mesmo_com_prob_baixa():
    """Odd 1.70 + prob 0.60 → EV +2%, mas odd nao e fina → passa."""
    assert _bloqueia_analista(odd=1.70, prob=0.60) is False


def test_prob_muito_alta_passa_mesmo_com_odd_baixa():
    """Prob 85% + odd 1.30 → certeza, 1 desvio nao mata. Passa."""
    assert _bloqueia_analista(odd=1.30, prob=0.85) is False


def test_odd_limite_1_39_e_prob_80():
    """Odd 1.39 (abaixo do limite 1.40) + prob 80% (abaixo de 82%) → bloqueia."""
    assert _bloqueia_analista(odd=1.39, prob=0.80) is True


def test_odd_exactamente_1_40_passa():
    """Odd 1.40 nao e < 1.40 → passa (limite inclusivo)."""
    assert _bloqueia_analista(odd=1.40, prob=0.75) is False


def test_prob_82_exacta_passa():
    """Prob 0.82 nao e < 0.82 → passa (limite inclusivo)."""
    assert _bloqueia_analista(odd=1.30, prob=0.82) is False


def test_odd_zero_nao_bloqueia():
    """Odd 0 (sem valor) nao e avaliavel → nao bloqueia."""
    assert _bloqueia_analista(odd=0, prob=0.79) is False


# ─── EV com Odds API: edge fino ──────────────────────────────────────────

def test_ev_fino_odd_baixa_bloqueia():
    """EV +5% com odd 1.33 → variance come. Bloqueia."""
    assert _bloqueia_ev(odd=1.33, ev=0.05) is True


def test_ev_alto_mesmo_com_odd_baixa_passa():
    """EV +15% com odd 1.35 → edge consolidado. Passa."""
    assert _bloqueia_ev(odd=1.35, ev=0.15) is False


def test_ev_fino_odd_alta_passa():
    """EV +5% com odd 2.00 → odd nao e fina. Passa."""
    assert _bloqueia_ev(odd=2.00, ev=0.05) is False


def test_ev_12_exacto_passa():
    """EV 0.12 nao e < 0.12 → passa (limite inclusivo)."""
    assert _bloqueia_ev(odd=1.35, ev=0.12) is False


def test_odd_zero_ev_nao_bloqueia():
    assert _bloqueia_ev(odd=0, ev=0.08) is False


# ─── Confirmacao de que a regra esta no source ───────────────────────────

def test_regra_edge_fino_no_scanner():
    """Verifica que o scanner tem o filtro aplicado."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "scanner_live_v2.py").read_text(encoding="utf-8")
    # Procura a chamada do filtro para analista
    assert "edge fino" in src.lower() or "odd_val < 1.40" in src
    assert "prob_val < 0.82" in src
