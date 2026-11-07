"""Precisao por equipa, mini-cerebro do treinador e substituicoes."""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.modelos import correcao_ritmo as cr
from apostas.modelos import perfil_treinador as pt
from apostas.modelos import precisao_equipas as pe

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 22)


def _prev(**kw):
    base = dict(lam_golos_casa_rest=0.6, lam_golos_fora_rest=0.3,
                lam_cartoes_amarelos_restantes=2.0, projeccao_cantos_casa=8.0,
                projeccao_cantos_fora=4.0, projeccao_shots_casa=15.0, projeccao_shots_fora=8.0,
                projeccao_sot_casa=7.0, projeccao_sot_fora=3.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _estado(**kw):
    from apostas.ingestao.sofascore_live import EstadoLive
    e = EstadoLive(event_id=1, home="Porto", away="Benfica", liga="Liga Portugal", minuto=70,
                   estado="2H", marcador_casa=0, marcador_fora=1, shots_total_casa=11,
                   shots_total_fora=6, shots_on_target_casa=5, shots_on_target_fora=2,
                   corners_casa=6, corners_fora=3, yellow_cards_casa=1, yellow_cards_fora=1,
                   fouls_casa=12, fouls_fora=8)
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def test_restantes_de_cada_equipa():
    e, p = _estado(), _prev()
    assert pe.restantes(p, e, "remates_a_baliza") == (2.0, 1.0)
    assert pe.restantes(p, e, "cantos") == (2.0, 1.0)
    assert pe.restantes(p, e, "golos") == (0.6, 0.3)
    c, f = pe.restantes(p, e, "cartoes")
    assert c + f == pytest.approx(2.0) and c > f      # casa a perder e com mais faltas


def test_situacao_e_postura():
    assert pe.situacao(2, 1) == "a ganhar" and pe.situacao(1, 1) == "empate"
    assert pe.situacao(0, 1) == "a perder"
    assert pe.postura(2, 0) == "ofensiva" and pe.postura(0, 1) == "defensiva"
    assert pe.postura(1, 1) == "neutra" and pe.postura(None, 0) is None


def _pares(n=900, seed=5, efeito_sit=True, treinador=False):
    """Momentos com previsoes por equipa: quem perde faz 40% mais do que o
    modelo diz e quem ganha 30% menos; com treinador, o fator guardado no par
    e o que explica o resto."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        mc, mf = rnd.choice([(0, 0), (1, 0), (0, 1), (2, 1), (1, 2)])
        par = {"mercado": "remates_a_baliza", "janela": 60, "event_id": i,
               "data": AGORA - timedelta(hours=n - i), "mc": mc, "mf": mf,
               "home": f"C{i}", "away": f"F{i}", "c_atual": 3.0, "f_atual": 2.0}
        for lado, outro in (("c", "f"), ("f", "c")):
            resto = rnd.uniform(1.0, 5.0)
            sit = pe.situacao(par[f"m{lado}"], par[f"m{outro}"])
            ef = {"a perder": 1.4, "a ganhar": 0.7, "empate": 1.0}[sit] if efeito_sit else 1.0
            tr = rnd.choice([0.6, 1.0, 1.5]) if treinador else 1.0
            par[f"{lado}_antigo"] = resto
            par[f"{lado}_treinador"] = tr
            par[f"{lado}_postura"] = None
            par[f"{lado}_real"] = max(0.0, round(resto * ef * tr + rnd.gauss(0, 0.4)))
        out.append(par)
    return out


def test_aprende_a_situacao_de_cada_equipa_e_erra_menos():
    p = pe.calcular(_pares(), cr.CorrecaoRitmo(), agora=AGORA)
    m = "remates_a_baliza"
    assert m in p.ativos and p.ganhos[m]["situacao"] > 0.05
    assert p.mults[m]["a perder"] > 1.25 and p.mults[m]["a ganhar"] < 0.8
    assert p.teste[m].mae < p.teste_sem[m].mae
    assert p.teste[m].taxa_dentro1 > p.teste_sem[m].taxa_dentro1
    assert m not in p.ativos_postura                 # ainda sem substituicoes guardadas
    assert p.historia[-1]["data"] == "2026-11-07"
    lo, hi = p.intervalo(m, 62)
    assert lo < 0 < hi


def test_camada_do_treinador_so_entra_se_ajudar():
    sem = pe.calcular(_pares(efeito_sit=False), cr.CorrecaoRitmo(), agora=AGORA)
    assert "remates_a_baliza" not in sem.ativos_treinador   # fator 1.0 em todos: nao ajuda
    com = pe.calcular(_pares(efeito_sit=False, treinador=True), cr.CorrecaoRitmo(), agora=AGORA)
    assert "remates_a_baliza" in com.ativos_treinador
    assert com.ganhos["remates_a_baliza"]["treinador"] > 0.1


def test_sem_efeito_nenhuma_camada_liga():
    p = pe.calcular(_pares(efeito_sit=False), cr.CorrecaoRitmo(), agora=AGORA)
    assert p.ativos == [] and p.ativos_treinador == []


def test_curva_de_aprendizagem_guarda_os_dias(tmp_path):
    f = tmp_path / "p.json"
    pe.recalcular_e_gravar(_pares(), cr.CorrecaoRitmo(), path=f)
    ontem = pe.ler(f)
    ontem.historia[-1]["data"] = "2026-11-06"
    ontem.historia[-1]["remates_a_baliza"]["dentro1"] = 0.40
    pe.gravar(ontem, f)
    p = pe.recalcular_e_gravar(_pares(), cr.CorrecaoRitmo(), path=f)
    assert [h["data"] for h in p.historia][-2:] == ["2026-11-06", pe.datetime.now().date().isoformat()]
    lido = pe.ler(f)
    assert lido.teste.keys() == p.teste.keys() and lido.mults == p.mults
    txt = pe.formatar(lido)
    assert "dentro de ±1 em" in txt and "desde 11-06" in txt
    assert "+ situacao (a ganhar/empate/a perder)" in txt


def test_texto_ao_vivo_por_equipa():
    p = pe.calcular(_pares(), cr.CorrecaoRitmo(), agora=AGORA)
    e = _estado()
    txt = pe.texto_equipas(e, _prev(), p, "remates_a_baliza")
    assert txt.startswith("📏 Ate ao fim: Porto ") and " · Benfica " in txt
    assert "remates a baliza — acertamos ±1 em" in txt
    pf = pe.prever_final(e, _prev(), p, "remates_a_baliza")
    assert pf is not None
    # Porto a perder: mais do que os 2 que o modelo dizia; Benfica a ganhar: menos.
    assert pf["casa"][0] > 7.0 and pf["fora"][0] < 3.0
    assert pe.texto_equipas(_estado(corners_casa=None, corners_fora=None), _prev(),
                            p, "cantos") == ""        # sem estatisticas: nada


def test_exemplos_dos_ultimos_jogos():
    p = pe.calcular(_pares(), cr.CorrecaoRitmo(), agora=AGORA)
    assert p.exemplos and ("previ" in p.exemplos[0]) and p.exemplos[0][0] in "✅❌"


# --- mini-cerebro do treinador --------------------------------------------------

def _eej(ev, team, dias, res_1t, rem1, rem2):
    return SimpleNamespace(sofascore_event_id=ev, sofascore_team_id=team,
                           data_jogo=AGORA - timedelta(days=dias), golos_marcados=1,
                           golos_sofridos=0, golos_marcados_1t=1 if res_1t == "W" else 0,
                           golos_sofridos_1t=1 if res_1t == "L" else 0, resultado_1t=res_1t,
                           cantos_1t=3, cantos_2t=3, cards_amarelos_1t=1, cards_amarelos_2t=2,
                           remates_1t=rem1, remates_2t=rem2, sot_1t=2, sot_2t=2)


def test_treinador_que_recua_a_ganhar():
    linhas = []
    for i in range(600):  # equipas normais: 2a parte igual a 1a em qualquer situacao
        linhas.append(_eej(i, 100 + i % 30, 3 + i % 19, "WDL"[i % 3], 6, 6))
    for i in range(12):   # equipa 1: a ganhar ao intervalo deixa de rematar
        linhas.append(_eej(500 + i, 1, 3 + i, "W", 6, 2))
    for i in range(8):
        linhas.append(_eej(600 + i, 1, 20 + i, "L", 6, 7))
    perfis = pt.PerfisTreinador(linhas)
    f = perfis.fator(1, "remates", "a ganhar")
    assert f is not None and f.fator < 0.7 and f.n >= 10
    assert perfis.fator(1, "remates", "a perder").fator > f.fator
    assert perfis.fator(105, "remates", "a ganhar").fator == pytest.approx(1.0, abs=0.1)
    assert "recua para segurar" in pt.texto("Roma", "a ganhar", "remates", f)
    assert pt.texto("X", "a ganhar", "remates", perfis.fator(105, "remates", "a ganhar")) == ""
    # sem batota: so jogos antes da data
    antes = perfis.fator(1, "remates", "a ganhar", antes=AGORA - timedelta(days=12))
    assert antes is None or antes.n < f.n
    assert perfis.fator(999, "remates", "a ganhar") is None


# --- substituicoes --------------------------------------------------------------

def test_substituicoes_ofensivas_e_defensivas():
    from apostas.ingestao import sofascore_live as sl
    e = _estado()
    sl._preencher_substituicoes(e, {"incidents": [
        {"incidentType": "substitution", "isHome": True,
         "playerIn": {"position": "F"}, "playerOut": {"position": "D"}},
        {"incidentType": "substitution", "isHome": True,
         "playerIn": {"position": "M"}, "playerOut": {"position": "M"}},
        {"incidentType": "substitution", "isHome": False,
         "playerIn": {"position": "D"}, "playerOut": {"position": "F"}},
        {"incidentType": "card", "isHome": True}]})
    assert (e.substituicoes_casa, e.subs_ofensivas_casa, e.subs_defensivas_casa) == (2, 1, 0)
    assert (e.substituicoes_fora, e.subs_ofensivas_fora, e.subs_defensivas_fora) == (1, 0, 1)
    assert sl.tipo_substituicao({"playerIn": {}, "playerOut": {"position": "D"}}) == "igual"


def test_bd_antiga_ganha_as_colunas_novas(tmp_path, monkeypatch):
    import sqlite3

    from apostas.utils import config as config_mod, db as db_mod
    f = tmp_path / "velha.db"
    con = sqlite3.connect(f)
    con.execute("CREATE TABLE estado_live_snapshots (id INTEGER PRIMARY KEY, event_id INTEGER "
                "NOT NULL, jogo_id INTEGER, ts DATETIME NOT NULL, home VARCHAR(100) NOT NULL, "
                "away VARCHAR(100) NOT NULL, minuto INTEGER NOT NULL, marcador_casa INTEGER "
                "NOT NULL, marcador_fora INTEGER NOT NULL)")
    con.execute("INSERT INTO estado_live_snapshots (event_id, ts, home, away, minuto, "
                "marcador_casa, marcador_fora) VALUES (7, '2026-11-01', 'A', 'B', 30, 0, 0)")
    con.commit()
    con.close()
    monkeypatch.setattr(config_mod, "db_path", lambda: f)
    db_mod._engine = None
    db_mod._SessionLocal = None
    try:
        db_mod.criar_schema()
        con = sqlite3.connect(f)
        cols = {r[1] for r in con.execute("PRAGMA table_info(estado_live_snapshots)")}
        assert {"subs_of_casa", "subs_def_fora", "corners_casa", "arbitro"} <= cols
        assert con.execute("SELECT event_id FROM estado_live_snapshots").fetchall() == [(7,)]
        con.close()
        db_mod.criar_schema()                         # segunda vez: nada a fazer
    finally:
        db_mod._engine = None
        db_mod._SessionLocal = None


# --- medicao nocturna com os campos por equipa ---------------------------------

@pytest.fixture
def bd(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    yield
    db_mod._engine = None
    db_mod._SessionLocal = None


def test_pares_tem_cada_equipa(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def eej(ev, team, casa, dias, sot):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa=f"T{team}", era_casa=casa,
                                     data_jogo=AGORA - timedelta(days=dias), resultado="D",
                                     golos_marcados=1, golos_sofridos=1, cantos=5,
                                     cards_amarelos=2, remates=12, sot=sot)
    with abrir_sessao() as s:
        s.add_all([eej(1, 10, True, 1, 9), eej(1, 11, False, 1, 2)])
        s.add(EstadoLiveSnapshot(event_id=1, ts=AGORA - timedelta(days=1), home="Porto",
                                 away="Benfica", liga="L", minuto=62, marcador_casa=0,
                                 marcador_fora=1, shots_total_casa=8, shots_total_fora=5,
                                 shots_on_target_casa=6, shots_on_target_fora=2,
                                 corners_casa=4, corners_fora=2, yellow_casa=1, yellow_fora=1,
                                 subs_of_casa=2, subs_def_casa=0, subs_of_fora=0,
                                 subs_def_fora=1))
    with abrir_sessao() as s:
        pares = cr.pares_da_bd(s, agora=AGORA)
    sot = next(p for p in pares if p["mercado"] == "remates_a_baliza")
    assert (sot["c_atual"], sot["f_atual"], sot["c_real"], sot["f_real"]) == (6.0, 2.0, 3.0, 0.0)
    assert (sot["mc"], sot["mf"], sot["home"]) == (0, 1, "Porto")
    assert sot["c_postura"] == "ofensiva" and sot["f_postura"] == "defensiva"
    assert {"c_antigo", "f_suave", "c_equipas", "c_treinador"} <= sot.keys()


def test_scanner_junta_equipas_treinador_e_substituicoes(monkeypatch):
    import scanner_live_v2 as sc
    p = pe.calcular(_pares(), cr.CorrecaoRitmo(), agora=AGORA)
    monkeypatch.setattr(sc, "_precisao_live", lambda: p)

    class _Perfis:
        def fator(self, eid, m, sit):
            return pt.FatorTreinador(fator=0.6, n=9, geral=0.9) if eid == 2 else None
    monkeypatch.setattr(sc, "_treinadores_live", lambda: _Perfis())
    e = _estado(home_id=1, away_id=2, subs_ofensivas_casa=2, subs_defensivas_fora=1)
    txt = sc._texto_equipas(e, _prev(), "sot")
    linhas = txt.split("\n")
    assert linhas[0].startswith("📏 Ate ao fim: Porto")
    assert linhas[1].startswith("🧑‍🏫 Benfica, quando esta a ganhar ao intervalo, recua")
    assert linhas[2] == "🔄 Substituicoes: Porto 2 ofensivas · Benfica 1 defensiva"
    assert sc._texto_equipas(e, _prev(), "1x2").startswith("📏")      # golos por equipa
    assert sc._texto_equipas(e, _prev(), "dupla") == ""


def test_maximo_possivel_poisson():
    # ritmo certo de 3.5 que faltam, 0 feitos: previsao 4, acerta ±1 se sair 3, 4 ou 5
    assert pe.p_dentro1(0, 3.5) == pytest.approx(0.5368, abs=0.001)
    # quase nada a faltar: quase certo
    assert pe.p_dentro1(2, 0.2) == pytest.approx(0.9825, abs=0.001)
    p = pe.calcular(_pares(), cr.CorrecaoRitmo(), agora=AGORA)
    m = p.teste["remates_a_baliza"]
    assert 0.3 < m.teto < 1.0
    txt = pe.formatar(p)
    assert "maximo possivel ~" in txt and "camadas: situacao" in txt
    assert "substituicoes: a juntar dados" in txt


def _pares_equipas(n=1500, seed=11):
    """20 equipas: umas acabam sempre mais fortes do que o modelo diz, outras menos."""
    rnd = random.Random(seed)
    efeito = {t: (0.6, 1.0, 1.6)[t % 3] for t in range(20)}
    out = []
    for i in range(n):
        tc, tf = rnd.sample(range(20), 2)
        par = {"mercado": "cantos", "janela": 30, "event_id": i,
               "data": AGORA - timedelta(hours=n - i), "mc": 0, "mf": 0, "home": f"T{tc}",
               "away": f"T{tf}", "c_atual": 2.0, "f_atual": 2.0, "c_id": tc, "f_id": tf}
        for lado, team in (("c", tc), ("f", tf)):
            resto = rnd.uniform(2.0, 5.0)
            par[f"{lado}_antigo"] = resto
            par[f"{lado}_treinador"] = 1.0
            par[f"{lado}_postura"] = None
            par[f"{lado}_real"] = max(0.0, round(resto * efeito[team] + rnd.gauss(0, 0.5)))
        out.append(par)
    return out


def test_aprende_o_erro_proprio_de_cada_equipa():
    p = pe.calcular(_pares_equipas(), cr.CorrecaoRitmo(), agora=AGORA)
    assert "cantos" in p.ativos_equipa and p.ganhos["cantos"]["equipa"] > 0.1
    assert p.mults_equipa["cantos"]["2"] > 1.3 and p.mults_equipa["cantos"]["0"] < 0.75
    e = _estado(home_id=2, away_id=0)
    pf = pe.prever_final(e, _prev(projeccao_cantos_casa=10.0, projeccao_cantos_fora=7.0),
                         p, "cantos")
    assert pf is not None and pf["casa"][0] - 6 > (pf["fora"][0] - 3) * 1.8
    assert "erro proprio de cada equipa" in pe.formatar(p)
