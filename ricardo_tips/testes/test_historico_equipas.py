"""Testes para apostas/modelos/historico_equipas.py.

Usa engine in-memory + monkeypatch de abrir_sessao (padrao
test_clv_tracker). Testa parse + leitura + agregacao.
Nao testa fetch Sofascore (requer flaresolverr).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apostas.modelos import historico_equipas, perfil_equipa
from apostas.utils.schema import Base, EstatisticaEquipaJogo


@pytest.fixture
def engine_test(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class _Ctx:
        def __init__(self, eng):
            self._s = Session(eng)
        def __enter__(self):
            return self._s
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                try:
                    self._s.commit()
                except Exception:
                    self._s.rollback()
            else:
                self._s.rollback()
            self._s.close()

    monkeypatch.setattr(historico_equipas, "abrir_sessao", lambda: _Ctx(engine))
    monkeypatch.setattr(perfil_equipa, "abrir_sessao", lambda: _Ctx(engine))
    return engine


def test_parse_valor_formatos():
    assert historico_equipas._parse_valor("12") == 12.0
    assert historico_equipas._parse_valor("12 (5)") == 12.0
    assert historico_equipas._parse_valor("45%") == 45.0
    assert historico_equipas._parse_valor("300/350 (86%)") == 300.0
    assert historico_equipas._parse_valor(None) is None
    assert historico_equipas._parse_valor("") is None


def test_extrair_stats_periodos_separados():
    stats_json = {
        "statistics": [
            {
                "period": "ALL",
                "groups": [{
                    "groupName": "Shots",
                    "statisticsItems": [
                        {"name": "Total shots", "home": "15", "away": "10"},
                        {"name": "Shots on target", "home": "6", "away": "3"},
                    ],
                }],
            },
            {
                "period": "1ST",
                "groups": [{
                    "groupName": "Shots",
                    "statisticsItems": [
                        {"name": "Total shots", "home": "7", "away": "4"},
                        {"name": "Shots on target", "home": "3", "away": "1"},
                    ],
                }],
            },
            {
                "period": "2ND",
                "groups": [{
                    "groupName": "Shots",
                    "statisticsItems": [
                        {"name": "Total shots", "home": "8", "away": "6"},
                    ],
                }],
            },
        ]
    }
    s_home = historico_equipas._extrair_stats_equipa(stats_json, "home")
    assert s_home.remates == 15.0
    assert s_home.sot == 6.0
    assert s_home.remates_1t == 7.0
    assert s_home.sot_1t == 3.0
    assert s_home.remates_2t == 8.0

    s_away = historico_equipas._extrair_stats_equipa(stats_json, "away")
    assert s_away.remates == 10.0
    assert s_away.sot == 3.0


def _insere_jogos(engine_test, team_id, jogos_data):
    """Insere lista de dicts como linhas de EstatisticaEquipaJogo."""
    with Session(engine_test) as s:
        for d in jogos_data:
            s.add(EstatisticaEquipaJogo(sofascore_team_id=team_id, **d))
        s.commit()


def test_stats_recentes_e_contexto(engine_test):
    now = datetime.utcnow()
    team_id = 42
    jogos = []
    for i in range(5):
        jogos.append(dict(
            sofascore_event_id=1000 + i,
            nome_equipa="X", liga="L",
            data_jogo=now - timedelta(days=i),
            era_casa=1 if i % 2 == 0 else 0,
            golos_marcados=i,
            golos_sofridos=0 if i < 3 else 2,
            resultado="W" if i < 3 else "L",
            resultado_1t="D",
            remates=10 + i, sot=4, cantos=5, cards_amarelos=2, fouls=10,
        ))
    _insere_jogos(engine_test, team_id, jogos)

    rec = historico_equipas.stats_recentes_equipa(team_id, 3)
    assert len(rec) == 3
    assert rec[0].sofascore_event_id == 1000  # hoje

    perdeu = historico_equipas.stats_por_contexto(team_id, "L")
    assert len(perdeu) == 2


def test_calcular_perfil_do_historico_com_dados(engine_test):
    now = datetime.utcnow()
    team_id = 99
    jogos = []
    for i in range(3):
        jogos.append(dict(
            sofascore_event_id=2000 + i,
            nome_equipa="Y", liga="L",
            data_jogo=now - timedelta(days=i * 2),
            era_casa=1,
            golos_marcados=2, golos_sofridos=0,
            resultado="W", resultado_1t="W",
            remates=12, sot=5, cantos=6, cards_amarelos=1,
            remates_1t=5, remates_2t=7,
            cantos_1t=3, cantos_2t=3,
        ))
    for i in range(3):
        jogos.append(dict(
            sofascore_event_id=2010 + i,
            nome_equipa="Y", liga="L",
            data_jogo=now - timedelta(days=10 + i * 2),
            era_casa=0,
            golos_marcados=0, golos_sofridos=2,
            resultado="L", resultado_1t="L",
            remates=18, sot=6, cantos=7, cards_amarelos=3,
            remates_1t=7, remates_2t=11,
        ))
    _insere_jogos(engine_test, team_id, jogos)

    perfil = perfil_equipa.calcular_perfil_do_historico(
        team_id, "Y", "L", n_jogos=20,
    )
    assert perfil is not None
    assert perfil.n_jogos_analisados == 6
    assert perfil.remates_media_90 == pytest.approx(15.0, abs=0.1)
    assert perfil.remates_atras_90 == pytest.approx(18.0, abs=0.1)
    assert perfil.remates_2a_parte_media == pytest.approx(9.0, abs=0.1)
    assert perfil.notas is not None


def test_perfil_do_historico_sem_dados_devolve_none(engine_test):
    """Sem jogos minimos (<3), devolve None."""
    perfil = perfil_equipa.calcular_perfil_do_historico(
        team_id=999999, nome="Z", n_jogos=20,
    )
    assert perfil is None


def test_virar_jogos_pct(engine_test):
    now = datetime.utcnow()
    team_id = 101
    jogos = [
        # perdia 1T, ganhou (virou)
        dict(sofascore_event_id=3001, nome_equipa="V", liga="L",
             data_jogo=now, era_casa=1, golos_marcados=2, golos_sofridos=1,
             resultado="W", resultado_1t="L", remates=10),
        # empatava 1T, ganhou (virou)
        dict(sofascore_event_id=3002, nome_equipa="V", liga="L",
             data_jogo=now - timedelta(days=3), era_casa=0,
             golos_marcados=1, golos_sofridos=0,
             resultado="W", resultado_1t="D", remates=10),
        # perdia 1T, perdeu (nao virou)
        dict(sofascore_event_id=3003, nome_equipa="V", liga="L",
             data_jogo=now - timedelta(days=6), era_casa=1,
             golos_marcados=1, golos_sofridos=2,
             resultado="L", resultado_1t="L", remates=10),
    ]
    _insere_jogos(engine_test, team_id, jogos)

    perfil = perfil_equipa.calcular_perfil_do_historico(
        team_id, "V", "L", n_jogos=20,
    )
    assert perfil is not None
    assert perfil.virar_jogos_pct == pytest.approx(2 / 3, abs=0.05)
