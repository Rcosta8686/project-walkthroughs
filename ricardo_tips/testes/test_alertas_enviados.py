"""Registo das dicas enviadas ao Telegram e o seu resultado no relatorio mensal."""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

from apostas.modelos import projecao_live
from apostas.relatorios import alertas_enviados as ae

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

NOV = datetime(2026, 11, 1)
DEZ = datetime(2026, 12, 1)


def _csv_sugestoes(path: Path, linhas: list[dict]) -> Path:
    campos = ["alert_id", "mercado", "lado", "resultado", "lucro"]
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos)
        w.writeheader()
        for l in linhas:
            w.writerow(l)
    return path


def test_so_conta_o_que_foi_enviado_e_no_mes(tmp_path):
    env = tmp_path / "env.csv"
    sug = _csv_sugestoes(tmp_path / "sug.csv", [
        {"alert_id": "1", "mercado": "remates", "lado": "over", "resultado": "W", "lucro": "0.8"},
        {"alert_id": "2", "mercado": "cartoes", "lado": "over", "resultado": "L", "lucro": "-1"},
        {"alert_id": "3", "mercado": "golos", "lado": "under", "resultado": "W", "lucro": "0.5"},
        {"alert_id": "4", "mercado": "cantos", "lado": "over", "resultado": "", "lucro": ""},
        {"alert_id": "5", "mercado": "golos", "lado": "under", "resultado": "L", "lucro": "-1"},
    ])
    ae.registar(1, datetime(2026, 11, 2, 20), env)
    ae.registar(2, datetime(2026, 11, 3, 21), env)
    ae.registar(4, datetime(2026, 11, 4, 19), env)
    ae.registar(5, datetime(2026, 10, 30, 19), env)   # outro mes
    # alert 3 nunca foi enviado (filtrado): nao conta
    rel = ae.resumir(NOV, DEZ, sug, env)
    assert (rel.n, rel.vitorias, rel.derrotas, rel.pendentes) == (3, 1, 1, 1)
    assert abs(rel.lucro - (-0.2)) < 1e-9
    assert abs(rel.roi - (-10.0)) < 1e-9


def test_projeccao_resolvida_com_total_final(tmp_path):
    env = tmp_path / "env.csv"
    ae.registar(tipo="projeccao", ts=datetime(2026, 11, 2, 20), path=env,
                event_id=10, mercado="cantos", lado="over", linha=8.5, odd=1.5)
    ae.registar(tipo="projeccao", ts=datetime(2026, 11, 2, 21), path=env,
                event_id=11, mercado="sot", lado="under", linha=9.5, odd=1.4)
    ae.registar(tipo="projeccao", ts=datetime(2026, 11, 2, 22), path=env,
                event_id=12, mercado="cantos", lado="over", linha=7.5, odd=1.3)
    assert ae.projeccoes_no_periodo(NOV, DEZ, env) == {(10, "cantos"), (11, "sot"), (12, "cantos")}
    totais = {(10, "cantos"): 11.0, (11, "sot"): 12.0}  # 12 ainda sem estatisticas
    rel = ae.resumir(NOV, DEZ, tmp_path / "nao_existe.csv", env, totais_proj=totais)
    assert (rel.n, rel.n_projeccoes, rel.vitorias, rel.derrotas, rel.pendentes) == (3, 3, 1, 1, 1)
    assert abs(rel.lucro - (0.5 - 1.0)) < 1e-9


def test_alerta_sem_id_nao_e_registado(tmp_path):
    env = tmp_path / "env.csv"
    ae.registar(None, path=env)
    assert not env.exists()


def test_formatar_sem_registo_explica_que_comeca_agora():
    rel = ae.ResumoEnviados(inicio=datetime(2026, 10, 1), fim=NOV, registo_desde=None)
    assert "começou agora" in ae.formatar(rel)


def test_formatar_com_dados(tmp_path):
    env = tmp_path / "env.csv"
    sug = _csv_sugestoes(tmp_path / "sug.csv", [
        {"alert_id": "1", "mercado": "remates", "lado": "over", "resultado": "W", "lucro": "0.8"},
    ])
    ae.registar(1, datetime(2026, 11, 2, 20), env)
    txt = ae.formatar(ae.resumir(NOV, DEZ, sug, env))
    assert "1 dicas (1 alertas · 0 projecções)" in txt
    assert "🟢 💥 remates over: 1/1 · +0.80 un" in txt


def test_totais_finais_bd_exige_as_duas_equipas(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod, db as db_mod
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()

    def stat(ev, team, cantos):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa="X", era_casa=team == 1,
                                     data_jogo=NOV, resultado="W", cantos=cantos)
    with abrir_sessao() as s:
        s.add_all([stat(1, 1, 6), stat(1, 2, 4), stat(2, 1, 5)])
    with abrir_sessao() as s:
        totais = ae.totais_finais_bd(s, {(1, "cantos"), (2, "cantos")})
    db_mod._engine = None
    db_mod._SessionLocal = None
    assert totais == {(1, "cantos"): 10.0}


# ─── Mes por defeito do relatorio ───────────────────────────────────────

def test_relatorio_no_inicio_do_mes_mostra_o_mes_que_acabou():
    from relatorio_mensal import mes_por_defeito
    assert mes_por_defeito(datetime(2026, 11, 1, 7)) == (10, 2026)
    assert mes_por_defeito(datetime(2027, 1, 3)) == (12, 2026)
    assert mes_por_defeito(datetime(2026, 11, 15)) == (11, 2026)


