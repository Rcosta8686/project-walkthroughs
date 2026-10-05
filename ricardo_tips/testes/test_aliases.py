"""Testes da reconciliação de equipas via aliases entre Sportmonks e football-data."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from apostas.ingestao import (
    aliases as _aliases,
    football_data_uk,
    jogos_sportmonks,
    ligas_equipas,
)
from apostas.ingestao.sportmonks import cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import Equipa, EquipaAlias, Jogo


# ─── Módulo aliases ─────────────────────────────────────────────────

def test_nome_canonico_de_resolve_football_data():
    assert _aliases.nome_canonico_de("Man United", "football_data") == "Manchester United"
    assert _aliases.nome_canonico_de("Paris SG", "football_data") == "Paris Saint Germain"
    assert _aliases.nome_canonico_de("Milan", "football_data") == "AC Milan"


def test_nome_canonico_de_devolve_none_para_desconhecido():
    assert _aliases.nome_canonico_de("Arsenal", "football_data") is None
    assert _aliases.nome_canonico_de("qualquer coisa", "sportmonks") is None


def test_aliases_de_devolve_lista():
    assert "Man United" in _aliases.aliases_de("Manchester United", "football_data")
    assert _aliases.aliases_de("Arsenal", "football_data") == []


# ─── Integração ─────────────────────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))
    yield bd
    db_mod.reset_engine()


def test_ingestao_sportmonks_regista_aliases(bd_populada):
    with db_mod.abrir_sessao() as s:
        # Equipa "Manchester United" deve ter alias "Man United" para football_data
        man_utd = s.scalar(select(Equipa).where(Equipa.nome == "Manchester United"))
        assert man_utd is not None
        aliases_db = s.scalars(
            select(EquipaAlias).where(EquipaAlias.equipa_id == man_utd.id)
        ).all()
        alias_strings = {(a.alias, a.fonte) for a in aliases_db}
        assert ("Manchester United", "sportmonks") in alias_strings
        assert ("Man United", "football_data") in alias_strings


def test_football_data_nao_duplica_equipas_via_aliases(bd_populada):
    """Após sincronizar football-data, as equipas com alias conhecido
    devem apontar para a mesma Equipa da Sportmonks."""
    res = football_data_uk.sincronizar(epocas=[2023])
    assert res.equipas_criadas == 0

    with db_mod.abrir_sessao() as s:
        total = s.scalar(select(func.count(Equipa.id)))
        assert total == 36  # só as da Sportmonks, nenhuma duplicada


def test_football_data_reutiliza_jogos_sportmonks(bd_populada):
    """Após sincronizar Sportmonks (fixtures) e depois football-data, as odds
    do football-data devem ser anexadas aos jogos já criados pela Sportmonks
    em vez de criarem jogos duplicados."""
    jogos_sportmonks.sincronizar(epocas_atras=1)
    com_jogos_sm = db_mod.abrir_sessao().__enter__()
    try:
        n_apos_sm = com_jogos_sm.scalar(select(func.count(Jogo.id)))
    finally:
        com_jogos_sm.close()

    football_data_uk.sincronizar(epocas=[2024])
    with db_mod.abrir_sessao() as s:
        total_jogos = s.scalar(select(func.count(Jogo.id)))

    # Mesma época + mesmos aliases + mesmas datas → zero novos jogos
    assert total_jogos == n_apos_sm
