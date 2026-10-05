"""Testes do gerador de dashboard HTML."""

from __future__ import annotations

from datetime import datetime

import pytest

from apostas.backtest import walk_forward
from apostas.backtest.walk_forward import ApostaSimulada, RelatorioBacktest
from apostas.dashboard import gerador
from apostas.ingestao import football_data_uk, jogos_sportmonks, ligas_equipas
from apostas.ingestao.sportmonks import cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


def _relatorio_sintetico(modelo: str = "gap") -> RelatorioBacktest:
    rel = RelatorioBacktest(
        linha=2.5, ev_minimo=0.03, casa_de_apostas_ref="Avg_Closing",
        meia_vida_dias=365, epoca_teste=2024, modelo=modelo,
    )
    base = datetime(2024, 8, 1)
    for i in range(5):
        rel.apostas.append(ApostaSimulada(
            data=base, liga_id=1, jogo_id=i, casa_id=1, fora_id=2,
            lado="over" if i % 2 == 0 else "under",
            linha=2.5, prob_modelo=0.6, odd=1.9, ev=0.14,
            stake=1.0,
            resultado="ganho" if i < 3 else "perdido",
            lucro=0.9 if i < 3 else -1.0,
        ))
    rel.bankroll = [(base, 0.9), (base, 1.8), (base, 2.7), (base, 1.7), (base, 0.7)]
    return rel


def test_gera_html_com_um_modelo(tmp_path):
    rel = _relatorio_sintetico("gap")
    destino = tmp_path / "out" / "index.html"
    caminho = gerador.gerar([rel], destino=destino)
    assert caminho == destino
    assert destino.exists()
    conteudo = destino.read_text(encoding="utf-8")
    assert "Ricardo Tips" in conteudo
    assert "GAP" in conteudo
    assert "Plotly" in conteudo
    assert "comparacao" not in conteudo or "<section class=\"comparacao\">" not in conteudo


def test_gera_html_com_comparacao_quando_dois_modelos(tmp_path):
    gap = _relatorio_sintetico("gap")
    poisson = _relatorio_sintetico("poisson")
    destino = tmp_path / "out" / "index.html"
    gerador.gerar([gap, poisson], destino=destino)
    html = destino.read_text(encoding="utf-8")
    assert "Comparação" in html
    assert "GAP" in html
    assert "POISSON" in html


def test_requer_pelo_menos_um_relatorio(tmp_path):
    with pytest.raises(ValueError):
        gerador.gerar([], destino=tmp_path / "x.html")


# ─── Integração end-to-end ──────────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))
    jogos_sportmonks.sincronizar(epocas_atras=2)
    football_data_uk.sincronizar(epocas=[2023, 2024])
    yield bd
    db_mod.reset_engine()


def test_dashboard_e2e_gap_e_poisson(tmp_path, bd_populada):
    gap = walk_forward.correr(epoca_teste=2024, modelo="gap")
    poisson = walk_forward.correr(epoca_teste=2024, modelo="poisson")
    destino = tmp_path / "dash" / "index.html"
    gerador.gerar([gap, poisson], destino=destino)

    html = destino.read_text(encoding="utf-8")
    assert "bankroll-1" in html
    assert "bankroll-2" in html
    # Deve conter dados de apostas
    assert "over" in html.lower()
    # Deve ter KPIs (ROI aparece como % com sinal)
    assert "%" in html
