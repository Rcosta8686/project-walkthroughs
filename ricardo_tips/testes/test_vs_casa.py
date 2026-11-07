"""Nos vs casas: medir se prevemos melhor do que as casas, e onde ha lucro."""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import pytest

from apostas.relatorios import csv_live
from apostas.relatorios import vs_casa as vc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_probabilidade_da_casa_sem_margem():
    assert vc.p_sem_margem(1.80, 2.00) == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.0))
    assert vc.p_sem_margem(2.0) == pytest.approx(0.5 / vc.MARGEM_UM_LADO)
    assert vc.p_sem_margem(None) is None and vc.p_sem_margem(1.0) is None


def test_anexa_a_odd_da_linha_e_do_outro_lado():
    odds = {("cartoes", 4.5): {"over": 1.80, "under": 2.00}, ("btts", None): {"sim": 1.9}}
    s = {"mercado": "cartoes", "lado": "over", "linha": "4.5"}
    assert vc.anexar_odds(s, odds)
    assert s["odd_casa"] == 1.80 and s["odd_outro"] == 2.00 and 0.52 < s["p_casa"] < 0.53
    b = {"mercado": "btts", "lado": "sim", "linha": ""}
    assert vc.anexar_odds(b, odds) and b["odd_outro"] is None
    assert not vc.anexar_odds({"mercado": "cantos", "lado": "over", "linha": "9.5"}, odds)


def _sug(i, mercado="cartoes", linha="4.5", res="W"):
    return {"alert_id": i, "ts_scan": f"2026-11-07T20:{i % 60:02d}:00", "event_id": 100 + i,
            "liga": "L", "home": "A", "away": "B", "minuto": 60, "marcador": "1-1",
            "mercado": mercado, "lado": "over", "linha": linha, "casa": "ANALISTA",
            "odd": 1.5, "prob_modelo": 0.70, "resultado": res}


def test_junta_a_odd_guardada_com_o_resultado(tmp_path):
    sug_csv, odds_csv = tmp_path / "s.csv", tmp_path / "o.csv"
    sugs = [_sug(1), _sug(2, res="L"), _sug(3, res=""), _sug(4)]
    with sug_csv.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=csv_live.COLUNAS_ACTUAIS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sugs)
    for s in sugs[:3]:   # a 4a nao tinha odd da casa
        s.update(odd_casa=1.9, odd_outro=1.9, p_casa=0.5, linha=4.5)
    assert vc.registar(sugs[:3], odds_csv) == 3
    pares = vc.carregar_pares(sug_csv, odds_csv)
    assert [(p["ganhou"], p["p_nos"], p["p_casa"], p["odd"]) for p in pares] == [
        (True, 0.70, 0.5, 1.9), (False, 0.70, 0.5, 1.9)]
    assert vc.carregar_pares(sug_csv, tmp_path / "nao_existe.csv") == []


def _pares(n, nosso_ruido, casa_encolhe, seed=7, margem=1.05):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        p = rnd.uniform(0.25, 0.85)
        p_nos = min(0.97, max(0.03, p + rnd.gauss(0, nosso_ruido)))
        p_casa = 0.55 + casa_encolhe * (p - 0.55)
        out.append({"mercado": "cartoes", "lado": "over", "minuto": 70, "p_nos": p_nos,
                    "p_casa": p_casa, "odd": round(1 / (p_casa * margem), 3),
                    "ganhou": rnd.random() < p, "ts": "2026-11-07"})
    return out


def test_quando_sabemos_mais_do_que_a_casa_e_medido():
    vs = vc.calcular(_pares(3000, nosso_ruido=0.03, casa_encolhe=0.3))
    assert vs.geral.vantagem > 0.03 and vs.por_mercado["cartoes"].vantagem > 0
    assert vs.mistura is not None and vs.mistura.acrescenta
    acima = vs.segmentos["todos"]["10pp+ acima da casa"]
    assert acima.n > 100 and acima.roi > 0.05 and acima.acerto > acima.p_casa_media
    txt = vc.formatar(vs)
    assert "batemos a casa" in txt and "acrescenta informacao" in txt and "💎 lucro provado" in txt


