"""Testes da ingestão The Odds API."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from apostas.ingestao import odds_api, ligas_equipas
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes


def test_mock_devolve_jogos_para_epl():
    jogos = odds_api._mocks_odds_api_jogos_sport("soccer_epl") if False else None  # placeholder
    from apostas.ingestao import _mocks_odds_api
    jogos = _mocks_odds_api.jogos_sport("soccer_epl")
    assert len(jogos) > 0
    j = jogos[0]
    assert "home_team" in j
    assert "away_team" in j
    assert j["bookmakers"][0]["key"] == "pinnacle"


def test_canoniza_resolve_aliases():
    assert odds_api._canoniza("Manchester United") == "Man United"
    assert odds_api._canoniza("Paris Saint-Germain") == "Paris SG"
    assert odds_api._canoniza("Arsenal") == "Arsenal"  # já canónico


def test_lado_1x2():
    assert odds_api._lado_1x2("Draw", "Arsenal", "Chelsea") == "empate"
    assert odds_api._lado_1x2("Arsenal", "Arsenal", "Chelsea") == "casa"
    assert odds_api._lado_1x2("Chelsea", "Arsenal", "Chelsea") == "fora"


@pytest.fixture
def bd_com_jogo(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar()

    # Criar manualmente um jogo Arsenal vs Chelsea daqui a 1 dia
    with db_mod.abrir_sessao() as s:
        liga = s.scalar(select(Liga).where(Liga.nome == "Premier League"))
        arsenal = Equipa(nome="Arsenal", liga_id=liga.id)
        chelsea = Equipa(nome="Chelsea", liga_id=liga.id)
        s.add_all([arsenal, chelsea])
        s.flush()
        s.add(Jogo(
            id_externo=99999, liga_id=liga.id, epoca=2026,
            data_utc=datetime.utcnow() + timedelta(days=1, hours=15),
            casa_id=arsenal.id, fora_id=chelsea.id,
            estado="agendado",
        ))
    yield bd
    db_mod.reset_engine()


def test_sincronizar_cria_odds_correntes(bd_com_jogo):
    res = odds_api.sincronizar()
    assert res.odds_criadas > 0

    with db_mod.abrir_sessao() as s:
        total = s.scalar(select(func.count(OddsCorrentes.id)))
        assert total > 0

        # Verifica que há odd Pinnacle de Over 2.5
        o = s.scalar(
            select(OddsCorrentes).where(
                OddsCorrentes.mercado == "golos",
                OddsCorrentes.linha == 2.5,
                OddsCorrentes.lado == "over",
                OddsCorrentes.casa_de_apostas == "pinnacle",
            )
        )
        assert o is not None
        assert o.odd > 1


def test_sincronizar_idempotente_sem_actualizacao(bd_com_jogo):
    primeiro = odds_api.sincronizar()
    segundo = odds_api.sincronizar()
    # Segunda chamada: odds já existem, mesma odd → nada a actualizar
    assert segundo.odds_criadas == 0
    assert primeiro.odds_criadas > 0
