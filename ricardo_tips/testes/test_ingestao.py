"""Testes da ingestão de ligas e equipas via Sportmonks (modo mock)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from apostas.ingestao import ligas_equipas
from apostas.ingestao.sportmonks import cliente
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


def test_mock_league_retorna_premier():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get("/leagues/8")
    assert resp["data"]["name"] == "Premier League"
    assert resp["data"]["id"] == 8
    assert "currentseason" in resp["data"]


def test_mock_teams_season_retorna_equipas():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get("/teams/seasons/10001")  # Premier League mock season
    nomes = {item["name"] for item in resp["data"]}
    assert "Arsenal" in nomes
    assert "Manchester United" in nomes


def test_sincronizar_popula_ligas_e_equipas(bd_temporaria):
    resultado = ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))

    assert resultado.ligas_criadas == 6
    assert resultado.ligas_existentes == 0
    assert resultado.equipas_criadas == 36  # 6 ligas × 6 equipas no mock
    assert resultado.equipas_existentes == 0

    with db_mod.abrir_sessao() as s:
        ligas = s.scalars(select(Liga)).all()
        assert len(ligas) == 6
        nomes = {l.nome for l in ligas}
        assert nomes == {
            "Premier League",
            "La Liga",
            "Serie A",
            "Bundesliga",
            "Ligue 1",
            "Liga Portugal",
        }

        equipas = s.scalars(select(Equipa)).all()
        assert len(equipas) == 36
        # Todas devem ter sportmonks_id preenchido
        assert all(e.sportmonks_id is not None for e in equipas)


def test_sincronizar_e_idempotente(bd_temporaria):
    cli = cliente(modo="desenvolvimento")
    ligas_equipas.sincronizar(cli=cli)
    segunda = ligas_equipas.sincronizar(cli=cli)

    assert segunda.ligas_criadas == 0
    assert segunda.ligas_existentes == 6
    assert segunda.equipas_criadas == 0
    assert segunda.equipas_existentes == 36


def test_modo_invalido_levanta():
    with pytest.raises(ValueError, match="MODO inválido"):
        cliente(modo="qualquer")
