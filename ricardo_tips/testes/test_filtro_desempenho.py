"""Testes M23 — filtro de desempenho dinamico por (liga, mercado)."""
from __future__ import annotations

import pytest

from apostas.alertas import filtro_desempenho
from apostas.alertas.historico_alertas import (
    PerfilAlerta,
    PerfilMercado,
    agregar_por_mercado,
)


def _p(**kw):
    base = dict(
        liga="L", mercado="golos", lado="over",
        n_alertas=50, n_resolvidos=40,
        clv_medio=0.0, pct_clv_positivo=0.5,
        roi=0.0, categoria_forte=False, stake_mult=1.0,
    )
    base.update(kw)
    return PerfilAlerta(**base)


# ─── Agregacao por mercado ─────────────────────────────────────────

def test_agregar_por_mercado_junta_lados_do_mesmo_mercado():
    perfis = {
        ("Serie A", "golos", "over"): _p(liga="Serie A", lado="over",
                                          n_alertas=60, n_resolvidos=50, roi=10.0),
        ("Serie A", "golos", "under"): _p(liga="Serie A", lado="under",
                                           n_alertas=40, n_resolvidos=30, roi=-5.0),
    }
    agg = agregar_por_mercado(perfis)
    assert ("Serie A", "golos") in agg
    pm = agg[("Serie A", "golos")]
    assert pm.n_alertas == 100
    assert pm.n_resolvidos == 80
    # ROI pesado por n_resolvidos: (10*50 + -5*30) / 80 = 350/80 = 4.375
    assert pm.roi == pytest.approx(4.375, abs=0.01)


def test_agregar_por_mercado_separa_mercados():
    perfis = {
        ("L", "golos", "over"): _p(mercado="golos", n_alertas=10, n_resolvidos=5),
        ("L", "cantos", "over"): _p(mercado="cantos", n_alertas=20, n_resolvidos=10),
    }
    agg = agregar_por_mercado(perfis)
    assert len(agg) == 2
    assert agg[("L", "golos")].n_alertas == 10
    assert agg[("L", "cantos")].n_alertas == 20


# ─── Filtro ────────────────────────────────────────────────────────

def _pm(liga="L", mercado="golos", n_alertas=100, n_resolvidos=80, roi=0.0):
    return PerfilMercado(
        liga=liga, mercado=mercado,
        n_alertas=n_alertas, n_resolvidos=n_resolvidos,
        roi=roi, clv_medio=None,
    )


def test_filtro_nao_bloqueia_sem_dados():
    dec = filtro_desempenho.avaliar(None, "Serie A", "golos")
    assert dec.bloquear is False
    assert "sem dados" in dec.razao


def test_filtro_nao_bloqueia_sem_historico():
    perfis = {("Outra", "cantos"): _pm(roi=-50.0, n_resolvidos=100)}
    dec = filtro_desempenho.avaliar(perfis, "Serie A", "golos")
    assert dec.bloquear is False
    assert "sem historico" in dec.razao


def test_filtro_nao_bloqueia_abaixo_do_volume_minimo():
    perfis = {("L", "golos"): _pm(n_resolvidos=20, roi=-50.0)}
    dec = filtro_desempenho.avaliar(perfis, "L", "golos")
    assert dec.bloquear is False
    assert "volume baixo" in dec.razao


def test_filtro_bloqueia_roi_claramente_negativo():
    """Como MLS cantos (-20% em 432 alertas)."""
    perfis = {("MLS", "cantos"): _pm(liga="MLS", mercado="cantos",
                                      n_resolvidos=432, roi=-20.0)}
    dec = filtro_desempenho.avaliar(perfis, "MLS", "cantos")
    assert dec.bloquear is True
    assert dec.stake_mult == 0.0
    assert "-20" in dec.razao


def test_filtro_meio_stake_quando_roi_entre_negativo_e_limiar():
    """ROI entre -5% e 0% → nao bloqueia mas pede metade."""
    perfis = {("L", "golos"): _pm(n_resolvidos=60, roi=-2.0)}
    dec = filtro_desempenho.avaliar(perfis, "L", "golos")
    assert dec.bloquear is False
    assert dec.stake_mult == 0.5


def test_filtro_permite_passar_quando_roi_positivo():
    """Championship remates +43% em 46 alertas resolvidos."""
    perfis = {("Championship", "remates"): _pm(liga="Championship", mercado="remates",
                                                n_resolvidos=46, roi=43.0)}
    dec = filtro_desempenho.avaliar(perfis, "Championship", "remates")
    assert dec.bloquear is False
    assert dec.stake_mult == 1.0


def test_filtro_case_insensitive_mercado():
    perfis = {("L", "golos"): _pm(n_resolvidos=50, roi=-20.0)}
    dec = filtro_desempenho.avaliar(perfis, "L", "GOLOS")
    assert dec.bloquear is True


def test_bloqueios_atuais_lista_apenas_bloqueados_e_ordenados():
    perfis = {
        ("A", "cantos"): _pm(liga="A", mercado="cantos", n_resolvidos=100, roi=-20.0),
        ("B", "golos"):  _pm(liga="B", mercado="golos", n_resolvidos=50, roi=-57.0),
        ("C", "remates"): _pm(liga="C", mercado="remates", n_resolvidos=50, roi=+30.0),
        ("D", "cantos"): _pm(liga="D", mercado="cantos", n_resolvidos=20, roi=-50.0),  # vol baixo
    }
    lista = filtro_desempenho.bloqueios_atuais(perfis)
    nomes = [(l, m) for (l, m, _, _) in lista]
    assert ("B", "golos") in nomes       # -57% pior
    assert ("A", "cantos") in nomes      # -20%
    assert ("C", "remates") not in nomes
    assert ("D", "cantos") not in nomes  # volume baixo
    # Ordenado por ROI mais negativo primeiro
    assert nomes[0] == ("B", "golos")
