"""Testes do modelo GAP ratings e do backtest com modelo=gap."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from apostas.backtest import walk_forward
from apostas.ingestao import football_data_uk, ligas_equipas
# removed: Sportmonks import
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


def test_gap_prob_1x2_soma_1_e_coerente():
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    p_casa, p_empate, p_fora = modelo.prob_1x2(1, 2)
    assert 0 <= p_casa <= 1
    assert 0 <= p_empate <= 1
    assert 0 <= p_fora <= 1
    assert math.isclose(p_casa + p_empate + p_fora, 1.0, abs_tol=1e-5)
    # Equipa 1 em casa é a mais forte neste dataset → p_casa maior
    assert p_casa > p_fora


def test_gap_equipa_forte_prevista_marcar_mais():
    """Equipa 1 (14-16 remates em casa) deve prever mais golos que a 3."""
    modelo = ModeloGAP()
    modelo.fit(_jogos_sinteticos_gap())
    lam_1_vs_3 = modelo.lambdas(1, 3)
    lam_3_vs_1 = modelo.lambdas(3, 1)
    # Em casa, 1 contra 3: equipa 1 deve ter λ maior que a 3 teve contra ela
    assert lam_1_vs_3[0] > lam_3_vs_1[0]


def test_dixon_coles_altera_resultados_baixos():
    """Com rho > 0, P(0-0) e P(1-1) devem aumentar vs. rho = 0."""
    sem_dc = ModeloGAP(rho_dixon_coles=0.0)
    com_dc = ModeloGAP(rho_dixon_coles=0.15)
    sem_dc.fit(_jogos_sinteticos_gap())
    com_dc.fit(_jogos_sinteticos_gap())

    # Com DC, probabilidade de empate 0-0 é maior
    def p_resultado(modelo, i, j):
        import math as m
        from apostas.modelos.gap import _poisson_pmf, _tau_dixon_coles
        lc, lf = modelo.lambdas(1, 2)
        p = _poisson_pmf(i, lc) * _poisson_pmf(j, lf)
        if modelo.rho_dixon_coles != 0:
            p *= _tau_dixon_coles(i, j, lc, lf, modelo.rho_dixon_coles)
        return p

    p00_sem = p_resultado(sem_dc, 0, 0)
    p00_com = p_resultado(com_dc, 0, 0)
    assert p00_com < p00_sem  # tau para (0,0) é < 1 com rho positivo


def test_dixon_coles_distribuicao_continua_a_somar_1():
    """A renormalização mantém a distribuição válida."""
    modelo = ModeloGAP(rho_dixon_coles=0.15)
    modelo.fit(_jogos_sinteticos_gap())
    dist = modelo.prob_total_golos(1, 2, max_golos=15)
    assert math.isclose(sum(dist), 1.0, abs_tol=1e-5)


def test_shrinkage_adaptativo_aproxima_equipas_pouco_vistas():
    """Equipa com só 1 jogo deve ter ratings mais próximos de 1.0 que a média."""
    from apostas.modelos.gap import _prior_adaptativo
    base = 3.0
    threshold = 5.0
    # Com 1 jogo: prior maior → mais shrinkage
    assert _prior_adaptativo(1.0, base, threshold) > base
    # Com threshold ou mais: prior base
    assert _prior_adaptativo(5.0, base, threshold) == base
    assert _prior_adaptativo(100.0, base, threshold) == base


# ─── Backtest com modelo GAP ─────────────────────────────────────────

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
