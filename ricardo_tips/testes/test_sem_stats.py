"""Jogos sem estatisticas no Sofascore: cantos/remates/cartoes desconhecidos, nao 0."""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.ingestao import sofascore_live as sl
from apostas.relatorios import sem_stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 18)

_EVENTO = {"event": {"homeTeam": {"name": "Chaves", "id": 1}, "awayTeam": {"name": "Penafiel",
                                                                          "id": 2},
                     "tournament": {"name": "Liga Portugal 2"}, "status": {"code": 100},
                     "homeScore": {"current": 2}, "awayScore": {"current": 3}}}
_INCIDENTES = {"incidents": [
    {"incidentType": "card", "incidentClass": "yellow", "isHome": True, "time": 20},
    {"incidentType": "card", "incidentClass": "yellow", "isHome": True, "time": 50},
    {"incidentType": "card", "incidentClass": "yellow", "isHome": False, "time": 60},
    {"incidentType": "card", "incidentClass": "red", "isHome": False, "time": 80},
    {"incidentType": "goal", "isHome": True, "time": 10},
]}
_STATS = {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [
    {"name": "Corner kicks", "home": "6", "away": "4"},
    {"name": "Total shots", "home": "12", "away": "9"},
    {"name": "Yellow cards", "home": "1", "away": "2"}]}]}]}


def _estado_do_sofascore(monkeypatch, stats, incidentes):
    respostas = {"/event/9": _EVENTO, "/event/9/statistics": stats,
                 "/event/9/incidents": incidentes}
    monkeypatch.setattr(sl, "_get_json", lambda path: respostas.get(path))
    monkeypatch.setattr(sl, "_preencher_contexto_pre", lambda e: None)
    estado = sl.obter_estado_live(9)
    assert estado is not None
    return estado


def test_jogo_sem_estatisticas_conta_cartoes_nos_incidentes(monkeypatch):
    e = _estado_do_sofascore(monkeypatch, None, _INCIDENTES)
    assert e.stats_ok is False and e.cartoes_ok is True
    assert (e.yellow_cards_casa, e.yellow_cards_fora, e.red_cards_fora) == (2, 1, 1)
    assert not sl.dados_disponiveis(e, "cantos") and not sl.dados_disponiveis(e, "remates")
    assert not sl.dados_disponiveis(e, "remates_a_baliza")
    assert sl.dados_disponiveis(e, "cartoes") and sl.dados_disponiveis(e, "golos")


def test_sem_estatisticas_nem_incidentes_cartoes_desconhecidos(monkeypatch):
    e = _estado_do_sofascore(monkeypatch, None, None)
    assert e.cartoes_ok is False and not sl.dados_disponiveis(e, "cartoes")


def test_jogo_com_estatisticas_usa_as_estatisticas(monkeypatch):
    e = _estado_do_sofascore(monkeypatch, _STATS, _INCIDENTES)
    assert e.stats_ok is True and e.corners_casa == 6
    assert (e.yellow_cards_casa, e.yellow_cards_fora) == (1, 2)   # das estatisticas
    assert sl.dados_disponiveis(e, "cantos") and sl.dados_disponiveis(e, "cartoes")


def test_estado_montado_a_mao_conta_como_disponivel():
    assert sl.dados_disponiveis(SimpleNamespace(), "cartoes")
    assert sl.dados_disponiveis(SimpleNamespace(corners_casa=0, corners_fora=0), "cantos")
    assert sl.dados_disponiveis(SimpleNamespace(), "golos")


def _estado_live(minuto=70, stats=True, cartoes_ok=True):
    e = sl.EstadoLive(event_id=1, home="A", away="B", liga="L", minuto=minuto, estado="2H",
                      marcador_casa=0, marcador_fora=0)
    if stats:
        e.shots_total_casa, e.shots_total_fora = 9, 8
        e.shots_on_target_casa, e.shots_on_target_fora = 3, 3
        e.corners_casa, e.corners_fora = 4, 3
    e.stats_ok, e.cartoes_ok = stats, cartoes_ok
    return e


