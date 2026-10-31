"""Filtros do scanner contra linhas stale e janela intervalo."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scanner_live_v2 import (  # noqa: E402
    _INTERVALO_FIM,
    _INTERVALO_INI,
    _MERCADOS_SEM_INTERVALO,
    _linha_stale_vs_marcador,
)


def _estado(**kw):
    base = dict(
        marcador_casa=0, marcador_fora=0,
        corners_casa=0, corners_fora=0,
        yellow_cards_casa=0, yellow_cards_fora=0,
        shots_total_casa=0, shots_total_fora=0,
        shots_on_target_casa=0, shots_on_target_fora=0,
        minuto=45,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_sot_stale_bologna_vs_monza_4sot_linha_5_5():
    """Caso real: 4 SoT ao HT, linha OVER 5.5 — bookie ja ajustou para 9.5."""
    est = _estado(shots_on_target_casa=3, shots_on_target_fora=1, minuto=45)
    assert _linha_stale_vs_marcador("remates_a_baliza", "over", 5.5, est) is True


def test_sot_linha_longe_passa():
    est = _estado(shots_on_target_casa=3, shots_on_target_fora=1)
    # 4 actual, linha 9.5 → faltam 5.5 > margem 3 → passa
    assert _linha_stale_vs_marcador("remates_a_baliza", "over", 9.5, est) is False


def test_under_nao_bloqueado_pelo_filtro_stale():
    """UNDER com linha perto nao e snapshot stale (mercado nao ajustou tanto)."""
    est = _estado(shots_on_target_casa=3, shots_on_target_fora=1)
    assert _linha_stale_vs_marcador("remates_a_baliza", "under", 5.5, est) is False


def test_cantos_stale_8_atuais_linha_9_5():
    est = _estado(corners_casa=5, corners_fora=3)
    # 8 actual, linha 9.5 → faltam 1.5 < margem 3 → stale
    assert _linha_stale_vs_marcador("cantos", "over", 9.5, est) is True


def test_cantos_linha_longe_passa():
    est = _estado(corners_casa=5, corners_fora=3)
    assert _linha_stale_vs_marcador("cantos", "over", 11.5, est) is False


def test_golos_margem_pequena():
    est = _estado(marcador_casa=2, marcador_fora=0)
    # 2 actual, linha 2.5 → faltam 0.5 ≥ margem 0.5 → passa (golos movem-se devagar)
    assert _linha_stale_vs_marcador("golos", "over", 2.5, est) is False
    # 2 actual, linha 2.0 → faltam 0 < 0.5 → stale
    assert _linha_stale_vs_marcador("golos", "over", 2.0, est) is True


def test_cartoes_margem():
    est = _estado(yellow_cards_casa=3, yellow_cards_fora=2)
    # 5 actual, linha 5.5 → faltam 0.5 < margem 1.5 → stale
    assert _linha_stale_vs_marcador("cartoes", "over", 5.5, est) is True
    # 5 actual, linha 6.5 → faltam 1.5 ≥ margem 1.5 → passa (limite)
    assert _linha_stale_vs_marcador("cartoes", "over", 6.5, est) is False


def test_remates_margem_5():
    est = _estado(shots_total_casa=8, shots_total_fora=6)
    # 14 actual, linha 15.5 → faltam 1.5 < 5 → stale
    assert _linha_stale_vs_marcador("remates", "over", 15.5, est) is True
    # 14 actual, linha 20.5 → faltam 6.5 ≥ 5 → passa
    assert _linha_stale_vs_marcador("remates", "over", 20.5, est) is False


def test_mercado_desconhecido_nao_rejeita():
    est = _estado()
    assert _linha_stale_vs_marcador("btts", "over", 0.5, est) is False


def test_linha_none_nao_rejeita():
    est = _estado()
    assert _linha_stale_vs_marcador("remates_a_baliza", "over", None, est) is False


def test_mercados_sem_intervalo_contem_dinamicos():
    assert "sot" in _MERCADOS_SEM_INTERVALO
    assert "remates_a_baliza" in _MERCADOS_SEM_INTERVALO
    assert "cantos" in _MERCADOS_SEM_INTERVALO
    assert "cartoes" in _MERCADOS_SEM_INTERVALO
    # Golos e BTTS passam no HT (movem-se devagar)
    assert "golos" not in _MERCADOS_SEM_INTERVALO
    assert "btts" not in _MERCADOS_SEM_INTERVALO


def test_janela_intervalo_cobre_40_a_50():
    assert _INTERVALO_INI == 40
    assert _INTERVALO_FIM == 50
