"""Testes do bootstrap de ligas a partir do config."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from apostas.ingestao import ligas_equipas
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import Liga


@pytest.fixture
def bd_temporaria(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    db_mod.reset_engine()
    db_mod.criar_schema()
    yield bd
    db_mod.reset_engine()


def test_bootstrap_cria_6_ligas(bd_temporaria):
    resultado = ligas_equipas.sincronizar()
    assert resultado.ligas_criadas == 6
    assert resultado.ligas_existentes == 0

    with db_mod.abrir_sessao() as s:
        ligas = s.scalars(select(Liga)).all()
        assert len(ligas) == 6
        nomes = {l.nome for l in ligas}
        assert nomes == {
            "Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1",
            "Liga Portugal",
        }


def test_bootstrap_e_idempotente(bd_temporaria):
    ligas_equipas.sincronizar()
    segunda = ligas_equipas.sincronizar()
    assert segunda.ligas_criadas == 0
    assert segunda.ligas_existentes == 6
