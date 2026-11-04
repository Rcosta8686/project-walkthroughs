"""Resultados do dia: resolver so de hoje, estatisticas dos jogos com projeccao e lista."""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from apostas.relatorios import alertas_enviados as ae  # noqa: E402

DIA = datetime(2026, 11, 4)


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


def _escrever_csv(path: Path, linhas: list[dict]) -> None:
    campos = ["alert_id", "ts_scan", "event_id", "home", "away", "mercado", "lado", "linha",
              "odd", "resultado", "lucro", "resolvido_em"]
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)


def test_resolver_hoje_so_pergunta_pelos_jogos_de_hoje(tmp_path, monkeypatch):
    import resolver_sugestoes_live as rsl
    csv_path = tmp_path / "sug.csv"
    _escrever_csv(csv_path, [
        {"alert_id": "1", "ts_scan": "2026-10-20T20:00:00", "event_id": "100",
         "mercado": "golos", "lado": "over", "linha": "2.5", "odd": "1.9"},
        {"alert_id": "2", "ts_scan": "2026-11-04T19:00:00", "event_id": "200",
         "mercado": "golos", "lado": "over", "linha": "2.5", "odd": "1.9"},
    ])
    monkeypatch.setattr(rsl, "CSV_PATH", csv_path)
    perguntados = []
    monkeypatch.setattr(rsl, "_puxar_resultado_final",
                        lambda eid: perguntados.append(eid) or None)
    monkeypatch.setattr(rsl, "_stats_seguro", lambda *a: None)
    rsl.resolver(desde=DIA)
    assert perguntados == [200]


def test_args_do_resolver():
    import resolver_sugestoes_live as rsl
    assert rsl._args([]) is None
    assert rsl._args(["--desde", "2026-11-01"]) == datetime(2026, 11, 1)
    hoje = rsl._args(["--hoje"])
    assert hoje is not None and (hoje.hour, hoje.minute) == (0, 0)


def test_grava_estatisticas_so_dos_jogos_com_projeccao_sem_elas(bd, tmp_path, monkeypatch):
    import resolver_sugestoes_live as rsl
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo, ProjecaoLive

    def proj(ev):
        return ProjecaoLive(sofascore_event_id=ev, minuto=55, score_casa=0, score_fora=0,
                            nome_casa="A", nome_fora="B", mercado="cantos",
                            valor_atual_total=5, projecao_final_total=9, data_envio=DIA)

    def stat(ev, team):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa="X", era_casa=team == 1,
                                     data_jogo=DIA, resultado="W", cantos=5)
    with abrir_sessao() as s:
        s.add_all([proj(1), stat(1, 1), stat(1, 2),   # ja tem estatisticas
                   proj(2),                          # falta
                   proj(3)])                         # ja tratado neste resolver
    env = tmp_path / "env.csv"
    ae.registar(tipo="projeccao", ts=DIA.replace(hour=20), path=env,
                event_id=4, mercado="sot", lado="over", linha=8.5, odd=1.5)
    monkeypatch.setattr(ae, "caminho", lambda: env)
    gravados = []
    monkeypatch.setattr(rsl.historico_equipas, "gravar_jogo_terminado",
                        lambda eid: gravados.append(eid) or 2)
    assert rsl._stats_jogos_com_projeccao(DIA, {3}) == 2
    assert gravados == [2, 4]


def test_lista_do_dia_por_ordem_com_resultado(tmp_path):
    env = tmp_path / "env.csv"
    sug = tmp_path / "sug.csv"
    _escrever_csv(sug, [
        {"alert_id": "7", "ts_scan": "2026-11-04T18:00:00", "event_id": "300",
         "home": "Benfica", "away": "Porto", "mercado": "cartoes", "lado": "over",
         "linha": "4.5", "odd": "1.85", "resultado": "W", "lucro": "0.85"},
    ])
    ae.registar(tipo="projeccao", ts=DIA.replace(hour=21, minute=5), path=env,
                event_id=11, mercado="cantos", lado="under", linha=12.5, odd=1.42)
    ae.registar(7, DIA.replace(hour=18, minute=2), env)
    ae.registar(8, DIA.replace(day=3, hour=18), env)   # outro dia
    dicas = ae.listar(DIA, DIA.replace(day=5), sug, env, totais_proj={(11, "cantos"): 10.0})
    assert [d.tipo for d in dicas] == ["alerta", "projeccao"]
    txt = ae.formatar_lista(dicas, {11: ("Lecce", "Genoa")})
    assert txt.splitlines() == [
        "✅ 18:02 Benfica - Porto · 🟨 cartoes OVER 4.5 @1.85 → +0.85",
        "✅ 21:05 Lecce - Genoa · 🚩 cantos UNDER 12.5 @1.42 (proj.) → +0.42",
    ]


def test_texto_do_dia(bd, tmp_path, monkeypatch):
    import relatorio_dia
    env = tmp_path / "env.csv"
    ae.registar(tipo="projeccao", ts=DIA.replace(hour=21), path=env,
                event_id=11, mercado="cantos", lado="over", linha=8.5, odd=1.5)
    monkeypatch.setattr(ae, "caminho", lambda: env)
    monkeypatch.setattr(ae, "totais_finais_bd", lambda s, ch: {(11, "cantos"): 7.0})
    monkeypatch.setattr(relatorio_dia, "_nomes_jogos", lambda s, ids: {11: ("Lecce", "Genoa")})
    txt = relatorio_dia.texto_do_dia(DIA)
    assert "Resultados de 04/11/2026" in txt
    assert "0✅ 1❌" in txt
    assert "❌ 21:00 Lecce - Genoa · 🚩 cantos OVER 8.5 @1.50 (proj.) → -1.00" in txt
