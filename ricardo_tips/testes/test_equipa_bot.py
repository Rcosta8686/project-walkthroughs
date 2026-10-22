"""Testes Fase 4 do 2o Cerebro — comando /equipa no bot."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from apostas.alertas import bot, mensagens
from apostas.modelos.historico_equipas import encontrar_equipa_por_nome
from apostas.utils import config as config_mod, db as db_mod
from apostas.utils.schema import EstatisticaEquipaJogo
from apostas.utils.tempo import agora_utc


@pytest.fixture
def bd_com_equipas(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()

    hoje = agora_utc()
    with db_mod.abrir_sessao() as s:
        # Benfica — 10 jogos (5 casa 5 fora)
        for i in range(10):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=100, sofascore_event_id=1000 + i,
                nome_equipa="SL Benfica", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i * 3),
                era_casa=bool(i % 2),
                golos_marcados=2, golos_sofridos=1, resultado="W",
                remates=15.0, sot=5.0, cantos=6.0, cards_amarelos=2.0,
            ))
        # Sporting (nome contem "sport" → vai colidir com /equipa sport)
        for i in range(5):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=200, sofascore_event_id=2000 + i,
                nome_equipa="Sporting CP", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i),  # mais recente
                era_casa=True,
                golos_marcados=1, golos_sofridos=0, resultado="W",
                remates=12.0, sot=4.0, cantos=5.0, cards_amarelos=1.5,
            ))
    yield tmp_path
    db_mod.reset_engine()


# ─── Resolver ───────────────────────────────────────────────────

def test_encontrar_equipa_por_nome_exacto(bd_com_equipas):
    r = encontrar_equipa_por_nome("Benfica")
    assert len(r) == 1
    assert r[0][0] == 100
    assert "Benfica" in r[0][1]


def test_encontrar_equipa_por_nome_case_insensitive(bd_com_equipas):
    r = encontrar_equipa_por_nome("benfica")
    assert len(r) == 1
    assert r[0][0] == 100


def test_encontrar_equipa_sem_match_devolve_vazio(bd_com_equipas):
    assert encontrar_equipa_por_nome("ChelseaFC") == []


def test_encontrar_equipa_query_vazia_devolve_vazio(bd_com_equipas):
    assert encontrar_equipa_por_nome("") == []
    assert encontrar_equipa_por_nome("   ") == []


# ─── /equipa handler ────────────────────────────────────────────

def test_equipa_sem_args_devolve_erro(bd_com_equipas):
    resposta = bot.comando_equipa([])
    assert "Uso:" in resposta
    assert "/equipa" in resposta


def test_equipa_devolve_ficha_quando_match_unico(bd_com_equipas):
    resposta = bot.comando_equipa(["Benfica"])
    assert "Ficha" in resposta
    assert "Benfica" in resposta
    # Deve mostrar baseline, casa/fora, fadiga, padroes
    assert "Baseline" in resposta
    assert "Casa vs Fora" in resposta


def test_equipa_devolve_erro_quando_nao_existe(bd_com_equipas):
    resposta = bot.comando_equipa(["Everton"])
    assert "Nenhuma equipa" in resposta or "nenhuma" in resposta.lower()


def test_equipa_aceita_nome_com_espacos(bd_com_equipas):
    """`/equipa SL Benfica` deve funcionar (nome composto)."""
    resposta = bot.comando_equipa(["SL", "Benfica"])
    assert "Benfica" in resposta
    assert "Ficha" in resposta


def test_ajuda_lista_comando_equipa():
    assert "/equipa" in mensagens.ajuda()