# ─── Aposta de referencia das projeccoes ────────────────────────────────

def _proj(actual, total, linhas):
    return projecao_live.ProjecaoResumo(
        mercado="cantos", minuto=55, valor_atual_total=actual, projecao_final_total=total,
        valor_atual_casa=actual, valor_atual_fora=0, projecao_final_casa=total,
        projecao_final_fora=0,
        linhas_total=[projecao_live.LinhaOdds(linha=l, over_justa=0, under_justa=0, prob_over=p)
                      for l, p in linhas])


def test_referencia_usa_a_probabilidade_da_propria_linha():
    """Antes: linha central 9.5 (~50%) com a prob da linha 8.5 (62%)."""
    ref = projecao_live.escolher_referencia(_proj(5, 9.3, [(8.5, 0.62), (9.5, 0.45), (10.5, 0.26)]))
    assert ref is not None
    assert (ref.lado, ref.linha, ref.prob) == ("under", 10.5, 0.74)
    assert ref.odd_justa == 1.35


def test_referencia_ignora_linhas_que_ja_nao_pagam():
    ref = projecao_live.escolher_referencia(_proj(5, 9.3, [(8.5, 0.92), (9.5, 0.70), (10.5, 0.40)]))
    assert ref is not None and (ref.lado, ref.linha) == ("over", 9.5)


def test_referencia_aplica_a_calibracao():
    """Com o historico a dizer que os UNDER prometem demais, o over passa a frente."""
    def calibrar(p, lado):
        return p - 0.20 if lado == "under" else p
    ref = projecao_live.escolher_referencia(
        _proj(5, 9.3, [(8.5, 0.62), (9.5, 0.45), (10.5, 0.26)]), calibrar=calibrar)
    assert ref is not None and (ref.lado, ref.linha, ref.prob) == ("over", 8.5, 0.62)


def test_formatar_referencia_pede_odd_acima_da_justa():
    ref = projecao_live.ReferenciaProjecao(linha=8.5, lado="over", prob=0.7, odd_justa=1.43)
    txt = projecao_live.formatar_referencia(ref)
    assert "OVER 8.5" in txt and "70%" in txt and "≥ 1.50" in txt


def test_narrativa_usa_a_projeccao_do_modelo():
    from apostas.alertas.mega_narrativa import narrativa_final
    s = {"mercado": "cantos", "lado": "over", "linha": 8.5, "minuto": 55, "marcador": "0-0",
         "corners_c": 3, "corners_f": 2, "prob_modelo": 0.7}
    txt = narrativa_final({**s, "projecao_total": 11.2})
    assert "projeccao 11.2 cantos; OVER 8.5 precisa de pelo menos 4 cantos nos 35 min" in txt
    # sem projeccao do modelo: ritmo 5 em 55' -> 8.2
    assert "projeccao 8.2" in narrativa_final(s)


def _correr_projeccao(monkeypatch, sot, cantos, cortes=None):
    import scanner_live_v2 as sc
    from types import SimpleNamespace
    from apostas.modelos.calibrador_mercado import Calibrador

    enviadas, registadas = [], []
    monkeypatch.setattr(sc, "_ambas_fichas_fortes", lambda *a: True)
    monkeypatch.setattr(sc, "_mult_projecao", lambda m, j: 1.0)
    monkeypatch.setattr(sc, "_gravar_projecao_db", lambda *a: None)
    monkeypatch.setattr(sc, "_calibrador_live", lambda: Calibrador())
    from apostas.alertas.corte_mercados import Cortes
    monkeypatch.setattr(sc, "_cortes_live", lambda: cortes or Cortes())
    monkeypatch.setattr(sc, "telegram_cliente",
                        lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sc.alertas_enviados, "registar",
                        lambda **kw: registadas.append(kw))
    sc._projecoes_enviadas.clear()
    sc._primeiro_minuto_visto.clear()
    estado = SimpleNamespace(event_id=77, minuto=56, liga="Serie A", home="Lecce", away="Genoa",
                             home_id=None, away_id=None, marcador_casa=0, marcador_fora=0,
                             shots_on_target_casa=sot[0], shots_on_target_fora=sot[1],
                             corners_casa=cantos[0], corners_fora=cantos[1])
    prev = SimpleNamespace(projeccao_sot_casa=sot[2], projeccao_sot_fora=sot[3],
                           projeccao_cantos_casa=cantos[2], projeccao_cantos_fora=cantos[3])
    sc._enviar_projecoes_jogo(estado, prev, enviar_telegram=True, min_estrelas=1)
    return enviadas, registadas


def test_scanner_projeccao_enviada_traz_aposta_de_referencia_e_fica_registada(monkeypatch):
    enviadas, registadas = _correr_projeccao(monkeypatch, (3, 2, 5.5, 4.0), (4, 3, 6.5, 5.0))
    assert enviadas and len(enviadas) == len(registadas)
    assert all("Aposta de referência" in m for m in enviadas)
    r = registadas[0]
    assert r["tipo"] == "projeccao" and r["event_id"] == 77
    assert r["lado"] in ("over", "under") and r["linha"] % 1 == 0.5 and r["odd"] > 1.0
