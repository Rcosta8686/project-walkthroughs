"""Testes para apostas/relatorios/periodicos.py — M15 + M22."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apostas.relatorios.periodicos import (
    ApostaResumo,
    agregar_edge_volume,
    formatar_relatorio_mensal_telegram,
    formatar_relatorio_semanal_telegram,
    gerar_relatorio_mensal,
)


# ─── M15 — Edge × Volume ───────────────────────────────────────────

def _row(ts: datetime, liga: str, mercado: str, ev: str | float = "",
         resultado: str = "", lucro: str | float = "", clv: str | float = "") -> dict:
    return {
        "ts_scan": ts.isoformat(timespec="seconds"),
        "liga": liga, "mercado": mercado,
        "ev": str(ev) if ev != "" else "",
        "resultado": resultado,
        "lucro": str(lucro) if lucro != "" else "",
        "clv": str(clv) if clv != "" else "",
    }


def test_agregar_edge_volume_ordena_por_score():
    agora = datetime(2026, 10, 18, 12, 0)
    inicio = agora - timedelta(days=7)
    rows = [
        # Serie A / golos: 3 alertas, edge medio 0.08 → score 0.24
        _row(agora - timedelta(days=1), "Serie A", "golos", 0.07),
        _row(agora - timedelta(days=2), "Serie A", "golos", 0.08),
        _row(agora - timedelta(days=3), "Serie A", "golos", 0.09),
        # Premier / cantos: 2 alertas, edge 0.05 → score 0.10
        _row(agora - timedelta(days=1), "Premier", "cantos", 0.05),
        _row(agora - timedelta(days=2), "Premier", "cantos", 0.05),
    ]
    rel = agregar_edge_volume(rows, data_inicio=inicio, data_fim=agora)
    assert rel.linhas_top[0].liga == "Serie A"
    assert rel.linhas_top[0].mercado == "golos"
    assert rel.linhas_top[0].n_alertas == 3
    assert rel.linhas_top[0].edge_x_volume == pytest.approx(0.24, abs=0.01)
    assert rel.total_alertas == 5


def test_agregar_edge_volume_ignora_fora_da_janela():
    agora = datetime(2026, 10, 18, 12, 0)
    inicio = agora - timedelta(days=7)
    rows = [
        _row(agora - timedelta(days=3), "L", "golos", 0.07),
        _row(agora - timedelta(days=30), "L", "golos", 0.07),  # fora
    ]
    rel = agregar_edge_volume(rows, data_inicio=inicio, data_fim=agora)
    assert rel.total_alertas == 1


def test_agregar_edge_volume_calcula_roi_e_clv():
    agora = datetime(2026, 10, 18, 12, 0)
    inicio = agora - timedelta(days=7)
    rows = [
        _row(agora - timedelta(days=1), "L", "golos", 0.07,
             resultado="W", lucro=0.9, clv=0.02),
        _row(agora - timedelta(days=1), "L", "golos", 0.07,
             resultado="L", lucro=-1.0, clv=-0.01),
    ]
    rel = agregar_edge_volume(rows, data_inicio=inicio, data_fim=agora)
    linha = rel.linhas_top[0]
    assert linha.n_resolvidos == 2
    assert linha.roi == pytest.approx((0.9 - 1.0) / 2 * 100, abs=0.1)  # ROI%
    assert linha.clv_medio == pytest.approx((0.02 - 0.01) / 2 * 100, abs=0.1)  # %


def test_formatar_semanal_funciona_sem_dados():
    rel = agregar_edge_volume(
        [], data_inicio=datetime(2026, 10, 1), data_fim=datetime(2026, 10, 8),
    )
    texto = formatar_relatorio_semanal_telegram(rel)
    assert "Relatório Semanal" in texto
    assert "Sem alertas" in texto


def test_formatar_semanal_tem_top_e_bottom():
    agora = datetime(2026, 10, 18, 12, 0)
    inicio = agora - timedelta(days=7)
    rows = []
    for i, liga in enumerate(["A", "B", "C", "D", "E", "F", "G", "H",
                              "I", "J", "K", "L", "M", "N", "O", "P",
                              "Q", "R", "S", "T"]):
        rows.append(_row(agora - timedelta(hours=i), liga, "golos", (20 - i) * 0.005))
    rel = agregar_edge_volume(rows, data_inicio=inicio, data_fim=agora, top_n=5, bottom_n=3)
    assert len(rel.linhas_top) == 5
    assert len(rel.linhas_bottom) == 3
    texto = formatar_relatorio_semanal_telegram(rel)
    assert "Top edge" in texto
    assert "Bottom" in texto


# ─── M22 — Mensal ──────────────────────────────────────────────────

def _ap(dia: int, lucro: float, stake: float = 1.0, resultado: str = "W",
        fonte: str = "pre_match") -> ApostaResumo:
    return ApostaResumo(
        criado_em=datetime(2026, 10, dia, 15, 0),
        stake=stake, odd_executada=1.9,
        resultado=resultado, lucro=lucro, fonte=fonte,
    )


def test_relatorio_mensal_lucro_e_roi():
    apostas = [
        _ap(5, 0.9),   # W pre
        _ap(6, -1.0, resultado="L"),  # L pre
        _ap(7, 0.95, fonte="live"),   # W live
    ]
    rel = gerar_relatorio_mensal(
        ano=2026, mes=10, apostas_mes=apostas,
    )
    assert rel.n_apostas == 3
    assert rel.n_live == 1
    assert rel.n_pre_match == 2
    assert rel.n_vitorias == 2
    assert rel.n_derrotas == 1
    assert rel.lucro_total == pytest.approx(0.9 - 1.0 + 0.95)
    assert rel.roi == pytest.approx((0.85 / 3.0) * 100, abs=0.1)
    assert rel.win_rate == pytest.approx(2 / 3 * 100, abs=0.1)


def test_relatorio_mensal_ignora_pendentes_em_roi():
    apostas = [
        _ap(5, 0.9),
        _ap(6, None, resultado="pendente"),  # pendente nao conta
    ]
    rel = gerar_relatorio_mensal(ano=2026, mes=10, apostas_mes=apostas)
    assert rel.n_pendentes == 1
    assert rel.lucro_total == pytest.approx(0.9)
    # ROI usa apenas stake resolvido
    assert rel.roi == pytest.approx(90.0, abs=0.1)


def test_relatorio_mensal_streak_de_W():
    apostas = [
        _ap(1, -1.0, resultado="L"),
        _ap(2, 0.9),   # W
        _ap(3, 0.9),   # W
        _ap(4, 0.9),   # W (mais recente)
    ]
    rel = gerar_relatorio_mensal(ano=2026, mes=10, apostas_mes=apostas)
    assert rel.streak_tipo == "W"
    assert rel.streak_tamanho == 3


def test_relatorio_mensal_dias_vencedores_perdedores():
    apostas = [
        _ap(1, 0.9),
        _ap(1, 0.9),   # mesmo dia, somam → verde
        _ap(2, -1.0, resultado="L"),  # vermelho
        _ap(3, 0.9),
        _ap(3, -1.0, resultado="L"),  # dia neutro (-0.1 → vermelho)
    ]
    rel = gerar_relatorio_mensal(ano=2026, mes=10, apostas_mes=apostas)
    assert rel.dias_vencedores == 1
    assert rel.dias_perdedores == 2


def test_relatorio_mensal_crescimento_banca():
    rel = gerar_relatorio_mensal(
        ano=2026, mes=10, apostas_mes=[],
        saldo_inicio_mes=50.0, saldo_fim_mes=65.0,
    )
    assert rel.crescimento_pct == pytest.approx(30.0)


def test_relatorio_mensal_compara_com_anterior():
    rel = gerar_relatorio_mensal(
        ano=2026, mes=10,
        apostas_mes=[_ap(5, 2.0)],
        apostas_mes_anterior=[_ap(5, -1.0, resultado="L")],
    )
    assert rel.lucro_mes_anterior == pytest.approx(-1.0)
    assert rel.delta_vs_anterior == pytest.approx(3.0)


def test_formatar_mensal_tem_todas_as_secoes():
    rel = gerar_relatorio_mensal(
        ano=2026, mes=10,
        apostas_mes=[_ap(1, 0.9), _ap(2, -1.0, resultado="L")],
        saldo_inicio_mes=50, saldo_fim_mes=49.9,
    )
    t = formatar_relatorio_mensal_telegram(rel)
    assert "Outubro 2026" in t
    assert "Banca" in t
    assert "Lucro" in t
    assert "ROI" in t
    assert "Win rate" in t


def test_relatorio_mensal_normaliza_resultados_pre_match():
    """ApostaReal usa 'ganho'/'perdido' — deve ser contado como W/L."""
    apostas = [
        ApostaResumo(datetime(2026, 10, 1), 1.0, 1.9, "ganho", 0.9, "pre_match"),
        ApostaResumo(datetime(2026, 10, 2), 1.0, 1.9, "perdido", -1.0, "pre_match"),
        ApostaResumo(datetime(2026, 10, 3), 1.0, 1.9, "reembolso", 0.0, "pre_match"),
    ]
    rel = gerar_relatorio_mensal(ano=2026, mes=10, apostas_mes=apostas)
    assert rel.n_vitorias == 1
    assert rel.n_derrotas == 1
    assert rel.n_push == 1
