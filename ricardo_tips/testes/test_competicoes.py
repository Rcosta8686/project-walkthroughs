"""So jogos oficiais de clubes: amigaveis, seleccoes e formacao ficam de fora de tudo."""
from __future__ import annotations

import csv
from datetime import datetime

import pytest

from apostas.dados_referencia.competicoes import motivo_exclusao, motivo_por_nome

AGORA = datetime(2026, 11, 7, 20)


@pytest.mark.parametrize("nome", [
    "UEFA Champions League", "UEFA Champions League, Qualification", "UEFA Europa League",
    "UEFA Europa League, Qualification", "UEFA Conference League", "Liga Portugal Betclic",
    "Liga Portugal 2", "Taça de Portugal", "Taça da Liga", "Premier League", "Championship",
    "FA Cup", "EFL Cup", "LaLiga", "LaLiga 2", "Copa del Rey", "Serie A", "Serie B",
    "Coppa Italia", "Bundesliga", "2. Bundesliga", "DFB-Pokal", "Ligue 1", "Coupe de France",
    "Eredivisie", "Eerste Divisie", "Brasileirão Série A"])
def test_competicoes_oficiais_de_clubes_passam(nome):
    assert motivo_por_nome(nome) is None


def test_nenhuma_liga_que_seguimos_fica_de_fora():
    from apostas.dados_referencia.ligas_prioritarias import LIGAS_PRIORITARIAS
    from apostas.ingestao.sofascore_live import LIGAS_SOFASCORE_IDS
    assert [n for n in LIGAS_SOFASCORE_IDS if motivo_por_nome(n)] == []
    assert [l.nome for l in LIGAS_PRIORITARIAS if motivo_por_nome(l.nome)] == []


@pytest.mark.parametrize("nome,motivo", [
    ("Club Friendly Games", "amigavel"), ("International Friendly Games", "amigavel"),
    ("World Cup Qual. UEFA", "selecao"), ("UEFA Nations League", "selecao"),
    ("UEFA Euro 2024", "selecao"), ("European Championship Qual.", "selecao"),
    ("Africa Cup of Nations", "selecao"), ("Copa América", "selecao"),
    ("UEFA Youth League", "formacao"), ("Premier League 2, Division 1", "formacao"),
    ("Campionato Primavera 1", "formacao"), ("U19 Bundesliga", "formacao")])
def test_amigaveis_seleccoes_e_formacao_ficam_fora(nome, motivo):
    assert motivo_por_nome(nome) == motivo


def _ev(torneio="Liga Portugal", nacional=False):
    return {"homeTeam": {"name": "A", "national": nacional}, "awayTeam": {"name": "B"},
            "tournament": {"name": torneio, "uniqueTournament": {"name": torneio}}}


def test_jogo_de_seleccoes_pela_marca_do_sofascore():
    assert motivo_exclusao(_ev("Qualquer coisa", nacional=True)) == "selecao"
    assert motivo_exclusao(_ev()) is None
    assert motivo_exclusao(_ev("Club Friendly Games")) == "amigavel"


def test_listagem_ao_vivo_tira_os_jogos_que_nao_contam():
    from apostas.ingestao.sofascore_live import so_jogos_oficiais
    evs = [_ev(), _ev("Club Friendly Games"), _ev("X", nacional=True), _ev("UEFA Europa League")]
    assert [e["tournament"]["name"] for e in so_jogos_oficiais(evs)] == [
        "Liga Portugal", "UEFA Europa League"]


def test_fichas_nao_guardam_amigaveis(monkeypatch):
    from apostas.modelos import historico_equipas, historico_jogadores
    ev = dict(_ev("Club Friendly Games"), status={"code": 100}, startTimestamp=1)
    pedidos = []
    for mod in (historico_equipas, historico_jogadores):
        monkeypatch.setattr(mod, "_get_json", lambda p: pedidos.append(p) or {"event": ev})
    assert historico_equipas.gravar_jogo_terminado(5) == 0
    assert historico_jogadores.gravar_jogadores_terminado(5) == 0
    assert pedidos == ["/event/5", "/event/5"]     # nem pede as estatisticas


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


def _csv(path, campos, linhas):
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)


def test_limpeza_apaga_da_bd_e_do_csv(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.limpeza_competicoes import limpar
    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ

    def eej(ev, team, liga):
        return EEJ(sofascore_event_id=ev, sofascore_team_id=team, nome_equipa="T",
                   era_casa=team % 2 == 1, data_jogo=AGORA, resultado="D", liga=liga)
    with abrir_sessao() as s:
        s.add_all([eej(1, 1, "Club Friendly Games"), eej(1, 2, "Club Friendly Games"),
                   eej(2, 3, "Liga Portugal"), eej(2, 4, "Liga Portugal"),
                   eej(3, 5, "UEFA Nations League"), eej(3, 6, "UEFA Nations League")])
        s.add(Snap(event_id=1, ts=AGORA, home="A", away="B", liga="Club Friendly Games",
                   minuto=60, marcador_casa=0, marcador_fora=0, yellow_casa=0, yellow_fora=0))
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    _csv(tracking / "sugestoes_live_v2.csv", ["alert_id", "event_id", "liga", "mercado"], [
        {"alert_id": "1", "event_id": "1", "liga": "Club Friendly Games", "mercado": "cantos"},
        {"alert_id": "2", "event_id": "2", "liga": "Liga Portugal", "mercado": "cantos"},
        {"alert_id": "3", "event_id": "9", "liga": "International Friendly Games",
         "mercado": "golos"}])
    _csv(tracking / "odds_casa.csv", ["event_id", "liga"], [
        {"event_id": "9", "liga": ""}, {"event_id": "2", "liga": "Liga Portugal"}])

    so_ver = limpar(aplicar=False, tracking=tracking)
    assert so_ver.jogos == 3 and so_ver.linhas_csv == 2
    with abrir_sessao() as s:
        assert s.query(EEJ).count() == 6                # nada apagado

    r = limpar(tracking=tracking)
    assert r.jogos == 3 and r.linhas_bd == {"fichas das equipas": 4, "momentos ao vivo": 1}
    assert r.competicoes == {"Club Friendly Games": 1, "UEFA Nations League": 1,
                             "International Friendly Games": 1}
    with abrir_sessao() as s:
        assert {e.sofascore_event_id for e in s.query(EEJ).all()} == {2}
        assert s.query(Snap).count() == 0
    ficam = list(csv.DictReader((tracking / "sugestoes_live_v2.csv").open(encoding="utf-8")))
    assert [r["alert_id"] for r in ficam] == ["2"]
    odds = list(csv.DictReader((tracking / "odds_casa.csv").open(encoding="utf-8")))
    assert [o["event_id"] for o in odds] == ["2"]
    guardadas = (tracking / "removidas_amigaveis_selecoes.csv").read_text(encoding="utf-8")
    assert "Club Friendly Games" in guardadas
    assert limpar(tracking=tracking).vazio            # a segunda vez nao ha nada