def test_analista_nao_inventa_dicas_sem_estatisticas():
    from apostas.alertas import analista
    from apostas.modelos import live_predictor
    e = _estado_live(stats=False, cartoes_ok=False)
    sinais = analista.gerar_sinais(e, live_predictor.prever(e), prob_minima=0.5)
    assert not [s for s in sinais if s.mercado in ("cantos", "cartoes", "remates",
                                                    "remates_a_baliza")]
    com = _estado_live()
    sinais = analista.gerar_sinais(com, live_predictor.prever(com), prob_minima=0.5)
    assert {s.mercado for s in sinais} & {"cantos", "cartoes", "remates", "remates_a_baliza"}


def test_cartao_no_fim_nao_sai_sem_cartoes_fiaveis(monkeypatch):
    import scanner_live_v2 as sc
    monkeypatch.setattr(sc, "_em_cooldown", lambda *a: False)
    chamadas = []
    monkeypatch.setattr(sc, "_historia_cartoes_live", lambda: chamadas.append(1))
    assert sc._alerta_cartoes_fim(_estado_live(minuto=75, cartoes_ok=False), None) is False
    assert chamadas == []


# --- resolver ------------------------------------------------------------------

def test_resolver_nao_conta_zeros_falsos(monkeypatch):
    import resolver_sugestoes_live as rsl
    e = _estado_live(minuto=95, stats=False, cartoes_ok=True)
    e.marcador_casa, e.marcador_fora, e.yellow_cards_casa = 2, 3, 2
    monkeypatch.setattr(rsl.sofascore_live, "obter_estado_live", lambda eid: e)
    res = rsl._puxar_resultado_final(9)
    assert res is not None
    assert res["cantos_total"] is None and res["shots_total"] is None
    assert res["shots_on_target_total"] is None and res["cards_amarelos_total"] == 2
    assert rsl._wl_por_mercado("cantos", "under", "8.5", res) is None
    assert rsl._wl_por_mercado("remates", "over", "20.5", res) is None
    assert rsl._wl_por_mercado("cartoes", "over", "1.5", res) == "W"
    assert rsl._wl_por_mercado("golos", "over", "4.5", res) == "W"


def _csv_sugestoes(path: Path, linhas: list[dict]) -> None:
    campos = ["alert_id", "ts_scan", "event_id", "home", "away", "mercado", "lado", "linha",
              "odd", "resultado", "lucro", "resolvido_em"]
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)


def test_resolver_espera_e_depois_fecha_como_sd(tmp_path, monkeypatch):
    import resolver_sugestoes_live as rsl
    f = tmp_path / "sug.csv"
    recente = (AGORA - timedelta(hours=2)).isoformat(timespec="seconds")
    antiga = (AGORA - timedelta(hours=30)).isoformat(timespec="seconds")
    _csv_sugestoes(f, [
        {"alert_id": "1", "ts_scan": antiga, "event_id": "9", "mercado": "cantos",
         "lado": "under", "linha": "8.5", "odd": "1.8"},
        {"alert_id": "2", "ts_scan": recente, "event_id": "10", "mercado": "cantos",
         "lado": "under", "linha": "8.5", "odd": "1.8"},
        {"alert_id": "3", "ts_scan": antiga, "event_id": "9", "mercado": "golos",
         "lado": "over", "linha": "2.5", "odd": "1.9"},
    ])
    monkeypatch.setattr(rsl, "CSV_PATH", f)
    monkeypatch.setattr(rsl, "agora_utc", lambda: AGORA)
    for nome in ("_stats_seguro", "_garantir_tabelas"):
        monkeypatch.setattr(rsl, nome, lambda *a: None)
    monkeypatch.setattr(rsl.historico_equipas, "gravar_jogo_terminado", lambda eid: 0)
    monkeypatch.setattr(rsl.historico_jogadores, "gravar_jogadores_terminado", lambda eid: 0)
    monkeypatch.setattr(sem_stats, "jogos_sem_stats", lambda s: {})
    sem = {"golos_total": 5, "golos_casa": 2, "golos_fora": 3, "btts": True,
           "cantos_total": None, "cards_amarelos_total": 3, "cards_vermelhos_total": 0,
           "shots_total": None, "shots_on_target_total": None}
    monkeypatch.setattr(rsl, "_puxar_resultado_final", lambda eid: sem)
    rsl.resolver()
    with f.open(encoding="utf-8") as fp:
        rows = {r["alert_id"]: r for r in csv.DictReader(fp)}
    assert rows["1"]["resultado"] == "SD" and rows["1"]["lucro"] == ""
    assert rows["2"]["resultado"] == ""          # ainda pode chegar a estatistica
    assert rows["3"]["resultado"] == "W"


