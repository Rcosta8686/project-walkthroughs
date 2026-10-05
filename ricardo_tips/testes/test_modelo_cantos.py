"""Testes do modelo de cantos e do backtest com mercado=cantos."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from apostas.backtest import walk_forward
from apostas.ingestao import football_data_uk, ligas_equipas
from apostas.ingestao.sportmonks import cliente
from apostas.modelos.cantos import JogoHistoricoCantos, ModeloCantos
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


def _jogos_cantos():
    base = datetime(2024, 1, 1)
    pares = [
        (1, 2, 7, 4),
        (1, 3, 9, 3),
        (2, 1, 5, 6),
        (2, 3, 8, 5),
        (3, 1, 3, 8),
        (3, 2, 4, 7),
    ]
    return [
        JogoHistoricoCantos(
            data=base + timedelta(days=i), liga_id=1,
            casa_id=c, fora_id=f, cantos_casa=cc, cantos_fora=cf,
        )
        for i, (c, f, cc, cf) in enumerate(pares)
    ]


def test_fit_cantos_requer_jogos():
    with pytest.raises(ValueError):
        ModeloCantos().fit([])


def test_cantos_prob_over_entre_0_e_1():
    m = ModeloCantos()
    m.fit(_jogos_cantos())
    p = m.prob_over(1, 2, linha=9.5)
    assert 0.0 <= p <= 1.0


def test_cantos_distribuicao_soma_1():
    m = ModeloCantos()
    m.fit(_jogos_cantos())
    dist = m.prob_total(1, 2, max_cantos=30)
    assert math.isclose(sum(dist), 1.0, abs_tol=1e-5)


def test_cantos_equipa_que_pressiona_mais_tem_mais_cantos():
    """Equipa 1 (9 cantos em casa) deve prever mais cantos que a 3 (3 em casa)."""
    m = ModeloCantos()
    m.fit(_jogos_cantos())
    lam_1 = m.lambdas(1, 2)
    lam_3 = m.lambdas(3, 2)
    assert lam_1[0] > lam_3[0]


# ─── Backtest end-to-end com mercado=cantos ─────────────────────────

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


def test_backtest_mercado_cantos_corre(bd_populada):
    rel = walk_forward.correr(
        epoca_teste=2024, linha=9.5, ev_minimo=0.03,
        mercado="cantos",
    )
    assert rel.mercado == "cantos"
    assert rel.modelo == "cantos"
    # Pode não haver apostas (football-data mock não gera odds de cantos),
    # mas o loop deve correr sem erros
    assert rel.n_apostas >= 0


def test_mercado_invalido_levanta(bd_populada):
    with pytest.raises(ValueError, match="desconhecido"):
        walk_forward.correr(epoca_teste=2024, mercado="xyz")
