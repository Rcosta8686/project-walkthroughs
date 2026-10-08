"""Testes para o módulo de calibração."""

from __future__ import annotations

import random

import pytest

from apostas.modelos.calibracao import Calibrador


def _dataset_sintetico(n: int = 500, bias: float = 0.0, seed: int = 42):
    """Gera probs do modelo + labels com um bias controlado.

    bias=0: modelo bem calibrado (prob = taxa real).
    bias=0.2: modelo sobrestima — label=1 ocorre com prob (p - 0.2).
    """
    rng = random.Random(seed)
    probs = [rng.uniform(0.1, 0.9) for _ in range(n)]
    labels = [1 if rng.random() < max(0.0, min(1.0, p - bias)) else 0 for p in probs]
    return probs, labels


def test_nenhuma_devolve_identidade():
    cal = Calibrador("nenhuma")
    cal.fit([], [])
    assert cal.transform(0.42) == 0.42
    assert cal.transform(0.0) == 0.0
    assert cal.transform(1.0) == 1.0


def test_platt_corrige_sobrestimar():
    probs, labels = _dataset_sintetico(n=500, bias=0.2)
    cal = Calibrador("platt")
    cal.fit(probs, labels)
    # Para prob modelo = 0.6, calibrado deve estar perto de 0.4 (bias=0.2)
    cal_60 = cal.transform(0.6)
    assert cal_60 < 0.6
    assert 0.25 < cal_60 < 0.55


def test_isotonic_corrige_sobrestimar():
    probs, labels = _dataset_sintetico(n=1000, bias=0.15)
    cal = Calibrador("isotonic")
    cal.fit(probs, labels)
    cal_60 = cal.transform(0.6)
    assert cal_60 < 0.6


def test_fit_requer_minimo_de_dados():
    cal = Calibrador("platt")
    with pytest.raises(ValueError):
        cal.fit([0.5] * 10, [1] * 10)


def test_transform_antes_de_fit_erra():
    cal = Calibrador("platt")
    with pytest.raises(RuntimeError):
        cal.transform(0.5)


def test_metodo_invalido_erra():
    cal = Calibrador("xpto")
    with pytest.raises(ValueError):
        cal.fit([0.5] * 100, [1] * 100)
