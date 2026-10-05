"""Testes do modelo GAP ratings e do backtest com modelo=gap."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from apostas.backtest import walk_forward
from apostas.ingestao import football_data_uk, jogos_sportmonks, ligas_equipas
from apostas.ingestao.sportmonks import cliente
from apostas.modelos.gap import JogoHistoricoGAP, ModeloGAP
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


def _jogos_sinteticos_gap():
    """Dataset mínimo: 3 equipas, 6 jogos (round-robin de ida/volta)."""
    base = datetime(2024, 1, 1)
    pares = [
        # (casa, fora, remates_c, remates_f, cantos_c, cantos_f, golos_c, golos_f)
        (1, 2, 14, 8, 6, 3, 2, 1),
        (1, 3, 16, 6, 7, 2, 3, 0),
        (2, 1, 10, 12, 4, 5, 1, 1),
        (2, 3, 12, 7, 5, 3, 2, 2),
        (3, 1, 7, 15, 2, 6, 0, 2),
        (3, 2, 8, 11, 3, 4, 1, 1),
    ]
    out = []
    for i, (c, f, rc, rf, cc, cf, gc, gf) in enumerate(pares):
        out.append(JogoHistoricoGAP(
            data=base + timedelta(days=i),
            liga_id=1, casa_id=c, fora_id=f,
            remates_casa=rc, remates_fora=rf,
            cantos_casa=cc, cantos_fora=cf,
            golos_casa=gc, golos_fora=gf,
        ))
    return out


def test_fit_gap_requer_jogos():
    with pytest.raises(ValueError):
        ModeloGAP().fit([])


def test_gap_calcula_medias_por_liga():
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    p = modelo.parametros
    assert 1 in p.media_atq_casa_liga
    assert p.media_atq_casa_liga[1] > 0
    assert p.media_atq_fora_liga[1] > 0
    assert p.taxa_golos_por_atq_casa[1] > 0


def test_gap_prob_over_entre_0_e_1():
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    p = modelo.prob_over(casa_id=1, fora_id=2, linha=2.5)
    assert 0.0 <= p <= 1.0


def test_gap_dist_total_soma_perto_de_1():
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    dist = modelo.prob_total_golos(1, 2, max_golos=15)
    assert math.isclose(sum(dist), 1.0, abs_tol=1e-5)


def test_gap_equipa_forte_prevista_marcar_mais():
    """Equipa 1 (14-16 remates em casa) deve prever mais golos que a 3."""
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    lam_1_vs_3 = modelo.lambdas(1, 3)
    lam_3_vs_1 = modelo.lambdas(3, 1)
    # Em casa, 1 contra 3: equipa 1 deve ter λ maior que a 3 teve contra ela
    assert lam_1_vs_3[0] > lam_3_vs_1[0]


# ─── Backtest com modelo GAP ─────────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))
    jogos_sportmonks.sincronizar(epocas_atras=2)
    football_data_uk.sincronizar(epocas=[2023, 2024])
    yield bd
    db_mod.reset_engine()


def test_backtest_gap_corre_sem_erros(bd_populada):
    rel = walk_forward.correr(
        epoca_teste=2024, linha=2.5, ev_minimo=0.03,
        meia_vida_dias=365, modelo="gap",
    )
    assert rel.modelo == "gap"
    assert rel.n_apostas >= 0
    # Deve ter apostas (há stats + odds para a época 2024)
    assert rel.n_apostas > 0


def test_backtest_poisson_ainda_corre(bd_populada):
    rel = walk_forward.correr(epoca_teste=2024, modelo="poisson")
    assert rel.modelo == "poisson"
    assert rel.n_apostas > 0


def test_modelo_invalido_levanta(bd_populada):
    with pytest.raises(ValueError, match="desconhecido"):
        walk_forward.correr(epoca_teste=2024, modelo="xyz")
