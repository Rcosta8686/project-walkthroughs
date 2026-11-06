"""Corte automatico de (mercado, lado) que perdem dinheiro nos ultimos 30 dias."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from apostas.alertas import corte_mercados as cm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 6, 12, 0)


def _rows(mercado, lado, n, ganha_cada, odd=1.45, dias_atras=1, liga="Serie A"):
    """n alertas; 1 em cada `ganha_cada` perde (lucro -1), os outros ganham odd-1."""
    ts = (AGORA - timedelta(days=dias_atras)).isoformat(timespec="seconds")
    return [{"mercado": mercado, "lado": lado, "liga": liga, "ts": ts,
             "lucro": -1.0 if i % ganha_cada == 0 else odd - 1} for i in range(n)]


def test_mercado_a_perder_e_cortado_e_o_que_ganha_passa():
    # cartoes over: acerta 50% a odd 1.45 -> ROI -27%; remates over: 80% -> ROI +16%
    rows = _rows("cartoes", "over", 200, 2) + _rows("remates", "over", 200, 5)
    c = cm.calcular(rows, AGORA)
    assert c.bloqueia("cartoes", "over", "Serie A")[0]
    assert not c.bloqueia("remates", "over", "Serie A")[0]
    assert "a perder" in c.bloqueia("cards", "over", "LaLiga")[1]   # nome antigo tambem


def test_liga_com_lucro_comprovado_e_excepcao():
    rows = _rows("cartoes", "over", 300, 2, liga="Premier League") \
        + _rows("cartoes", "over", 40, 5, liga="Championship")
    c = cm.calcular(rows, AGORA)
    assert c.bloqueia("cartoes", "over", "Premier League")[0]
    passa, razao = c.bloqueia("cartoes", "over", "Championship")
    assert not passa and "excepcao" in razao


def test_poucos_dados_nao_corta():
    c = cm.calcular(_rows("golos", "over", 20, 2), AGORA)
    assert not c.cortado("golos", "over")


def test_mercado_que_recuperou_volta_a_passar():
    """Perdia ha 25 dias, agora ganha: os recentes pesam mais."""
    rows = _rows("cantos", "over", 200, 2, dias_atras=25) + _rows("cantos", "over", 150, 5)
    assert not cm.calcular(rows, AGORA).cortado("cantos", "over")


def test_dados_fora_da_janela_nao_contam():
    rows = _rows("golos", "under", 500, 2, dias_atras=45)
    assert not cm.calcular(rows, AGORA).cortado("golos", "under")


def test_sem_dados_nada_e_cortado():
    assert not cm.calcular([]).bloqueia("cartoes", "over", "x")[0]


def test_formatar():
    rows = _rows("cartoes", "over", 200, 2) + _rows("remates", "over", 200, 5) \
        + _rows("btts", "nao", 15, 5)
    txt = cm.formatar(cm.calcular(rows, AGORA))
    assert "⛔ cortado 🟨 cartoes over" in txt
    assert "✅ passa 💥 remates over" in txt
    assert "· poucos dados 🔃 BTTS nao" in txt


def test_relatorio_de_calibracao_mostra_o_corte(tmp_path):
    from apostas.relatorios.calibracao_alertas import formatar_relatorio
    rows = [{**r, "prob": 0.74, "ganhou": r["lucro"] > 0, "fonte": "analista"}
            for r in _rows("cartoes", "over", 200, 2)]
    assert "Corte automatico" in formatar_relatorio(rows)


def test_projeccao_de_mercado_cortado_nao_e_enviada(monkeypatch):
    from testes.test_alertas_enviados import _correr_projeccao
    rows = _rows("remates_a_baliza", "under", 200, 2) + _rows("remates_a_baliza", "over", 200, 2) \
        + _rows("cantos", "under", 200, 2) + _rows("cantos", "over", 200, 2)
    enviadas, registadas = _correr_projeccao(monkeypatch, (3, 2, 5.5, 4.0), (4, 3, 6.5, 5.0),
                                             cortes=cm.calcular(rows, AGORA))
    assert enviadas == [] and registadas == []
    # e sem corte as mesmas projeccoes saem
    enviadas, _ = _correr_projeccao(monkeypatch, (3, 2, 5.5, 4.0), (4, 3, 6.5, 5.0))
    assert len(enviadas) == 2
