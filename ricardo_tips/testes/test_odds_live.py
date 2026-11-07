"""Odds reais ao vivo: ler o Sofascore, filtrar dicas sem valor, medir lucro real."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from apostas.ingestao import odds_live as ol

PAYLOAD = {"markets": [
    {"marketName": "Full time", "choices": [{"name": "1", "fractionalValue": "6/5"}]},
    {"marketName": "Corners 2-Way", "marketPeriod": "Full-time", "choiceGroup": "9.5",
     "choices": [{"name": "Over", "fractionalValue": "4/5"},
                 {"name": "Under", "fractionalValue": "1/1"}]},
    {"marketName": "Cards in match", "choices": [{"name": "Over 4.5", "decimalValue": "1.72"},
                                                 {"name": "Under 4.5", "decimalValue": "2.05"}]},
    {"marketName": "Match goals", "choiceGroup": "2.5",
     "choices": [{"name": "Over", "fractionalValue": "9/10"}]},
    {"marketName": "Both teams to score", "choices": [{"name": "Yes", "fractionalValue": "4/6"},
                                                      {"name": "No", "fractionalValue": "11/10"}]},
    {"marketName": "Total shots on target", "choiceGroup": "8.5",
     "choices": [{"name": "Over", "fractionalValue": "1/1"}]},
    {"marketName": "1st half corners", "choiceGroup": "4.5",
     "choices": [{"name": "Over", "fractionalValue": "5/1"}]},           # 1a parte: fora
    {"marketName": "Home team total goals", "choiceGroup": "1.5",
     "choices": [{"name": "Over", "fractionalValue": "5/1"}]},           # so uma equipa: fora
    {"marketName": "Corners", "marketPeriod": "1st half", "choiceGroup": "3.5",
     "choices": [{"name": "Over", "fractionalValue": "5/1"}]},           # periodo 1a parte
]}


def test_interpretar_os_dois_formatos_e_so_o_jogo_inteiro():
    o = ol.interpretar(PAYLOAD)
    assert o[("cantos", 9.5)] == {"over": 1.8, "under": 2.0}
    assert o[("cartoes", 4.5)] == {"over": 1.72, "under": 2.05}
    assert o[("golos", 2.5)]["over"] == pytest.approx(1.9)
    assert o[("btts", None)] == {"sim": pytest.approx(1.667, abs=0.001), "nao": 2.1}
    assert o[("remates_a_baliza", 8.5)]["over"] == 2.0
    assert ("cantos", 4.5) not in o and ("cantos", 3.5) not in o and ("golos", 1.5) not in o


def test_procurar_a_linha_da_dica():
    o = ol.interpretar(PAYLOAD)
    assert ol.procurar(o, "cartoes", "OVER", "4.5") == 1.72
    assert ol.procurar(o, "sot", "over", 8.5) == 2.0
    assert ol.procurar(o, "btts", "nao", None) == 2.1
    assert ol.procurar(o, "cantos", "over", 10.5) is None


def test_odd_do_mercado_e_so_informacao():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import scanner_live_v2 as sc
    assert sc._texto_odd_real(1.72) == "Odd no mercado agora: *1.72*"
    assert sc._texto_odd_real(None) == ""


def test_odd_baixa_no_mercado_nao_bloqueia_a_dica(monkeypatch):
    """O Ricardo decide no live: a odd informa e fica guardada, nao corta."""
    from testes.test_alertas_enviados import _correr_projeccao
    enviadas, registos = _correr_projeccao(monkeypatch, (3, 2, 5.5, 4.0), (4, 3, 6.5, 5.0),
                                           odd_real=1.05)
    assert len(enviadas) == 2 and all("Odd no mercado agora: *1.05*" in m for m in enviadas)
    assert all(r["odd_real"] == 1.05 for r in registos)


def test_registo_antigo_ganha_a_coluna_sem_desalinhar(tmp_path):
    from apostas.relatorios import alertas_enviados as ae
    f = tmp_path / "env.csv"
    with f.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["alert_id", "ts_envio", "tipo", "event_id", "mercado", "lado", "linha", "odd"])
        w.writerow(["5", "2026-11-07T20:00:00", "alerta", "77", "remates", "over", "18.5", "1.45"])
    ae.registar(6, datetime(2026, 11, 7, 21), f, event_id=78, mercado="cartoes", lado="over",
                linha=4.5, odd=1.4, odd_real=1.75)
    rows = ae.carregar(f)
    assert [r["alert_id"] for r in rows] == ["5", "6"]
    assert rows[0]["odd_real"] == "" and rows[1]["odd_real"] == "1.75"
    assert rows[1]["mercado"] == "cartoes" and rows[1]["linha"] == "4.5"


def test_lucro_dos_alertas_a_odd_real(tmp_path):
    from apostas.relatorios import alertas_enviados as ae
    sug = tmp_path / "sug.csv"
    with sug.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["alert_id", "event_id", "mercado", "lado", "linha", "odd", "resultado", "lucro"])
        w.writerow(["1", "77", "remates", "over", "18.5", "1.45", "W", "0.45"])
        w.writerow(["2", "78", "golos", "under", "2.5", "1.50", "W", "0.5"])
    env = tmp_path / "env.csv"
    dia = datetime(2026, 11, 7)
    ae.registar(1, dia.replace(hour=20), env, event_id=77, mercado="remates", lado="over",
                linha=18.5, odd=1.45, odd_real=1.80)
    ae.registar(2, dia.replace(hour=21), env, event_id=78, mercado="golos", lado="under",
                linha=2.5, odd=1.50)
    dicas = ae.listar(dia, dia + timedelta(days=1), sug, env)
    assert dicas[0].lucro == pytest.approx(0.80) and dicas[0].odd == 1.80
    assert dicas[1].lucro == pytest.approx(0.5)      # sem odd real: a odd indicada
    rel = ae.resumir(dia, dia + timedelta(days=1), sug, env)
    assert rel.n_odd_real == 1
    assert "à odd real do mercado em 1 de 2" in ae.formatar(rel)
