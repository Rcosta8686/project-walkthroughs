"""Testes para apostas/alertas/historico_alertas.py — M16."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from apostas.alertas import historico_alertas as ha


def _escrever_csv(path: Path, rows: list[dict]) -> None:
    campos = ["liga", "mercado", "lado", "odd", "clv", "resultado", "lucro"]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=campos)
        writer.writeheader()
        writer.writerows(rows)


def test_categoria_forte_com_mais_de_100_alertas_e_clv_positivo(tmp_path):
    p = tmp_path / "sug.csv"
    rows = []
    for i in range(110):
        rows.append({"liga": "Serie A", "mercado": "golos", "lado": "over",
                     "odd": 1.9, "clv": 0.03,
                     "resultado": "W" if i % 2 == 0 else "L",
                     "lucro": 0.90 if i % 2 == 0 else -1.0})
    _escrever_csv(p, rows)

    perfis = ha.carregar_perfis(p)
    perfil = ha.perfil_para(perfis, "Serie A", "golos", "over")
    assert perfil is not None
    assert perfil.n_alertas == 110
    assert perfil.clv_medio == pytest.approx(3.0, abs=0.1)
    assert perfil.categoria_forte
    assert perfil.stake_mult == 1.0


def test_categoria_fraca_poucos_alertas(tmp_path):
    p = tmp_path / "sug.csv"
    rows = [
        {"liga": "L", "mercado": "golos", "lado": "over",
         "odd": 1.9, "clv": 0.01, "resultado": "W", "lucro": 0.90}
        for _ in range(15)
    ]
    _escrever_csv(p, rows)
    perfis = ha.carregar_perfis(p)
    perfil = ha.perfil_para(perfis, "L", "golos", "over")
    assert perfil.n_alertas == 15
    assert not perfil.categoria_forte
    assert perfil.stake_mult == 0.5  # <20 → metade


def test_bloqueia_quando_clv_muito_negativo(tmp_path):
    p = tmp_path / "sug.csv"
    rows = [
        {"liga": "L", "mercado": "cantos", "lado": "over",
         "odd": 1.9, "clv": -0.05, "resultado": "L", "lucro": -1.0}
        for _ in range(50)
    ]
    _escrever_csv(p, rows)
    perfis = ha.carregar_perfis(p)
    perfil = ha.perfil_para(perfis, "L", "cantos", "over")
    assert perfil.stake_mult == 0.0  # CLV <= -3% → bloqueia


def test_formatar_linha_telegram_tem_essencial(tmp_path):
    p = tmp_path / "sug.csv"
    rows = [
        {"liga": "L", "mercado": "golos", "lado": "over",
         "odd": 1.9, "clv": 0.03, "resultado": "W", "lucro": 0.90}
        for _ in range(110)
    ]
    _escrever_csv(p, rows)
    perfis = ha.carregar_perfis(p)
    perfil = ha.perfil_para(perfis, "L", "golos", "over")
    linha = ha.formatar_linha_telegram(perfil)
    assert "110 alertas" in linha
    assert "CLV" in linha
    assert "ROI" in linha
    assert "categoria forte" in linha


def test_perfil_para_sem_match_devolve_none(tmp_path):
    p = tmp_path / "sug.csv"
    _escrever_csv(p, [])
    perfis = ha.carregar_perfis(p)
    assert ha.perfil_para(perfis, "X", "y", "z") is None


def test_csv_inexistente_devolve_vazio(tmp_path):
    perfis = ha.carregar_perfis(tmp_path / "nao_existe.csv")
    assert perfis == {}
