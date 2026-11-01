"""Cooldown ignora a linha: OVER 6.5 e OVER 8.5 do mesmo jogo+mercado+lado
sao o mesmo sinal — nao deve haver 2 alertas em 30 min."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    scn._cooldown.clear()
    yield
    scn._cooldown.clear()


def test_cooldown_bloqueia_mesma_linha():
    scn._marcar_enviado("ev1", "sot", "over", 6.5)
    assert scn._em_cooldown("ev1", "sot", "over", 6.5) is True


def test_cooldown_bloqueia_linha_diferente_mesmo_jogo():
    """Caso real do Ricardo: OVER 6.5 remates e OVER 8.5 remates do
    mesmo Frosinone-Torino sao o mesmo sinal."""
    scn._marcar_enviado("ev_frosinone", "remates_a_baliza", "over", 6.5)
    # Nova linha mesmo jogo, mesmo mercado, mesmo lado → DEVE bloquear
    assert scn._em_cooldown(
        "ev_frosinone", "remates_a_baliza", "over", 8.5) is True
    # Mesmo mercado sem linha explicita → tambem bloqueia
    assert scn._em_cooldown(
        "ev_frosinone", "remates_a_baliza", "over") is True


def test_cooldown_nao_bloqueia_mercado_diferente():
    scn._marcar_enviado("ev1", "sot", "over", 6.5)
    assert scn._em_cooldown("ev1", "cantos", "over", 9.5) is False


def test_cooldown_nao_bloqueia_lado_diferente():
    scn._marcar_enviado("ev1", "golos", "over", 2.5)
    assert scn._em_cooldown("ev1", "golos", "under", 2.5) is False


def test_cooldown_nao_bloqueia_jogo_diferente():
    scn._marcar_enviado("ev1", "sot", "over", 6.5)
    assert scn._em_cooldown("ev2", "sot", "over", 6.5) is False


def test_cooldown_30_minutos_default():
    assert scn.COOLDOWN_MIN == 30


def test_cooldown_expira_apos_janela():
    scn._marcar_enviado("ev1", "sot", "over", 6.5)
    # Simula passagem de 31 min no dict
    key = ("ev1", "sot", "over")
    scn._cooldown[key] = datetime.utcnow() - timedelta(minutes=31)
    assert scn._em_cooldown("ev1", "sot", "over") is False


def test_purgar_remove_entradas_antigas():
    scn._marcar_enviado("ev1", "sot", "over", 6.5)
    # Forca entrada velha
    key = ("ev1", "sot", "over")
    scn._cooldown[key] = datetime.utcnow() - timedelta(minutes=60)
    scn._purgar_cooldown()
    assert key not in scn._cooldown
