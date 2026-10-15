"""Testes do filtro M6 — 8 pontos."""
from __future__ import annotations

from apostas.alertas.filtros import ContextoFiltro, passa_filtros_m6


def _ctx_ok(**over):
    """Contexto que passa todos os pontos — override com kwargs."""
    base = dict(
        ev=0.05, odd=2.00,
        mercado="golos", liga="UEFA Europa League",
        dados_recentes=True, onze_confirmado=True,
        momento="abertura",
        limite_casa_chf=100.0, stake_pretendida_chf=5.0,
        carteira_pior_perda_pct=0.02, stop_loss_hoje_atingido=False,
        is_live=False, latencia_segundos=None, sem_suspensao=True,
        casa_limitada=False, ev_bom_noutra_casa=False,
    )
    base.update(over)
    return ContextoFiltro(**base)


def test_ok_passa_todos():
    r = passa_filtros_m6(_ctx_ok())
    assert r.passou
    assert r.pontos_falhados == []


def test_p1_ev_baixo_para_odd_normal():
    r = passa_filtros_m6(_ctx_ok(ev=0.02, odd=2.00))
    assert not r.passou
    assert any("P1" in p for p in r.pontos_falhados)


def test_p1_ev_mais_rigoroso_para_odd_baixa():
    """Odd 1.40 exige EV 5% min; 3% nao passa."""
    r = passa_filtros_m6(_ctx_ok(ev=0.03, odd=1.40))
    assert not r.passou
    assert any("P1" in p for p in r.pontos_falhados)


def test_p1_ev_5pct_passa_para_odd_baixa():
    r = passa_filtros_m6(_ctx_ok(ev=0.05, odd=1.40))
    assert r.passou


def test_p2_liga_fraca_bloqueia():
    r = passa_filtros_m6(_ctx_ok(liga="Premier League"))
    assert not r.passou
    assert any("P2" in p for p in r.pontos_falhados)


def test_p2_mercado_fraco_1x2_bloqueia():
    r = passa_filtros_m6(_ctx_ok(mercado="1x2"))
    assert not r.passou
    assert any("P2" in p for p in r.pontos_falhados)


def test_p3_dados_antigos_bloqueia():
    r = passa_filtros_m6(_ctx_ok(dados_recentes=False))
    assert not r.passou
    assert any("P3" in p for p in r.pontos_falhados)


def test_p3_sem_onze_so_avisa():
    r = passa_filtros_m6(_ctx_ok(onze_confirmado=False))
    assert r.passou  # aviso, nao bloqueia
    assert any("P3" in a for a in r.pontos_avisos)


def test_p4_tarde_bloqueia():
    r = passa_filtros_m6(_ctx_ok(momento="tarde"))
    assert not r.passou


def test_p5_limite_casa_abaixo_de_stake():
    r = passa_filtros_m6(_ctx_ok(limite_casa_chf=3.0, stake_pretendida_chf=5.0))
    assert not r.passou
    assert any("P5" in p for p in r.pontos_falhados)


def test_p6_stop_loss_bloqueia():
    r = passa_filtros_m6(_ctx_ok(stop_loss_hoje_atingido=True))
    assert not r.passou
    assert any("P6" in p for p in r.pontos_falhados)


def test_p6_carteira_pior_perda_5pct_bloqueia():
    r = passa_filtros_m6(_ctx_ok(carteira_pior_perda_pct=0.05))
    assert not r.passou
    assert any("P6" in p for p in r.pontos_falhados)


def test_p7_live_latencia_alta_bloqueia():
    r = passa_filtros_m6(_ctx_ok(is_live=True, latencia_segundos=15))
    assert not r.passou
    assert any("P7" in p for p in r.pontos_falhados)


def test_p7_live_suspenso_bloqueia():
    r = passa_filtros_m6(_ctx_ok(is_live=True, latencia_segundos=5, sem_suspensao=False))
    assert not r.passou


def test_p8_casa_limitada_e_ev_pequeno_bloqueia():
    r = passa_filtros_m6(_ctx_ok(ev=0.05, casa_limitada=True))
    assert not r.passou
    assert any("P8" in p for p in r.pontos_falhados)


def test_p8_casa_limitada_mas_ev_grande_passa():
    r = passa_filtros_m6(_ctx_ok(ev=0.10, casa_limitada=True))
    assert r.passou


def test_razao_devolve_motivos():
    r = passa_filtros_m6(_ctx_ok(ev=0.01, liga="Premier League"))
    razao = r.razao()
    assert "P1" in razao
    assert "P2" in razao
