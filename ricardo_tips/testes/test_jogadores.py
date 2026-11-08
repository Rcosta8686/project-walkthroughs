"""Mini-cerebro de cada jogador: remates, remates a baliza e amarelo."""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.ingestao import sofascore_live as sl
from apostas.modelos import jogadores as jg
from apostas.modelos import precisao_equipas as pe

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 8, 20)

LINEUPS = {
    "home": {"players": [
        {"player": {"id": 1, "name": "Avancado"}, "position": "F", "substitute": False,
         "statistics": {"minutesPlayed": 60, "totalScoringAttempts": 3,
                        "onTargetScoringAttempt": 2, "fouls": 1}},
        {"player": {"id": 2, "name": "Central"}, "position": "D", "substitute": False,
         "statistics": {"minutesPlayed": 60, "fouls": 3}},
        {"player": {"id": 3, "name": "Saiu"}, "position": "M", "substitute": False,
         "statistics": {"minutesPlayed": 55, "substitutionOut": True}},
        {"player": {"id": 4, "name": "Entrou"}, "position": "M", "substitute": True,
         "statistics": {"minutesPlayed": 5}},
        {"player": {"id": 5, "name": "Banco"}, "position": "D", "substitute": True,
         "statistics": {}},
    ]},
    "away": {"players": [
        {"player": {"id": 10, "name": "Medio"}, "position": "M", "substitute": False,
         "statistics": {"minutesPlayed": 60, "totalScoringAttempts": 1}},
    ]},
}


def test_jogadores_das_lineups():
    js = {j["id"]: j for j in sl.jogadores_das_lineups(LINEUPS)}
    assert set(js) == {1, 2, 3, 4, 10}                     # o do banco nao jogou
    assert (js[1]["remates"], js[1]["sot"], js[1]["pos"], js[1]["lado"]) == (3, 2, "F", "casa")
    assert js[2]["remates"] == 0 and js[2]["faltas"] == 3   # sem estatistica = 0
    assert js[3]["em_campo"] is False and js[4]["titular"] is False
    assert js[10]["lado"] == "fora"


def test_amarelo_vem_dos_incidentes():
    e = sl.EstadoLive(event_id=1, home="A", away="B", liga="L", minuto=60, estado="2H",
                      marcador_casa=0, marcador_fora=0)
    sl._preencher_cartoes(e, {"incidents": [
        {"incidentType": "card", "incidentClass": "yellow", "isHome": True,
         "player": {"id": 2, "name": "Central"}}]})
    sl._preencher_lineups(e, LINEUPS)
    js = {j["id"]: j for j in e.jogadores}
    assert js[2]["amarelo"] is True and js[1]["amarelo"] is False


def _ejj(pid, ev, dias, pos, minutos, rem, sot, cart=0, faltas=1, team=100):
    return SimpleNamespace(sofascore_player_id=pid, sofascore_event_id=ev, sofascore_team_id=team,
                           nome_jogador=f"J{pid}", posicao=pos,
                           data_jogo=AGORA - timedelta(days=dias), minutos=minutos,
                           remates=rem, sot=sot, cards_amarelos=cart, fouls_committed=faltas)


def _historico():
    linhas, totais = [], {}
    for i in range(10):
        ev = 1000 + i
        totais[(ev, 100)] = (12, 4, 2)
        linhas += [_ejj(1, ev, i + 1, "ST", 90, 4, 2),           # faz 1/3 dos remates
                   _ejj(2, ev, i + 1, "CB", 90, None, None, cart=1 if i % 2 else 0, faltas=2)]
    return jg.HistoricoJogadores(linhas, totais)


def test_perfil_parte_dos_remates_e_cartoes():
    h = _historico()
    a, c = h.perfil(1), h.perfil(2)
    assert a is not None and c is not None
    assert a.pos == "F" and a.quota_remates > 0.27 and a.quota_sot > 0.4
    assert c.pos == "D" and c.quota_remates < 0.03            # vazio = 0 remates
    assert c.cartoes90 > 0.35 > a.cartoes90
    novo = h.perfil(999, pos_hint="F")                       # sem jogos: o normal da posicao
    assert novo is not None and novo.quota_remates == pytest.approx(jg.PRIOR_POS["F"][0])
    assert h.perfil(1, antes=AGORA - timedelta(days=8)).n_jogos == 2


def _prev():
    return SimpleNamespace(lam_golos_casa_rest=0.5, lam_golos_fora_rest=0.4,
                           lam_golos_restantes=0.9, lam_cartoes_amarelos_restantes=2.0,
                           projeccao_cantos_casa=6.0, projeccao_cantos_fora=4.0,
                           lam_cantos_restantes=3.0, projeccao_shots_casa=15.0,
                           projeccao_shots_fora=9.0, lam_shots_restantes=9.0,
                           projeccao_sot_casa=6.0, projeccao_sot_fora=3.0,
                           lam_shots_on_target_restantes=4.0)