# --- reparacao dos resultados antigos -------------------------------------------

def test_repara_so_mercados_sem_dados_desses_jogos(tmp_path):
    f = tmp_path / "sug.csv"
    _csv_sugestoes(f, [
        {"alert_id": "1", "event_id": "9", "mercado": "cantos", "lado": "under",
         "resultado": "W", "lucro": "0.8"},
        {"alert_id": "2", "event_id": "9", "mercado": "remates", "lado": "over",
         "resultado": "L", "lucro": "-1"},
        {"alert_id": "3", "event_id": "9", "mercado": "golos", "lado": "over",
         "resultado": "W", "lucro": "0.9"},
        {"alert_id": "4", "event_id": "9", "mercado": "cartoes", "lado": "over",
         "resultado": "W", "lucro": "0.9"},
        {"alert_id": "5", "event_id": "50", "mercado": "cantos", "lado": "under",
         "resultado": "W", "lucro": "0.8"},
        {"alert_id": "6", "event_id": "9", "mercado": "cantos", "lado": "over",
         "resultado": "", "lucro": ""},
    ])
    sem = {"9": {"cantos", "remates"}}            # cartoes vieram dos incidentes
    corr = sem_stats.reparar(f, sem=sem)
    assert corr.n == 2 and corr.por_grupo == {"under cantos (W)": 1, "over remates (L)": 1}
    assert "2 resultados antigos corrigidos" in corr.texto()
    with f.open(encoding="utf-8") as fp:
        rows = {r["alert_id"]: r for r in csv.DictReader(fp)}
    assert [rows[i]["resultado"] for i in "123456"] == ["SD", "SD", "W", "W", "W", ""]
    assert rows["1"]["lucro"] == ""
    assert sem_stats.reparar(f, sem=sem).n == 0     # idempotente


def test_so_ver_nao_escreve(tmp_path):
    f = tmp_path / "sug.csv"
    _csv_sugestoes(f, [{"alert_id": "1", "event_id": "9", "mercado": "cantos", "lado": "under",
                        "resultado": "W", "lucro": "0.8"}])
    assert sem_stats.reparar(f, sem={"9": {"cantos"}}, aplicar=False).n == 1
    assert "W" in f.read_text(encoding="utf-8")


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


def test_encontra_os_jogos_sem_estatisticas_na_bd(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ

    def eej(ev, team, **kw):
        return EEJ(sofascore_event_id=ev, sofascore_team_id=team, nome_equipa=f"T{team}",
                   era_casa=team % 2 == 1, data_jogo=AGORA, resultado="D", **kw)
    with abrir_sessao() as s:
        s.add_all([eej(1, 1), eej(1, 2),                                    # sem nada
                   eej(2, 3, cards_amarelos=2), eej(2, 4, cards_amarelos=1),  # so cartoes
                   eej(3, 5, cantos=5, remates=10, sot=3, cards_amarelos=2),
                   eej(3, 6, cantos=4, remates=8, sot=2, cards_amarelos=1)])
    with abrir_sessao() as s:
        sem = sem_stats.jogos_sem_stats(s)
    assert sem == {"1": {"cantos", "remates", "remates_a_baliza", "cartoes"},
                   "2": {"cantos", "remates", "remates_a_baliza"}}


def test_amarelos_dos_incidentes_para_a_bd():
    from apostas.modelos.historico_equipas import _amarelos_dos_incidentes
    assert _amarelos_dos_incidentes(_INCIDENTES) == (2, 1)


def test_csv_aceita_sd():
    from apostas.relatorios import csv_live
    ok = csv_live._VALIDADORES["resultado"][1]
    assert ok("SD") and ok("W") and not ok("X")
