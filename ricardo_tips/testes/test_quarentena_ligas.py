"""Quarentena de ligas: so top (p1) OU com >= 5 alertas resolvidos passam.

Alem disso, blacklist com granularidade (liga, mercado, lado):
UNDER pode continuar OK enquanto OVER esta a perder dinheiro.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402
from apostas.dados_referencia import ligas_prioritarias as lp  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    scn._blacklist_cache.clear()
    scn._perfis_cache = None
    yield
    scn._blacklist_cache.clear()
    scn._perfis_cache = None


def _agg(n, roi):
    return SimpleNamespace(n_resolvidos=n, roi=roi, n_alertas=n,
                            clv_medio=None, pct_clv_positivo=None,
                            categoria_forte=False, stake_mult=1.0)


def _perfil(n, roi, lado="over"):
    return SimpleNamespace(
        liga="X", mercado="golos", lado=lado,
        n_alertas=n, n_resolvidos=n, roi=roi,
        clv_medio=None, pct_clv_positivo=None,
        categoria_forte=False, stake_mult=1.0,
    )


def _instalar(monkeypatch, perfis=None, agg=None):
    import apostas.alertas.historico_alertas as ha
    monkeypatch.setattr(ha, "carregar_perfis",
                         lambda p: perfis if perfis is not None else {})
    monkeypatch.setattr(ha, "agregar_por_mercado",
                         lambda p: agg if agg is not None else {})


# ─── nome_e_prioridade_1 ─────────────────────────────────────────────────

def test_premier_league_e_top():
    assert lp.nome_e_prioridade_1("Premier League") is True
    assert lp.nome_e_prioridade_1("Premier League (ENG)") is True
    assert lp.nome_e_prioridade_1("PREMIER LEAGUE") is True


def test_laliga_e_top():
    assert lp.nome_e_prioridade_1("LaLiga") is True
    assert lp.nome_e_prioridade_1("La Liga") is True
    assert lp.nome_e_prioridade_1("La Liga Santander") is True


def test_bundesliga_1_e_top_mas_2_nao():
    assert lp.nome_e_prioridade_1("Bundesliga") is True
    assert lp.nome_e_prioridade_1("2. Bundesliga") is False
    assert lp.nome_e_prioridade_1("Bundesliga 2") is False


def test_serie_a_e_top_mas_b_nao():
    assert lp.nome_e_prioridade_1("Serie A") is True
    assert lp.nome_e_prioridade_1("Serie A (ITA)") is True
    assert lp.nome_e_prioridade_1("Serie B") is False
    assert lp.nome_e_prioridade_1("Serie B (ITA)") is False


def test_ligue_2_nao_e_top():
    assert lp.nome_e_prioridade_1("Ligue 1") is True
    assert lp.nome_e_prioridade_1("Ligue 1 (FRA)") is True
    assert lp.nome_e_prioridade_1("Ligue 2") is False


def test_liga_portugal_2_nao_e_top():
    assert lp.nome_e_prioridade_1("Liga Portugal") is True
    assert lp.nome_e_prioridade_1("Liga Portugal Betclic") is True
    assert lp.nome_e_prioridade_1("Liga Portugal 2") is False


def test_eredivisie_e_top_mas_eerste_nao():
    assert lp.nome_e_prioridade_1("Eredivisie") is True
    assert lp.nome_e_prioridade_1("Eerste Divisie") is False


def test_championship_nao_e_top():
    assert lp.nome_e_prioridade_1("Championship") is False
    assert lp.nome_e_prioridade_1("Championship (ENG)") is False


def test_women_filtradas_mesmo_com_premier_no_nome():
    assert lp.nome_e_prioridade_1("Women's Premier League") is False


def test_youth_filtrados():
    assert lp.nome_e_prioridade_1("Serie A U21") is False
    assert lp.nome_e_prioridade_1("Premier League U18") is False


def test_liga_desconhecida_nao_e_top():
    assert lp.nome_e_prioridade_1("Brasileirao") is False
    assert lp.nome_e_prioridade_1("MLS") is False
    assert lp.nome_e_prioridade_1("") is False


# ─── _liga_top_ou_com_historico ──────────────────────────────────────────

def test_liga_top_sem_historico_passa(monkeypatch):
    _instalar(monkeypatch, agg={})
    assert scn._liga_top_ou_com_historico("Premier League", "golos") is True


def test_liga_nao_top_sem_historico_bloqueia(monkeypatch):
    _instalar(monkeypatch, agg={})
    assert scn._liga_top_ou_com_historico("Brasileirao", "golos") is False


def test_liga_nao_top_com_historico_curto_bloqueia(monkeypatch):
    _instalar(monkeypatch, agg={
        ("Brasileirao", "golos"): _agg(4, +15.0),
    })
    assert scn._liga_top_ou_com_historico("Brasileirao", "golos") is False


def test_liga_nao_top_com_historico_minimo_passa(monkeypatch):
    _instalar(monkeypatch, agg={
        ("Brasileirao", "golos"): _agg(5, +15.0),
    })
    assert scn._liga_top_ou_com_historico("Brasileirao", "golos") is True


def test_quarentena_normaliza_sot(monkeypatch):
    _instalar(monkeypatch, agg={
        ("Brasileirao", "sot"): _agg(10, +5.0),
    })
    assert scn._liga_top_ou_com_historico(
        "Brasileirao", "remates_a_baliza") is True


# ─── Blacklist granular por lado ─────────────────────────────────────────

def test_blacklist_lado_especifico_bloqueia_so_esse_lado(monkeypatch):
    """OVER perde (-30%), UNDER ganha (+10%): so OVER bloqueia."""
    _instalar(
        monkeypatch,
        perfis={
            ("Serie A", "golos", "over"): _perfil(20, -30.0, "over"),
            ("Serie A", "golos", "under"): _perfil(20, +10.0, "under"),
        },
        agg={
            # Agregado (sem lado) resulta em misturado — ex: -10%
            ("Serie A", "golos"): _agg(40, -10.0),
        },
    )
    # OVER especifico bloqueia (perfil tem -30%)
    assert scn._blacklist_roi_bloqueia("Serie A", "golos", "over") is True
    # UNDER especifico passa (perfil tem +10%, nao bloqueia o lado)
    # Mas o agregado -10% com 40 resolvidos TAMBEM nao bloqueia o UNDER
    # porque o lado dele vem primeiro.
    assert scn._blacklist_roi_bloqueia("Serie A", "golos", "under") is False


def test_blacklist_sem_lado_cai_para_agregado(monkeypatch):
    _instalar(
        monkeypatch,
        perfis={
            ("Serie A", "golos", "over"): _perfil(20, -30.0, "over"),
        },
        agg={
            ("Serie A", "golos"): _agg(20, -30.0),
        },
    )
    # Sem lado explicito → cai para agregado → bloqueia
    assert scn._blacklist_roi_bloqueia("Serie A", "golos", None) is True


def test_blacklist_lado_sem_perfil_cai_para_agregado(monkeypatch):
    """UNDER sem perfil especifico mas agregado bloqueia."""
    _instalar(
        monkeypatch,
        perfis={
            ("Liga X", "golos", "over"): _perfil(15, -40.0, "over"),
        },
        agg={
            ("Liga X", "golos"): _agg(15, -40.0),
        },
    )
    # UNDER nao tem perfil → cai para agregado → bloqueia
    assert scn._blacklist_roi_bloqueia("Liga X", "golos", "under") is True
