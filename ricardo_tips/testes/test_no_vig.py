"""Testes do módulo no_vig (remoção de overround)."""

from __future__ import annotations

import math

import pytest

from apostas.modelos.no_vig import margem, prob_justa_1x2, prob_justa_binaria


def test_binaria_soma_1():
    # Pinnacle margem ~2%: 1.95 e 1.95 → sum(1/o) = 1.025
    p_over, p_under = prob_justa_binaria(1.95, 1.95)
    assert math.isclose(p_over + p_under, 1.0, abs_tol=1e-9)
    assert math.isclose(p_over, 0.5, abs_tol=1e-9)


def test_binaria_assimetrica():
    # Jogo com Over favorito: 1.5 vs 2.6 (sum = 1/1.5 + 1/2.6 ≈ 1.051)
    p_over, p_under = prob_justa_binaria(1.5, 2.6)
    assert math.isclose(p_over + p_under, 1.0, abs_tol=1e-9)
    assert p_over > p_under
    # Normalização: 1/1.5 / 1.0513 ≈ 0.634
    assert math.isclose(p_over, (1 / 1.5) / (1 / 1.5 + 1 / 2.6), abs_tol=1e-6)


def test_1x2_soma_1():
    p_c, p_x, p_f = prob_justa_1x2(2.0, 3.5, 4.0)
    assert math.isclose(p_c + p_x + p_f, 1.0, abs_tol=1e-9)
    assert p_c > p_f  # 2.0 < 4.0 → casa favorito → prob maior


def test_odd_invalida_erra():
    with pytest.raises(ValueError):
        prob_justa_binaria(0.9, 2.0)
    with pytest.raises(ValueError):
        prob_justa_1x2(1.0, 3.0, 4.0)


def test_overround_suspeito_erra():
    # Odds impossíveis — sum < 0.99 significa arbitragem a nosso favor
    # mas é sinal de erro nos dados.
    with pytest.raises(ValueError):
        prob_justa_binaria(10.0, 10.0)  # sum = 0.2


def test_margem():
    # Pinnacle típico: 2.6% (odds mais justas = margem baixa = sum perto de 1)
    m = margem([1.95, 1.95])
    assert 1.02 < m < 1.03
    # Soft book: 8% (odds mais apertadas = sum mais acima de 1)
    m = margem([1.85, 1.85])
    assert 1.07 < m < 1.09
