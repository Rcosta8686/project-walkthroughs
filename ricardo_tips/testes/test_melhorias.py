"""Testes das funcoes de apostas/modelos/melhorias.py.

Valores de referencia tirados directamente do documento
docs/MELHORIAS_SCANNER.md (tabela do fim).

Correr: pytest testes/test_melhorias.py -v
"""
from __future__ import annotations

import pytest

from apostas.modelos.melhorias import (
    clv,
    ev,
    ev_ah,
    grelha_dixon_coles,
    inverter_linha_total,
    kelly_quarto,
    lucro_esperado_chf,
    odd_americana_para_decimal,
    passa_stop_loss,
    poisson_cdf,
    poisson_pmf,
    prob_btts_da_grelha,
    prob_over,
    prob_over_da_grelha,
    probs_1x2_da_grelha,
    stake_flat,
    stake_kelly_cap,
    tirar_margem,
)


# ─────────────── M1 — remover margem ───────────────

def test_tirar_margem_binaria():
    probs, margem = tirar_margem([1.95, 1.95])
    assert probs == pytest.approx([0.5, 0.5], abs=1e-4)
    assert margem == pytest.approx(0.02564, abs=1e-4)


def test_tirar_margem_1x2():
    probs, margem = tirar_margem([2.10, 3.40, 3.60])
    assert probs == pytest.approx([0.4543, 0.2806, 0.2650], abs=1e-3)
    assert margem == pytest.approx(0.0481, abs=1e-3)


def test_tirar_margem_rejeita_odd_invalida():
    with pytest.raises(ValueError):
        tirar_margem([1.0, 2.0])


# ─────────────── EV e Kelly ───────────────

def test_ev_referencia():
    assert ev(0.55, 2.00) == pytest.approx(0.10)


def test_kelly_quarto_cap_a_2pct():
    # p=0.55, odd=2.00 -> f_kelly=0.10, /4=0.025, cap em 0.02
    assert kelly_quarto(0.55, 2.00, max_frac=0.02) == pytest.approx(0.02)


def test_kelly_quarto_negativo_vira_zero():
    # p=0.45, odd=2.00 -> EV negativo, Kelly < 0
    assert kelly_quarto(0.45, 2.00) == 0.0


# ─────────────── Odd americana ───────────────

def test_odd_americana_positiva():
    assert odd_americana_para_decimal(140) == pytest.approx(2.40, abs=1e-4)


def test_odd_americana_negativa():
    assert odd_americana_para_decimal(-175) == pytest.approx(1.5714, abs=1e-4)


# ─────────────── M2 — CLV ───────────────

def test_clv_referencia():
    # odd apostada 2.10, fecho justo 1.95 -> 7.69%
    assert clv(2.10, 1.95) == pytest.approx(0.0769, abs=1e-3)


def test_clv_negativo_quando_odd_piora():
    # Apostou a 1.80, fecho justo 2.00 -> CLV negativo
    assert clv(1.80, 2.00) < 0


# ─────────────── Poisson ───────────────

def test_prob_over_2_5_lambda_2_6():
    # Doc: lam total 2.6 -> P(Over 2.5) ≈ 0.4816
    assert prob_over(2.5, 2.6) == pytest.approx(0.4816, abs=1e-3)


def test_poisson_pmf_zero():
    assert poisson_pmf(0, 2.0) == pytest.approx(0.1353, abs=1e-3)


def test_poisson_cdf_monotonia():
    assert poisson_cdf(0, 1.5) < poisson_cdf(1, 1.5) < poisson_cdf(5, 1.5)


# ─────────────── M4 — Grelha Dixon-Coles ───────────────

def test_grelha_poisson_sem_rho_1x2():
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    casa, empate, fora = probs_1x2_da_grelha(g)
    assert casa == pytest.approx(0.4642, abs=1e-3)
    assert empate == pytest.approx(0.2577, abs=1e-3)
    assert fora == pytest.approx(0.2781, abs=1e-3)


