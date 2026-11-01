"""Blacklist dinamica por (liga, mercado) com ROI historico negativo.

Caso real: Ricardo recebeu alertas de 2a Bundesliga com ROI -30% e -61%
que foram para Telegram apesar do historico ser mau. Novo filtro:
10 alertas + ROI <= -5% bloqueia.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    scn._blacklist_cache.clear()
    scn._perfis_cache = None
    yield
    scn._blacklist_cache.clear()
    scn._perfis_cache = None


def _agg(n_resolvidos, roi):
    return SimpleNamespace(n_resolvidos=n_resolvidos, roi=roi)


def _instalar_perfis(monkeypatch, mapa):
    """mapa = {(liga, merc_key): agg_namespace}"""
    import apostas.alertas.historico_alertas as ha
    monkeypatch.setattr(ha, "carregar_perfis", lambda p: {"dummy": True})
    monkeypatch.setattr(ha, "agregar_por_mercado", lambda perfis: mapa)


def test_roi_fortemente_negativo_com_volume_bloqueia(monkeypatch):
    """2a Bundesliga/remates: -30% em 16 → bloqueia."""
    _instalar_perfis(monkeypatch, {
        ("2. Bundesliga", "remates"): _agg(16, -30.2),
    })
    assert scn._blacklist_roi_bloqueia("2. Bundesliga", "remates") is True


def test_sot_normalizado_para_mesmo_perfil(monkeypatch):
    """mercado 'sot' e 'remates_a_baliza' usam a mesma chave 'sot'."""
    _instalar_perfis(monkeypatch, {
        ("2. Bundesliga", "sot"): _agg(11, -61.5),
    })
    assert scn._blacklist_roi_bloqueia("2. Bundesliga", "sot") is True
    scn._blacklist_cache.clear()
    assert scn._blacklist_roi_bloqueia("2. Bundesliga", "remates_a_baliza") is True


def test_volume_abaixo_do_limiar_nao_bloqueia(monkeypatch):
    """9 alertas → ainda nao ha volume suficiente."""
    _instalar_perfis(monkeypatch, {
        ("Liga X", "cantos"): _agg(9, -30.0),
    })
    assert scn._blacklist_roi_bloqueia("Liga X", "cantos") is False


def test_roi_positivo_nao_bloqueia(monkeypatch):
    _instalar_perfis(monkeypatch, {
        ("Serie A", "cantos"): _agg(80, +12.4),
    })
    assert scn._blacklist_roi_bloqueia("Serie A", "cantos") is False


def test_roi_levemente_negativo_mas_acima_do_limiar(monkeypatch):
    """-4% com volume: nao bate -5% → nao bloqueia (ainda tem edge marginal)."""
    _instalar_perfis(monkeypatch, {
        ("Premier League", "golos"): _agg(50, -4.0),
    })
    assert scn._blacklist_roi_bloqueia("Premier League", "golos") is False


def test_roi_exactamente_minus_5_bloqueia(monkeypatch):
    """-5% exact → bloqueia (limite inclusivo)."""
    _instalar_perfis(monkeypatch, {
        ("LaLiga", "cards"): _agg(10, -5.0),
    })
    assert scn._blacklist_roi_bloqueia("LaLiga", "cards") is True


def test_liga_sem_historico_nao_bloqueia(monkeypatch):
    _instalar_perfis(monkeypatch, {})  # sem entry para esta liga
    assert scn._blacklist_roi_bloqueia("Liga Nova", "golos") is False


def test_sem_perfis_nao_bloqueia(monkeypatch):
    import apostas.alertas.historico_alertas as ha
    monkeypatch.setattr(ha, "carregar_perfis", lambda p: {})
    monkeypatch.setattr(ha, "agregar_por_mercado", lambda perfis: {})
    assert scn._blacklist_roi_bloqueia("qualquer", "golos") is False


def test_cache_poupa_recarregar(monkeypatch):
    """Segunda chamada mesma chave usa a cache, nao chama perfis outra vez."""
    chamadas = []
    import apostas.alertas.historico_alertas as ha

    def fake_carregar(p):
        chamadas.append(1)
        return {"x": True}

    monkeypatch.setattr(ha, "carregar_perfis", fake_carregar)
    monkeypatch.setattr(ha, "agregar_por_mercado",
                         lambda perfis: {("A", "golos"): _agg(20, -10.0)})
    scn._blacklist_roi_bloqueia("A", "golos")
    scn._blacklist_roi_bloqueia("A", "golos")
    assert len(chamadas) == 1


def test_resetar_cache_apaga(monkeypatch):
    _instalar_perfis(monkeypatch, {
        ("X", "golos"): _agg(20, -10.0),
    })
    assert scn._blacklist_roi_bloqueia("X", "golos") is True
    assert ("X", "golos", None) in scn._blacklist_cache
    scn._resetar_blacklist_cache()
    assert scn._blacklist_cache == {}


def test_limiares_sao_10_e_minus_5():
    """Confirma os valores actuais; qualquer mudanca precisa de teste novo."""
    assert scn._BLACKLIST_ROI_LIMIAR_N == 10
    assert scn._BLACKLIST_ROI_LIMIAR_ROI == -5.0
