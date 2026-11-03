"""Calibrador por (mercado, lado): corrige UNDER sobrestimados e OVER subestimados.

Numeros reais do Ricardo (6110 alertas): cartoes under prometia 78% e
acertava 59%; remates over prometia 73% e acertava 91%.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from apostas.alertas.analista import Sinal  # noqa: E402
from apostas.modelos.calibrador_mercado import (  # noqa: E402
    MIN_N, Calibrador, avaliar_fora_da_amostra, treinar,
)


def _grupo(mercado, lado, prob, taxa_real, n, fonte="analista", seed=1, ts0=0):
    rng = random.Random(seed)
    return [{"mercado": mercado, "lado": lado, "fonte": fonte,
             "prob": min(0.97, max(0.51, prob + rng.uniform(-0.05, 0.05))),
             "ganhou": rng.random() < taxa_real, "ts": f"2026-10-{ts0 + i:06d}"}
            for i in range(n)]


def _media_cal(cal, rows):
    return sum(cal.calibrar(r["prob"], r["mercado"], r["lado"], r["fonte"])
               for r in rows) / len(rows)


def test_under_sobrestimado_desce():
    rows = _grupo("cartoes", "under", 0.78, 0.59, 800)
    cal = treinar(rows)
    assert 0.58 < _media_cal(cal, rows) < 0.68


def test_over_subestimado_sobe():
    rows = _grupo("remates", "over", 0.73, 0.91, 700)
    cal = treinar(rows)
    assert _media_cal(cal, rows) > 0.84


def test_grupo_pequeno_fica_igual():
    rows = _grupo("cantos", "over", 0.70, 0.40, MIN_N - 1)
    assert treinar(rows).calibrar(0.70, "cantos", "over", "analista") == 0.70


def test_over_e_under_do_mesmo_mercado_corrigidos_em_sentidos_opostos():
    """O defeito da calibracao antiga: a media global anulava os dois."""
    rows = (_grupo("cantos", "under", 0.74, 0.50, 1000, seed=2)
            + _grupo("cantos", "over", 0.70, 0.85, 500, seed=3))
    cal = treinar(rows)
    assert cal.calibrar(0.74, "cantos", "under", "analista") < 0.62
    assert cal.calibrar(0.70, "cantos", "over", "analista") > 0.75


def test_fonte_sem_dados_cai_para_o_grupo_sem_fonte():
    rows = _grupo("cartoes", "under", 0.78, 0.59, 800, fonte="analista")
    cal = treinar(rows)
    assert cal.calibrar(0.78, "cartoes", "under", "odds") < 0.70


def test_nome_cards_e_normalizado():
    rows = _grupo("cartoes", "under", 0.78, 0.59, 800)
    cal = treinar(rows)
    assert cal.calibrar(0.78, "cards", "under", "analista") < 0.70


def test_calibrador_vazio_e_identidade():
    assert Calibrador().calibrar(0.66, "golos", "over") == 0.66


def test_curva_monotonica():
    rows = (_grupo("golos", "under", 0.60, 0.50, 400, seed=4)
            + _grupo("golos", "under", 0.85, 0.70, 400, seed=5))
    cal = treinar(rows)
    ps = [cal.calibrar(p / 100, "golos", "under") for p in range(52, 97)]
    assert all(a <= b + 1e-12 for a, b in zip(ps, ps[1:]))


def test_avaliacao_fora_da_amostra_melhora_o_brier():
    rows = (_grupo("cantos", "under", 0.74, 0.50, 1200, seed=6, ts0=0)
            + _grupo("remates", "over", 0.72, 0.90, 800, seed=7, ts0=0))
    av = avaliar_fora_da_amostra(rows)
    assert av is not None
    assert av["brier_cal"] < av["brier_bruto"]
    assert av["n_teste"] == 600


def _com_data(rows, dia):
    return [{**r, "ts": f"2026-{dia}T12:00:00"} for r in rows]


def test_alertas_recentes_pesam_mais():
    """Golos under: antes acertava 55%, nas ultimas semanas 75%."""
    antigos = _com_data(_grupo("golos", "under", 0.76, 0.55, 1500, seed=8), "08-01")
    recentes = _com_data(_grupo("golos", "under", 0.76, 0.75, 400, seed=9), "11-01")
    cal = treinar(antigos + recentes)
    p = cal.calibrar(0.76, "golos", "under", "analista")
    # sem pesos a media seria ~59%; com meia-vida de 14 dias fica perto dos 75%
    assert p > 0.68


def test_sem_datas_validas_pesos_iguais():
    rows = _grupo("cartoes", "under", 0.78, 0.59, 800)  # ts invalidos
    assert 0.58 < _media_cal(treinar(rows), rows) < 0.68


def test_avaliacao_sem_dados_suficientes():
    assert avaliar_fora_da_amostra(_grupo("golos", "over", 0.7, 0.7, 100)) is None


# ─── Ligacao ao scanner ──────────────────────────────────────────────────

class _CalFixo:
    def __init__(self, p):
        self.p = p

    def calibrar(self, prob, mercado, lado, fonte=None):
        return self.p


def _sinal(prob=0.78):
    return Sinal(tipo="t", mercado="cartoes", lado="under", linha=4.5,
                 prob_modelo=prob, odd_minima=1.28, odd_valor=1.35, estrelas=4)


def test_scanner_sinal_que_cai_abaixo_de_55pct_nunca_vai_ao_telegram(monkeypatch):
    import scanner_live_v2 as scn
    monkeypatch.setattr(scn, "_calibrador_live", lambda: _CalFixo(0.50))
    sinal, bruta = scn._calibrar_sinal(SimpleNamespace(liga="Serie A"), _sinal(0.78))
    assert bruta == 0.78
    assert sinal.prob_modelo == 0.50
    assert sinal.estrelas == 1
    assert sinal.odd_valor > 2.0  # odd alvo passa a reflectir 50%


def test_scanner_recalcula_odd_alvo_com_prob_calibrada(monkeypatch):
    import scanner_live_v2 as scn
    monkeypatch.setattr(scn, "_calibrador_live", lambda: _CalFixo(0.80))
    sinal, _ = scn._calibrar_sinal(SimpleNamespace(liga="Serie A"), _sinal(0.70))
    assert sinal.odd_valor == round(1.05 / 0.80, 2)


def test_scanner_grava_prob_bruta_no_csv(tmp_path):
    import scanner_live_v2 as scn
    f = tmp_path / "s.csv"
    s = {"home": "A", "away": "B", "mercado": "cartoes", "lado": "under",
         "linha": "4.5", "casa": "ANALISTA", "minuto": 60,
         "prob_modelo": 0.59, "prob_bruta": 0.78}
    assert scn._gravar_novas([s], f) == 1
    with f.open(encoding="utf-8") as fp:
        row = next(csv.DictReader(fp))
    assert float(row["prob_modelo"]) == 0.78
    assert s["prob_modelo"] == 0.59  # em memoria continua a calibrada