def _estado():
    e = sl.EstadoLive(event_id=7, home="Porto", away="Benfica", liga="L", minuto=60,
                      estado="2H", marcador_casa=0, marcador_fora=0, shots_total_casa=9,
                      shots_total_fora=6, shots_on_target_casa=3, shots_on_target_fora=2,
                      corners_casa=3, corners_fora=2, yellow_cards_casa=1, yellow_cards_fora=0,
                      fouls_casa=10, fouls_fora=8)
    e.stats_ok = e.cartoes_ok = True
    e.jogadores = sl.jogadores_das_lineups(LINEUPS)
    return e


def test_previsao_ao_vivo_de_cada_jogador():
    prevs = jg.prever_live(_estado(), _prev(), {}, _historico(), precisao=pe.Precisao())
    por = {(p.id, p.mercado): p for p in prevs}
    assert (3, "remates") not in por                         # saiu: nada
    assert por[(1, "sot")].lam > 3 * por[(2, "sot")].lam
    assert por[(1, "sot")].atual == 2 and 0 < por[(1, "sot")].p_mais(1) < 1
    assert por[(1, "sot")].p_total(2) == 1.0                 # ja tem 2
    assert por[(2, "cartao")].lam > por[(1, "cartao")].lam    # central com 3 faltas
    soma = sum(p.lam for p in prevs if p.mercado == "cartao" and p.lado == "casa")
    assert soma <= 2.0 * 0.85                                # nao reparte mais do que falta
    txt = jg.texto_live(prevs, "sot")
    assert txt.startswith("👤 Ate ao fim (a baliza): Avancado (2) +1 ")
    assert "Central" in jg.texto_live(prevs, "cartao") and "(3 faltas)" in jg.texto_live(prevs, "cartao")


def test_quem_ja_tem_amarelo_sai_do_mercado_do_cartao():
    e = _estado()
    for j in e.jogadores:
        if j["id"] == 2:
            j["amarelo"] = True
    prevs = jg.prever_live(e, _prev(), {}, _historico(), precisao=pe.Precisao())
    assert not [p for p in prevs if p.id == 2 and p.mercado == "cartao"]


def test_antes_do_jogo():
    lineup = [j for j in sl.jogadores_das_lineups(LINEUPS) if j["titular"]]
    prevs = jg.prever_pre_jogo(lineup, {}, _historico(), (14.0, 10.0), (5.0, 3.5))
    a = next(p for p in prevs if p.id == 1 and p.mercado == "remates")
    assert a.lam == pytest.approx(14.0 * _historico().perfil(1).quota_remates * 75 / 90)
    assert not [p for p in prevs if p.id == 4]               # suplente: fora


def _pares(n=900, fator=1.4, seed=3):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        lam = rnd.uniform(0.2, 2.0)
        real = 0
        limite, k, prob = pow(2.718281828, -lam * fator), 0, 1.0
        while True:   # Poisson(lam * fator)
            prob *= rnd.random()
            if prob <= limite:
                break
            k += 1
        real = k
        out.append({"mercado": "sot", "janela": 60, "lam": lam, "real": float(real),
                    "data": AGORA - timedelta(hours=n - i), "event_id": i})
    return out


def test_aprende_quando_o_modelo_preve_a_menos():
    m = jg.calcular(_pares(), agora=AGORA)
    assert "sot" in m.ativos and m.mults["sot"]["60"] > 1.2
    assert m.mult("sot", 62) == m.mults["sot"]["60"] and m.mult("cartao", 62) == 1.0
    assert m.brier["sot"][1] < m.brier["sot"][0]
    assert m.faixas["sot"] and m.n_teste["sot"] == 270


def test_alertas_so_de_jogador_so_com_provas():
    bem = jg.calcular(_pares(n=1500, fator=1.0, seed=5), agora=AGORA)
    assert bem.n_teste["sot"] >= jg.MIN_CONFIAVEL
    # nas probabilidades altas o que se promete bate com o real?
    altas = [f for k, f in bem.faixas["sot"].items() if k in ("70-85%", "85-100%")]
    if sum(f.n for f in altas) >= 50:
        assert bem.confiavel("sot")
    assert not jg.Medicao().confiavel("sot")
    txt = jg.formatar(bem)
    assert "a baliza (+1 ate ao fim)" in txt


