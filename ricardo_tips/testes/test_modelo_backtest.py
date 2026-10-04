"""Testes do modelo Poisson, do motor de value e do backtest end-to-end."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from apostas.backtest import walk_forward
from apostas.ingestao import football_data_uk, ligas_equipas
from apostas.ingestao.api_football import cliente
from apostas.modelos import value
from apostas.modelos.poisson import JogoHistorico, ModeloPoisson
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


# ─── value ──────────────────────────────────────────────────────────

def test_ev_positivo_quando_prob_vence_odd():
    assert value.ev(0.55, 2.0) == pytest.approx(0.10)


def test_ev_negativo_quando_prob_fica_curta():
    assert value.ev(0.40, 2.0) == pytest.approx(-0.20)


def test_overround_tipico_bookmaker():
    # 1.91 em cada lado → ~4.7% de margem
    assert value.overround([1.91, 1.91]) == pytest.approx(0.047, abs=1e-3)


def test_probs_implicitas_somam_1():
    probs = value.probs_implicitas([2.0, 2.0, 4.0])
    assert sum(probs) == pytest.approx(1.0)


def test_ha_valor_filtra_pela_margem():
    assert value.ha_valor(0.55, 2.0, ev_minimo=0.03)
    assert not value.ha_valor(0.51, 2.0, ev_minimo=0.03)


# ─── modelo Poisson ─────────────────────────────────────────────────

def _jogos_sinteticos():
    """Dataset mínimo: 3 equipas, cada uma joga 2x em casa e 2x fora."""
    base = datetime(2024, 1, 1)
    jogos = []
    pares = [
        (1, 2, 2, 1),  # casa > fora
        (1, 3, 3, 0),
        (2, 1, 1, 1),
        (2, 3, 2, 2),
        (3, 1, 0, 2),
        (3, 2, 1, 1),
    ]
    for i, (c, f, gc, gf) in enumerate(pares):
        jogos.append(JogoHistorico(
            data=base + timedelta(days=i),
            liga_id=1, casa_id=c, fora_id=f,
            golos_casa=gc, golos_fora=gf,
        ))
    return jogos


def test_fit_poisson_calcula_medias_por_liga():
    modelo = ModeloPoisson(meia_vida_dias=365)
    modelo.fit(_jogos_sinteticos())
    p = modelo.parametros
    assert 1 in p.media_casa_por_liga
    assert p.media_casa_por_liga[1] > 0
    assert p.media_fora_por_liga[1] > 0


def test_prob_over_entre_0_e_1():
    modelo = ModeloPoisson()
    modelo.fit(_jogos_sinteticos())
    p = modelo.prob_over(casa_id=1, fora_id=2, linha=2.5)
    assert 0.0 <= p <= 1.0


def test_prob_total_golos_soma_perto_de_1():
    modelo = ModeloPoisson()
    modelo.fit(_jogos_sinteticos())
    dist = modelo.prob_total_golos(1, 2, max_golos=15)
    assert math.isclose(sum(dist), 1.0, abs_tol=1e-5)


def test_fit_requer_jogos():
    with pytest.raises(ValueError):
        ModeloPoisson().fit([])


# ─── backtest end-to-end ────────────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))
    football_data_uk.sincronizar(epocas=[2023, 2024])
    yield bd
    db_mod.reset_engine()


def test_backtest_corre_sem_erros(bd_populada):
    rel = walk_forward.correr(
        epoca_teste=2024, linha=2.5, ev_minimo=0.03,
        meia_vida_dias=365,
    )
    # Deve ter corrido sem exceções e produzido algumas apostas
    assert rel.n_apostas >= 0
    # Se produziu apostas, ROI deve ser finito e bankroll coerente
    if rel.n_apostas > 0:
        assert -1.0 <= rel.roi <= 10.0
        assert rel.stake_total == rel.n_apostas  # stake=1 cada
        assert abs(sum(a.lucro for a in rel.apostas) - rel.lucro) < 1e-6


def test_backtest_sem_apostas_quando_ev_altissimo(bd_populada):
    rel = walk_forward.correr(
        epoca_teste=2024, linha=2.5, ev_minimo=10.0,  # 1000% EV impossível
    )
    assert rel.n_apostas == 0
    assert rel.lucro == 0
