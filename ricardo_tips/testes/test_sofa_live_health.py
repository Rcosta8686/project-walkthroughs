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
    scn._sofa_live_ciclos_falha = 0
    scn._sofa_live_alerta_enviado_dia = None
    yield
    scn._sofa_live_ciclos_falha = 0
    scn._sofa_live_alerta_enviado_dia = None


def test_ciclo_com_jogos_zera_contador(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    scn._sofa_live_ciclos_falha = 3
    scn._actualizar_health_sofa_live(sofa_ok=True, enviar_telegram=False)
    assert scn._sofa_live_ciclos_falha == 0


def test_fora_de_janela_nao_conta(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: False)
    scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=False)
    assert scn._sofa_live_ciclos_falha == 0


def test_na_janela_sem_jogos_incrementa(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=False)
    scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=False)
    assert scn._sofa_live_ciclos_falha == 2


def test_abaixo_limiar_nao_alerta(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS - 1):
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
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
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
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
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
    assert len(enviadas) == 1
    # Mais 10 ciclos sem jogos no mesmo dia - nao reenvia
    for _ in range(10):
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
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
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
    assert len(enviadas) == 1
    # Vem jogos
    scn._actualizar_health_sofa_live(sofa_ok=True, enviar_telegram=True)
    assert scn._sofa_live_ciclos_falha == 0
    # Depois volta a cair — mesmo dia, nao alerta novamente
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=True)
    assert len(enviadas) == 1


def test_sem_telegram_nao_lanca(monkeypatch):
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    for _ in range(scn._SOFA_LIVE_HEALTH_LIMIAR_CICLOS):
        scn._actualizar_health_sofa_live(sofa_ok=False, enviar_telegram=False)
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


def test_zero_jogos_com_resposta_valida_nao_alerta(monkeypatch):
    """Caso real 03/11 23h: jogos acabaram, Sofascore responde bem com 0 jogos.
    Nao e 'cego' — nao pode alertar."""
    monkeypatch.setattr(scn, "_em_janela_jogos_pt", lambda: True)
    enviadas = []
    monkeypatch.setattr(
        scn, "telegram_cliente",
        lambda: type("C", (), {"enviar": lambda self, m: enviadas.append(m)})(),
    )
    for _ in range(20):
        scn._actualizar_health_sofa_live(sofa_ok=True, enviar_telegram=True)
    assert enviadas == []
    assert scn._sofa_live_ciclos_falha == 0


def _fake_resp(status, payload):
    return type("R", (), {"status": status, "json": lambda self: payload})()


def test_listar_live_flag_ok_com_zero_jogos(monkeypatch):
    from apostas.ingestao import sofascore_live as sl
    monkeypatch.setattr(sl, "_circuit_pode_pedir", lambda: True)
    monkeypatch.setattr(sl._flaresolverr, "get",
                        lambda *a, **k: _fake_resp(200, {"events": []}))
    assert sl.listar_jogos_live(ids_ligas=(17,)) == []
    assert sl.ultima_listagem_live_ok is True


def test_listar_live_flag_falha_com_bloqueio_cloudflare(monkeypatch):
    from apostas.ingestao import sofascore_live as sl
    monkeypatch.setattr(sl, "_circuit_pode_pedir", lambda: True)
    monkeypatch.setattr(sl._flaresolverr, "get",
                        lambda *a, **k: _fake_resp(200, {"error": {"code": 403}}))
    assert sl.listar_jogos_live() == []
    assert sl.ultima_listagem_live_ok is False


def test_listar_live_flag_falha_com_http_500(monkeypatch):
    from apostas.ingestao import sofascore_live as sl
    monkeypatch.setattr(sl, "_circuit_pode_pedir", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("500 Server Error")
    monkeypatch.setattr(sl._flaresolverr, "get", boom)
    assert sl.listar_jogos_live() == []
    assert sl.ultima_listagem_live_ok is False
