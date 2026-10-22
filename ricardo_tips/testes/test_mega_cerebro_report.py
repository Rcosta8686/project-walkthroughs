"""Testes do relatorio semanal do mega-cerebro."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apostas.relatorios.mega_cerebro_report import (
    LinhaRelatorioMega,
    agregar_por_heuristica,
    formatar_telegram,
)
from apostas.utils.tempo import agora_utc


class _Sinal:
    def __init__(self, heuristica, resolvido, dias_atras=1):
        self.heuristica = heuristica
        self.resolvido = resolvido
        self.criado_em = agora_utc() - timedelta(days=dias_atras)


def test_agregar_vazio_devolve_lista_vazia():
    assert agregar_por_heuristica([]) == []


def test_agregar_so_resolvidos_entram():
    sinais = [
        _Sinal("H1", "acerto"),
        _Sinal("H1", "pendente"),  # ignorado
        _Sinal("H1", "falha"),
        _Sinal("H1", "nao_resolvido"),  # ignorado
    ]
    linhas = agregar_por_heuristica(sinais)
    assert len(linhas) == 1
    assert linhas[0].heuristica == "H1"
    assert linhas[0].n_total == 2
    assert linhas[0].n_acerto == 1
    assert linhas[0].n_falha == 1
    assert linhas[0].taxa_acerto == 0.5


def test_agregar_fora_da_janela_ignorado():
    sinais = [
        _Sinal("H1", "acerto", dias_atras=10),  # fora da janela de 7
        _Sinal("H1", "acerto", dias_atras=1),
    ]
    linhas = agregar_por_heuristica(sinais, dias=7)
    assert linhas[0].n_total == 1


def test_agregar_ordena_por_volume():
    sinais = (
        [_Sinal("H1", "acerto")] * 3
        + [_Sinal("H2", "acerto")] * 10
        + [_Sinal("H3", "falha")] * 5
    )
    linhas = agregar_por_heuristica(sinais)
    assert [l.heuristica for l in linhas] == ["H2", "H3", "H1"]


def test_formatar_vazio_devolve_vazio():
    assert formatar_telegram([]) == ""


def test_formatar_mostra_todas_heuristicas():
    linhas = [
        LinhaRelatorioMega("H1_amarelos", 10, 7, 3, 0.7),
        LinhaRelatorioMega("H3_pressao_lateral", 5, 2, 3, 0.4),
        LinhaRelatorioMega("H4_dominante_controla", 3, 1, 2, 0.33),
    ]
    txt = formatar_telegram(linhas, dias=7)
    assert "Mega-cerebro" in txt
    assert "H1_amarelos" in txt
    assert "7/10" in txt
    assert "70%" in txt
    assert "✅" in txt  # 70% >= 60
    assert "❌" in txt  # 33% < 40
