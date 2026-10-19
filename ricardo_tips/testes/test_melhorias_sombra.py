"""Testes para apostas/relatorios/melhorias_sombra.py."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apostas.relatorios.melhorias_sombra import (
    candidatos_a_reactivar,
    formatar_seccao_sombra,
    trajetoria_bloqueado,
)


def _row(ts: datetime, liga: str, mercado: str, resultado: str = "",
         lucro: str | float = "") -> dict:
    return {
        "ts_scan": ts.isoformat(timespec="seconds"),
        "liga": liga, "mercado": mercado,
        "resultado": resultado,
        "lucro": str(lucro) if lucro != "" else "",
    }


def test_trajetoria_separa_por_semana():
    agora = datetime(2026, 10, 19, 12, 0)
    rows = [
        # Semana 0 (0-6 dias) — 2W
        _row(agora - timedelta(days=1), "MLS", "cantos", "W", 0.9),
        _row(agora - timedelta(days=2), "MLS", "cantos", "W", 0.9),
        # Semana 1 (7-13 dias) — 1L
        _row(agora - timedelta(days=10), "MLS", "cantos", "L", -1.0),
        # Semana 2 (14-20 dias) — 1W 1L
        _row(agora - timedelta(days=15), "MLS", "cantos", "W", 0.9),
        _row(agora - timedelta(days=18), "MLS", "cantos", "L", -1.0),
    ]
    t = trajetoria_bloqueado(rows, "MLS", "cantos", agora=agora, n_semanas=4)
    assert t.n_recentes[0] == 2
    assert t.roi_recentes[0] == pytest.approx(90.0)  # ROI de 2W
    assert t.n_recentes[1] == 1
    assert t.roi_recentes[1] == pytest.approx(-100.0)
    assert t.n_recentes[2] == 2
    assert t.roi_recentes[2] == pytest.approx(-5.0, abs=0.5)
    # Semana 3 vazia
    assert t.n_recentes[3] == 0
    assert t.roi_recentes[3] is None


def test_trajetoria_marca_candidato_quando_roi_recente_positivo():
    agora = datetime(2026, 10, 19, 12, 0)
    # 2 semanas recentes com volume >= 10 e ROI >= 0 em ambas
    rows = []
    for i in range(6):
        rows.append(_row(agora - timedelta(days=i), "L", "golos", "W", 0.9))
    for i in range(6):
        rows.append(_row(agora - timedelta(days=7 + i), "L", "golos", "W", 0.9))
    t = trajetoria_bloqueado(rows, "L", "golos", agora=agora)
    assert t.candidato_reactivar is True


def test_trajetoria_nao_candidato_sem_volume():
    agora = datetime(2026, 10, 19, 12, 0)
    rows = [_row(agora - timedelta(days=1), "L", "golos", "W", 0.9)]
    t = trajetoria_bloqueado(rows, "L", "golos", agora=agora)
    assert t.candidato_reactivar is False


def test_trajetoria_nao_candidato_se_recente_negativo():
    agora = datetime(2026, 10, 19, 12, 0)
    rows = [_row(agora - timedelta(days=i), "L", "golos", "L", -1.0)
            for i in range(12)]
    t = trajetoria_bloqueado(rows, "L", "golos", agora=agora)
    assert t.candidato_reactivar is False


def test_candidatos_a_reactivar_ordena_candidatos_primeiro():
    agora = datetime(2026, 10, 19, 12, 0)
    # (liga, mercado, roi, n)  — simulando saida de bloqueios_atuais
    bloqueios = [
        ("A", "cantos", -20.0, 100),  # candidato: recente positivo
        ("B", "golos", -57.0, 50),    # mantem: recente negativo
    ]
    rows = []
    # A: ultimas 2 sem com 10 W
    for i in range(10):
        rows.append(_row(agora - timedelta(days=i), "A", "cantos", "W", 0.9))
    # B: ultimas 2 sem com 10 L
    for i in range(10):
        rows.append(_row(agora - timedelta(days=i), "B", "golos", "L", -1.0))
    trajs = candidatos_a_reactivar(rows, bloqueios, agora=agora)
    assert trajs[0].liga == "A"
    assert trajs[0].candidato_reactivar is True
    assert trajs[1].liga == "B"
    assert trajs[1].candidato_reactivar is False


def test_formatar_seccao_sombra_vazia_devolve_vazio():
    assert formatar_seccao_sombra([]) == ""


def test_formatar_seccao_sombra_mostra_candidato_e_mantem():
    agora = datetime(2026, 10, 19, 12, 0)
    rows_a = [_row(agora - timedelta(days=i), "A", "cantos", "W", 0.9)
              for i in range(10)]
    rows_b = [_row(agora - timedelta(days=i), "B", "golos", "L", -1.0)
              for i in range(10)]
    traj_a = trajetoria_bloqueado(rows_a, "A", "cantos", agora=agora)
    traj_b = trajetoria_bloqueado(rows_b, "B", "golos", agora=agora)
    texto = formatar_seccao_sombra([traj_a, traj_b])
    assert "Em sombra" in texto
    assert "A · cantos" in texto
    assert "B · golos" in texto
    assert "candidato" in texto  # A deve ter o flag
