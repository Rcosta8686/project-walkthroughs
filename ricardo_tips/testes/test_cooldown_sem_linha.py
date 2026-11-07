"""Cooldown ignora a linha: OVER 6.5 e OVER 8.5 do mesmo jogo+mercado+lado
sao o mesmo sinal — uma dica por (jogo, mercado, lado), no jogo inteiro."""
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


def test_cooldown_cobre_o_jogo_inteiro():
    assert scn.COOLDOWN_MIN == 180


def test_mesma_dica_mais_tarde_no_jogo_nao_repete():
    """Rennes OVER remates aos 45' e outra vez aos 63' (35 min depois)."""
    key = ("ev1", "remates", "over")
    scn._cooldown[key] = scn.agora_utc() - timedelta(minutes=60)
    assert scn._em_cooldown("ev1", "remates", "over", 18.5) is True


def test_cooldown_expira_apos_janela():
    key = ("ev1", "sot", "over")
    scn._cooldown[key] = scn.agora_utc() - timedelta(minutes=181)
    assert scn._em_cooldown("ev1", "sot", "over") is False


def test_purgar_remove_entradas_antigas():
    key = ("ev1", "sot", "over")
    scn._cooldown[key] = scn.agora_utc() - timedelta(minutes=200)
    scn._purgar_cooldown()
    assert key not in scn._cooldown


def test_restart_a_meio_do_jogo_nao_repete_a_dica(tmp_path, monkeypatch):
    from apostas.relatorios import alertas_enviados as ae
    reg = tmp_path / "env.csv"
    agora = scn.agora_utc()
    ae.registar(7, agora - timedelta(minutes=40), reg, event_id=555, mercado="remates",
                lado="over", linha=16.5, odd=1.45)
    ae.registar(8, agora - timedelta(minutes=300), reg, event_id=556, mercado="golos",
                lado="over", linha=2.5, odd=1.9)                    # jogo antigo
    ae.registar(tipo="projeccao", ts=agora, path=reg, event_id=557, mercado="sot",
                lado="over", linha=9.5, odd=1.5)
    monkeypatch.setattr(ae, "caminho", lambda: reg)
    assert scn._carregar_cooldown_do_registo() == 2
    assert scn._em_cooldown(555, "remates", "over", 20.5) is True
    assert scn._em_cooldown(556, "golos", "over", 2.5) is False
    # a projeccao de SoT bloqueia o alerta de remates a baliza do mesmo jogo
    assert scn._em_cooldown(557, "remates_a_baliza", "over", 7.5) is True


def test_alerta_e_projeccao_da_mesma_aposta_nao_saem_os_dois():
    scn._marcar_enviado(900, "remates_a_baliza", "over", 6.5)
    assert scn._em_cooldown(900, "sot", "OVER") is True
    assert scn._em_cooldown(900, "sot", "under") is False