def test_modelo_sem_informacao_nao_engana():
    rnd = random.Random(3)
    pares = _pares(3000, nosso_ruido=0.0, casa_encolhe=1.0)
    for p in pares:            # o nosso modelo: ruido puro
        p["p_nos"] = rnd.uniform(0.2, 0.9)
    vs = vc.calcular(pares)
    assert vs.geral.vantagem < 0
    assert vs.mistura is not None and not vs.mistura.acrescenta
    assert "a casa ainda preve melhor" in vc.formatar(vs)


def test_poucas_dicas_sem_mistura_e_texto_honesto():
    assert vc.calcular(_pares(50, 0.03, 0.3)).mistura is None
    assert "Ainda sem dicas resolvidas" in vc.formatar(vc.VsCasa())


def test_texto_do_alerta_com_e_sem_historia():
    vazio = vc.VsCasa()
    assert vc.texto_alerta(vazio, "cartoes", 1.85, 0.52, 0.68) == (
        "Casa *1.85* (da 52%) · nos *68%* (+16pp)")
    vs = vc.calcular(_pares(3000, 0.03, 0.3))
    txt = vc.texto_alerta(vs, "cartoes", 1.85, 0.52, 0.68)
    assert "📚 Quando estivemos 10pp+ acima da casa em cartoes: acertamos" in txt
    assert "a odd da casa em" in txt
    # mercado sem historia propria usa todos os mercados
    assert "acima da casa: acertamos" in vc.texto_alerta(vs, "cantos", 1.85, 0.52, 0.68)


def test_gravar_e_ler(tmp_path):
    vs = vc.calcular(_pares(400, 0.03, 0.3))
    vc.gravar(vs, tmp_path / "v.json")
    lido = vc.ler(tmp_path / "v.json")
    assert lido.geral == vs.geral and lido.mistura == vs.mistura
    assert lido.segmentos["todos"].keys() == vs.segmentos["todos"].keys()
    assert vc.ler(tmp_path / "nao.json").geral.n == 0


def test_scanner_guarda_a_odd_da_casa_nas_dicas_novas(tmp_path, monkeypatch):
    import scanner_live_v2 as sc
    pedidos = []
    odds = {("cartoes", 4.5): {"over": 1.8, "under": 2.0}}
    monkeypatch.setattr(sc, "_odds_jogo", lambda eid, ttl=0: pedidos.append(eid) or odds)
    monkeypatch.setattr(sc.sofascore_live, "sofascore_disponivel", lambda: True)
    destino = tmp_path / "odds_casa.csv"
    monkeypatch.setattr(vc, "caminho_odds", lambda: destino)
    sugs = [dict(_sug(1), prob_modelo=0.7, _novo=True), dict(_sug(2), event_id=101, _novo=True),
            dict(_sug(3), _novo=False), dict(_sug(4, mercado="cantos"), _novo=True)]
    assert sc._odds_casa_novas(sugs) == 2
    assert sorted(pedidos) == [101, 104]   # uma vez por jogo, so jogos com dicas novas
    linhas = list(csv.DictReader(destino.open(encoding="utf-8")))
    assert [(r["event_id"], r["linha"], r["odd_casa"]) for r in linhas] == [
        ("101", "4.5", "1.8"), ("101", "4.5", "1.8")]
    s = sugs[0]
    monkeypatch.setattr(sc, "_vs_casa_live", lambda: vc.VsCasa())
    assert sc._texto_casa(s, "cartoes").startswith("Casa *1.80* (da 53%) · nos *70%*")
    assert sc._texto_casa({"prob_modelo": 0.7}, "cartoes") == ""


def test_scanner_marca_as_dicas_novas(tmp_path):
    import scanner_live_v2 as sc
    f = tmp_path / "s.csv"
    a = dict(_sug(1), prob_bruta=0.66)
    sc._gravar_novas([a], f)
    b = dict(_sug(1), prob_bruta=0.66)
    sc._gravar_novas([b], f)
    assert a.get("_novo") is True and not b.get("_novo")
