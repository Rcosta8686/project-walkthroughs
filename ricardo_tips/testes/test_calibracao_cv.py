"""Testes de calibração e cross-validation."""

from __future__ import annotations

from datetime import datetime

import pytest

from apostas.backtest import calibracao as calib
from apostas.backtest.walk_forward import ApostaSimulada
from apostas.ingestao import football_data_uk, ligas_equipas
# removed: Sportmonks import
from apostas.modelos import cross_val
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


def _apostas_sinteticas(n_ganhas: int, n_perdidas: int, prob: float) -> list[ApostaSimulada]:
    base = datetime(2024, 8, 1)
    out = []
    for i in range(n_ganhas):
        out.append(ApostaSimulada(
            data=base, liga_id=1, jogo_id=i, casa_id=1, fora_id=2,
            lado="over", linha=2.5, prob_modelo=prob, odd=1.9, ev=0.14,
            stake=1.0, resultado="ganho", lucro=0.9,
        ))
    for i in range(n_perdidas):
        out.append(ApostaSimulada(
            data=base, liga_id=1, jogo_id=100 + i, casa_id=1, fora_id=2,
            lado="over", linha=2.5, prob_modelo=prob, odd=1.9, ev=0.14,
            stake=1.0, resultado="perdido", lucro=-1.0,
        ))
    return out


def test_calibrar_devolve_bin_com_hit_rate():
    apostas = _apostas_sinteticas(n_ganhas=6, n_perdidas=4, prob=0.60)
    bins = calib.calibrar(apostas)
    assert len(bins) == 1
    b = bins[0]
    assert b.n_apostas == 10
    assert b.hit_rate_real == pytest.approx(0.6)
    assert b.prob_media_modelo == pytest.approx(0.6)


def test_brier_score_perfeito_e_pessimo():
    # Modelo prevê 100% e acerta sempre → Brier = 0
    perfeito = _apostas_sinteticas(n_ganhas=5, n_perdidas=0, prob=1.0)
    assert calib.brier_score(perfeito) == pytest.approx(0.0, abs=1e-6)
    # Modelo prevê 0% e acerta sempre → Brier = 1 (péssimo)
    pessimo = _apostas_sinteticas(n_ganhas=5, n_perdidas=0, prob=0.0)
    assert calib.brier_score(pessimo) == pytest.approx(1.0, abs=1e-6)


def test_log_loss_perfeito_e_evitar_inf():
    perfeito = _apostas_sinteticas(n_ganhas=5, n_perdidas=0, prob=0.999999)
    assert calib.log_loss(perfeito) >= 0
    assert calib.log_loss(perfeito) < 0.01

    # Com prob 1.0 teríamos log(0) para perdas; a clipagem evita isso
    valor = calib.log_loss(_apostas_sinteticas(0, 1, 1.0))
    assert valor > 0 and valor < float("inf")


def test_calibrar_vazio():
    assert calib.calibrar([]) == []
    assert calib.brier_score([]) == 0
    assert calib.log_loss([]) == 0


# ─── Cross-validation end-to-end ────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar()
    football_data_uk.sincronizar(epocas=[2023, 2024])
    yield bd
    db_mod.reset_engine()


def test_grid_search_devolve_resultados_ordenados(bd_populada):
    resultados = cross_val.grid_search(
        epoca_teste=2024,
        pesos_cantos=[0.3, 0.5],
        rhos=[0.0, 0.1],
    )
    assert len(resultados) == 4  # 2 × 2
    # Ordenados por ROI decrescente
    rois = [r.roi for r in resultados]
    assert rois == sorted(rois, reverse=True)
    # Cada resultado tem métricas populadas
    for r in resultados:
        assert r.n_apostas > 0
        assert r.brier >= 0
        assert r.log_loss >= 0


def test_formatar_tabela_contem_header_e_melhor(bd_populada):
    resultados = cross_val.grid_search(
        epoca_teste=2024, pesos_cantos=[0.5], rhos=[0.1],
    )
    texto = cross_val.formatar_tabela(resultados)
    assert "CROSS-VALIDATION" in texto
    assert "Melhor combo" in texto
