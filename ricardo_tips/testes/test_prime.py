"""PRIME = tipo de dica que ja provou dar lucro (nao so acertar muito)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from apostas.relatorios import prime as pm


def _rows(n, acerta, odd, mercado="remates", lado="over", minuto=62, prob=0.72):
    k = round(n * acerta)
    return [{"mercado": mercado, "lado": lado, "minuto": minuto, "prob": prob,
             "ganhou": i < k, "lucro": (odd - 1) if i < k else -1.0,
             "ts": "2026-11-08T20:00:00"} for i in range(n)]


def test_lucro_confirmado_e_prime():
    pr = pm.calcular(_rows(120, 0.70, 1.80))            # ROI +26%
    g = pr.grupo_prime("remates", "over", 62, 0.72)
    assert g is not None and g.roi == pytest.approx(0.26, abs=0.01)
    assert g.roi_pior_caso is not None and 0 < g.roi_pior_caso < g.roi


def test_acertar_muito_a_odd_baixa_nao_e_prime():
    """O argumento do Ricardo: 95% de acerto a 1.03 perde dinheiro."""
    pr = pm.calcular(_rows(200, 0.95, 1.03, mercado="cantos"))
    g = pr.grupos[pm.chave("cantos", "over", 62, 0.72)]
    assert g.taxa == pytest.approx(0.95) and g.roi < 0 and not g.e_prime


def test_lucro_com_poucos_casos_ainda_nao_e_prime():
    pr = pm.calcular(_rows(30, 0.75, 1.80))
    assert pr.grupo_prime("remates", "over", 62, 0.72) is None
    assert "A caminho" in pm.formatar(pr)


def test_lucro_na_media_mas_dentro_da_sorte_nao_e_prime():
    pr = pm.calcular(_rows(60, 0.58, 1.80))             # ROI +4%, desvio grande
    g = pr.grupos[pm.chave("remates", "over", 62, 0.72)]
    assert g.roi > 0 and g.roi_pior_caso < 0 and not g.e_prime


def test_grupos_por_mercado_lado_fase_e_faixa():
    assert pm.chave("sot", "OVER", 62, 0.72) == ("remates_a_baliza", "over", 60, 0.65)
    assert pm.chave("cartoes", "under", 10, 0.96) == ("cartoes", "under", 0, 0.95)
    assert pm.chave("golos", "over", 50, 0.50) is None          # abaixo de 55%
    pr = pm.calcular(_rows(120, 0.70, 1.80))
    assert pr.grupo_prime("remates", "over", 80, 0.72) is None  # outra fase


def test_selo_e_relatorio():
    pr = pm.calcular(_rows(120, 0.70, 1.80))
    g = pr.grupo_prime("remates", "over", 62, 0.72)
    assert g is not None
    assert pm.selo(g).startswith("lucro confirmado: ROI +26% em 120 casos iguais")
    txt = pm.formatar(pr)
    assert "💎 💥 remates over · 60'-75' · modelo 65-70%" in txt


def test_gravar_ler(tmp_path):
    f = tmp_path / "p.json"
    pm.gravar(pm.calcular(_rows(120, 0.70, 1.80)), f)
    lido = pm.ler(f)
    assert lido.grupo_prime("remates", "over", 62, 0.72) is not None


def test_recalcular_usa_so_o_modelo_actual(tmp_path, monkeypatch):
    import csv
    from apostas.modelos import correcao_ritmo as cr
    f = tmp_path / "sug.csv"
    with f.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["ts_scan", "minuto", "mercado", "lado", "casa", "prob_modelo", "resultado",
                    "lucro"])
        for i in range(120):   # antes da mudanca do ritmo: lucro; depois: nada
            w.writerow(["2026-11-01T20:00:00", 62, "remates", "over", "ANALISTA", 0.72,
                        "W" if i < 84 else "L", 0.8 if i < 84 else -1])
    monkeypatch.setattr(cr, "ler", lambda path=None: cr.CorrecaoRitmo(
        bases={"remates": "equipas"}, datas_ativacao={"remates": datetime(2026, 11, 7)}))
    pr = pm.recalcular_e_gravar(f, tmp_path / "p.json")
    assert pr.grupos == {}


def test_alerta_prime_leva_o_selo_na_mensagem(monkeypatch):
    from apostas.alertas import telegram_live
    enviados = []
    monkeypatch.setattr(telegram_live, "cliente", lambda: SimpleNamespace(enviar=enviados.append))
    s = {"alert_id": 9, "liga": "Serie A", "home": "Roma", "away": "Lazio", "minuto": 62,
         "marcador": "1-1", "mercado": "remates", "lado": "over", "linha": "22.5",
         "casa": "ANALISTA", "odd": "1.55", "prob_modelo": "0.72", "estrelas": "4",
         "prime": "lucro confirmado: ROI +26% em 120 casos iguais"}
    telegram_live.enviar_sugestao_live(s)
    assert enviados and "💎 *PRIME* — lucro confirmado: ROI +26%" in enviados[0]
