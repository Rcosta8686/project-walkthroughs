"""Testes da ingestão de ligas e equipas (modo mock)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from apostas.ingestao import ligas_equipas
from apostas.ingestao.api_football import cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import Equipa, Liga


@pytest.fixture
def bd_temporaria(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    yield bd
    db_mod.reset_engine()


def test_mock_leagues_retorna_premier():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get("/leagues", params={"id": 39})
    assert resp["results"] == 1
    assert resp["response"][0]["league"]["name"] == "Premier League"


def test_mock_teams_retorna_equipas_premier():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get("/teams", params={"league": 39, "season": 2024})
    nomes = {item["team"]["name"] for item in resp["response"]}
    assert "Arsenal" in nomes
    assert "Manchester United" in nomes


def test_sincronizar_popula_ligas_e_equipas(bd_temporaria):
    resultado = ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))

    assert resultado.ligas_criadas == 5
    assert resultado.ligas_existentes == 0
    assert resultado.equipas_criadas == 30  # 5 ligas × 6 equipas no mock
    assert resultado.equipas_existentes == 0

    with db_mod.abrir_sessao() as s:
        ligas = s.scalars(select(Liga)).all()
        assert len(ligas) == 5
        nomes = {l.nome for l in ligas}
        assert nomes == {
            "Premier League",
            "La Liga",
            "Serie A",
            "Bundesliga",
            "Ligue 1",
        }

        equipas = s.scalars(select(Equipa)).all()
        assert len(equipas) == 30


def test_sincronizar_e_idempotente(bd_temporaria):
    cli = cliente(modo="desenvolvimento")
    ligas_equipas.sincronizar(cli=cli)
    segunda = ligas_equipas.sincronizar(cli=cli)

    assert segunda.ligas_criadas == 0
    assert segunda.ligas_existentes == 5
    assert segunda.equipas_criadas == 0
    assert segunda.equipas_existentes == 30


def test_modo_invalido_levanta():
    with pytest.raises(ValueError, match="MODO inválido"):
        cliente(modo="qualquer")