def test_grelha_poisson_sem_rho_over_2_5():
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    assert prob_over_da_grelha(g, 2.5) == pytest.approx(0.4816, abs=1e-3)


def test_grelha_poisson_sem_rho_btts():
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    assert prob_btts_da_grelha(g) == pytest.approx(0.5183, abs=1e-3)


def test_grelha_dixon_coles_com_rho():
    g = grelha_dixon_coles(1.5, 1.1, rho=-0.1)
    casa, empate, fora = probs_1x2_da_grelha(g)
    # Doc: casa ≈ 0.4520, empate ≈ 0.2822, fora ≈ 0.2658
    assert casa == pytest.approx(0.4520, abs=1e-3)
    assert empate == pytest.approx(0.2822, abs=1e-3)
    assert fora == pytest.approx(0.2658, abs=1e-3)
    assert prob_btts_da_grelha(g) == pytest.approx(0.5305, abs=1e-3)


# ─────────────── M4 — Handicap asiatico ───────────────

def test_ah_casa_menos_0_5():
    # Doc: lam_c=1.5, lam_f=1.1, rho=0; casa -0.5 @ 1.90 -> EV ≈ -0.1179
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    assert ev_ah(g, "casa", -0.5, 1.90) == pytest.approx(-0.1179, abs=1e-3)


def test_ah_casa_menos_0_25():
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    assert ev_ah(g, "casa", -0.25, 1.75) == pytest.approx(-0.0587, abs=1e-3)


def test_ah_casa_zero_push_possivel():
    g = grelha_dixon_coles(1.5, 1.1, rho=0.0)
    assert ev_ah(g, "casa", 0.0, 1.60) == pytest.approx(0.0005, abs=1e-3)


# ─────────────── M12 — Inverter linha ───────────────

def test_inverter_linha_total_a():
    # p_over 0.55, faltam 3 golos -> lam ≈ 2.8826
    assert inverter_linha_total(0.55, 3) == pytest.approx(2.8826, abs=1e-3)


def test_inverter_linha_total_b():
    # p_over 0.40, faltam 2 golos -> lam ≈ 1.3764
    assert inverter_linha_total(0.40, 2) == pytest.approx(1.3764, abs=1e-3)


# ─────────────── M13 — Banca simples ───────────────

def test_stake_flat_5chf():
    assert stake_flat() == 5.0
    assert stake_flat(10) == 10.0


def test_stake_kelly_cap_arredonda_a_5():
    # Prob 0.55, odd 2.0, banca 100: kelly_1/4 = 0.025, cap 0.02 → 2 CHF.
    # Arredondar a 5 → 0 CHF (não vale a pena stake tão pequena).
    assert stake_kelly_cap(0.55, 2.00, banca=100, round_to=5.0) == 0.0


def test_stake_kelly_cap_banca_grande():
    # Com banca de 1000: cap 2% = 20 CHF, arredonda a 5 -> 20
    assert stake_kelly_cap(0.55, 2.00, banca=1000, round_to=5.0) == 20.0


def test_lucro_esperado():
    assert lucro_esperado_chf(0.55, 2.00, stake=5.0) == pytest.approx(0.50)


def test_stop_loss_dia_bate():
    # Perdeu 2.5 CHF numa banca de 50: 5% exacto -> bate stop
    ok, motivo = passa_stop_loss(perda_dia=2.5, perda_semana=0.0, banca_atual=50)
    assert not ok
    assert "dia" in motivo


def test_stop_loss_semana_bate():
    ok, motivo = passa_stop_loss(perda_dia=0.0, perda_semana=5.0, banca_atual=50)
    assert not ok
    assert "semana" in motivo


def test_stop_loss_ok():
    ok, _ = passa_stop_loss(perda_dia=1.0, perda_semana=1.0, banca_atual=50)
    assert ok


def test_banca_zero_bloqueia():
    ok, motivo = passa_stop_loss(perda_dia=0, perda_semana=0, banca_atual=0)
    assert not ok
    assert "esgot" in motivo
