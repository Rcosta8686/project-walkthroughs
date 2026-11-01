"""Testes do health check de Sofascore live no scanner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_estado():
    """Entre testes, reset do estado global."""
    scn._sofa_live_ciclos_zero = 0
    scn._sofa_live_alerta_enviado_dia = None
    yield
    scn._sofa_live_ciclos_zero = 0
    scn._sofa_live_alerta_enviado_dia = None


def test_ciclo_com_jogos_zera_contador(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    scn._sofa_live_ciclos_zero = 3
    scn._actualizar_health_sofa_live(houve_jogos=True, enviar_telegram=False)
    assert scn._sofa_live_ciclos_zero == 0


def test_fora_de_janela_nao_conta(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: False)
    scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=False)
    assert scn._sofa_live_ciclos_zero == 0


def test_na_janela_sem_jogos_incrementa(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=False)
    scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=False)
    assert scn._sofa_live_ciclos_zero == 2


def test_abaixo_limiar_nao_alerta(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS - 1):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert enviadas == []
    assert scn._sofa_live_alerta_enviado_dia is None


def test_atinge_limiar_envia_alerta(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert len(enviadas) == 1
    assert "Sofascore live cego" in enviadas[0]
    assert "Preview das 17h continua" in enviadas[0]


def test_alerta_so_uma_vez_por_dia(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    # Atinge limiar
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert len(enviadas) == 1
    # Mais 10 ciclos sem jogos no mesmo dia - nao reenvia
    for _ in range(10):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert len(enviadas) == 1


def test_reset_ciclos_zera_mas_mantem_marker_dia(monkeypatch):
    """Jogos aparecem no meio: zera ciclos, mas marker do dia fica. Nao reenvia."""
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert len(enviadas) == 1
    # Vem jogos
    scn._actualizar_health_sofa_live(houve_jogos=True, enviar_telegram=True)
    assert scn._sofa_live_ciclos_zero == 0
    # Depois volta a cair — mesmo dia, nao alerta novamente
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=True)
    assert len(enviadas) == 1


def test_sem_telegram_nao_lanca(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(houve_jogos=False, enviar_telegram=False)
    # Nao deve lancar; marker de dia marca
    assert scn._sofa_live_alerta_enviado_dia is not None


def test_janela_pt_default_hora_cobre_16_a_02(monkeypatch):
    """Verificacao da janela: 16h-02h cobre tarde e noite PT."""
    import scanner_live_v2 as s
    # Vamos chamar _em_janela_jogos_pt com datetime mocked
    casos = {
        0: True, 1: True, 2: True,     # 00h-02h
        3: False, 4: False, 15: False,  # madrugada e manha
        16: True, 20: True, 23: True,   # tarde/noite
    }
    import datetime as _dt

    class _FakeDT(_dt.datetime):
        _h = 0

        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 11, 1, cls._h, 0, 0)

    monkeypatch.setattr(s, "datetime", _FakeDT)
    for h, esperado in casos.items():
        _FakeDT._h = h
        assert s._em_janela_jogos_pt() == esperado, f"hora {h}h: esperava {esperado}"