def test_gravar_e_ler(tmp_path):
    m = jg.calcular(_pares(), agora=AGORA)
    jg.gravar(m, tmp_path / "j.json")
    lido = jg.ler(tmp_path / "j.json")
    assert lido.mults == m.mults and lido.ativos == m.ativos
    assert lido.faixas["sot"].keys() == m.faixas["sot"].keys()
    assert "Ainda sem jogos acabados" in jg.formatar(jg.Medicao())


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


def test_registo_e_medicao_com_a_bd(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    prevs = [jg.PrevJogador(1, "Avancado", "casa", "F", "sot", 1, 0.8),
             jg.PrevJogador(2, "Central", "casa", "D", "cartao", 0, 0.3)]
    with abrir_sessao() as s:
        assert jg.registar(s, 7, 62, prevs, {"casa": "Porto", "fora": "Benfica"}) == 2
    with abrir_sessao() as s:
        assert jg.registar(s, 7, 64, prevs, {"casa": "Porto"}) == 0   # mesma fase: nao repete
        s.add_all([EJJ(sofascore_player_id=1, sofascore_event_id=7, sofascore_team_id=100,
                       nome_jogador="Avancado", posicao="ST", data_jogo=AGORA, era_casa=1,
                       minutos=90, sot=3),
                   EJJ(sofascore_player_id=2, sofascore_event_id=7, sofascore_team_id=100,
                       nome_jogador="Central", posicao="CB", data_jogo=AGORA, era_casa=1,
                       minutos=90, cards_amarelos=1, minuto_amarelo=80),
                   EEJ(sofascore_event_id=7, sofascore_team_id=100, nome_equipa="Porto",
                       era_casa=1, data_jogo=AGORA, resultado="W", remates=14, sot=5)])
    with abrir_sessao() as s:
        pares = jg.pares_da_bd(s, agora=AGORA + timedelta(hours=3))
        priors = jg.priors_da_bd(s)
    assert sorted((p["mercado"], p["real"]) for p in pares) == [("cartao", 1.0), ("sot", 2.0)]
    assert priors == {}                                       # poucos jogos: normal de partida


def test_scanner_so_alerta_jogador_com_provas(monkeypatch):
    import scanner_live_v2 as sc
    enviados = []
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(
        enviar=lambda txt: enviados.append(txt)))
    monkeypatch.setattr(sc, "_em_cooldown", lambda *a: False)
    monkeypatch.setattr(sc, "_marcar_enviado", lambda *a: None)
    e = _estado()
    prevs = [jg.PrevJogador(1, "Avancado", "casa", "F", "sot", 1, 2.0)]
    monkeypatch.setattr(sc, "_medicao_jogadores_live", lambda: jg.Medicao())
    assert sc._alerta_jogadores(e, prevs) == 0 and enviados == []
    prova = jg.Medicao()
    monkeypatch.setattr(prova, "confiavel", lambda m, p_min=0.7: m == "sot")
    monkeypatch.setattr(sc, "_medicao_jogadores_live", lambda: prova)
    assert sc._alerta_jogadores(e, prevs) == 1
    assert enviados[0].startswith("👤 *JOGADOR* · Avancado (Porto)")
    assert "+1 remate a baliza ate ao fim" in enviados[0]


def test_multipla_de_jogadores():
    import multipla_jogadores as mj
    prevs = [jg.PrevJogador(1, "A", "casa", "F", "remates", 0, 2.2),   # 3+: 38%, 2+: 65%
             jg.PrevJogador(1, "A", "casa", "F", "sot", 0, 1.4),
             jg.PrevJogador(2, "B", "casa", "D", "cartao", 0, 0.7),
             jg.PrevJogador(3, "C", "fora", "M", "sot", 0, 0.1)]
    cands = mj.candidatas(prevs, "X–Y 20:00", 7)
    descr = {(c.jogador, c.descricao) for c in cands}
    assert ("A", "3+ remates") in descr and ("A", "2+ remates") in descr   # 38% e 65%
    assert ("B", "ve amarelo") in descr and not [c for c in cands if c.jogador == "C"]
    sel = mj.montar(cands, n=4, p_min=0.45)
    assert len({s.player_id for s in sel}) == len(sel) <= 2      # 1 por jogador, 2 por jogo
    maluca = mj.montar(cands, n=8, p_min=0.30, p_max=0.65, por_mercado=1,
                       sem_jogadores={sel[0].player_id})
    assert sel[0].player_id not in {s.player_id for s in maluca}
    assert len({s.mercado for s in maluca}) == len(maluca)        # 1 por mercado aqui
    txt = mj.texto_multipla("Teste", sel)
    assert "odd justa" in txt and txt.startswith("*Teste* (2 selecoes")
