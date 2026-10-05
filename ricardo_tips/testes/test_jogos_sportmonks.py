"""Testes da ingestão de fixtures + stats via Sportmonks (modo mock)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from apostas.ingestao import jogos_sportmonks, ligas_equipas
from apostas.ingestao.sportmonks import cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import EstatisticasJogo, Jogo


@pytest.fixture
def bd_com_ligas(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar(cli=cliente(modo="desenvolvimento"))
    yield bd
    db_mod.reset_engine()


def test_mock_fixtures_tem_participants_e_scores():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get("/fixtures/seasons/10001", params={"include": "participants;scores"})
    assert len(resp["data"]) > 0
    f = resp["data"][0]
    assert "participants" in f
    assert len(f["participants"]) == 2
    assert "scores" in f
    locs = {p["meta"]["location"] for p in f["participants"]}
    assert locs == {"home", "away"}


def test_mock_fixtures_com_statistics_tem_remates_e_cantos():
    cli = cliente(modo="desenvolvimento")
    resp = cli.get(
        "/fixtures/seasons/10001",
        params={"include": "participants;scores;statistics"},
    )
    stats = resp["data"][0].get("statistics", [])
    assert stats, "Esperávamos lista de estatísticas"
    tipos = {s["type_id"] for s in stats}
    # Tem de incluir pelo menos remates e cantos
    assert 42 in tipos  # shots total
    assert 34 in tipos  # corners


def test_sincronizar_popula_jogos(bd_com_ligas):
    res = jogos_sportmonks.sincronizar(epocas_atras=2)
    assert res.erros == []
    # 6 ligas × 2 épocas × 30 fixtures (6 equipas round-robin)
    assert res.jogos_criados == 6 * 2 * 30
    assert res.equipas_em_falta == 0

    with db_mod.abrir_sessao() as s:
        total = s.scalar(select(func.count(Jogo.id)))
        assert total == 360
        # Todos devem estar terminados (state_id = 5 no mock)
        terminados = s.scalar(
            select(func.count(Jogo.id)).where(Jogo.estado == "terminado")
        )
        assert terminados == 360
        # Golos preenchidos
        com_golos = s.scalar(
            select(func.count(Jogo.id)).where(
                Jogo.golos_casa.is_not(None),
                Jogo.golos_fora.is_not(None),
            )
        )
        assert com_golos == 360


def test_sincronizar_grava_estatisticas(bd_com_ligas):
    res = jogos_sportmonks.sincronizar(epocas_atras=1, com_stats=True)
    # 2 estatísticas por jogo (casa + fora)
    assert res.stats_criados == 6 * 30 * 2

    with db_mod.abrir_sessao() as s:
        total_stats = s.scalar(select(func.count(EstatisticasJogo.id)))
        assert total_stats == 360
        # Remates, cantos, cartões devem ter valores
        amostra = s.scalars(select(EstatisticasJogo).limit(5)).all()
        for st in amostra:
            assert st.remates is not None
            assert st.cantos is not None
            assert st.cartoes_amarelos is not None


def test_sincronizar_e_idempotente(bd_com_ligas):
    primeiro = jogos_sportmonks.sincronizar(epocas_atras=1)
    segundo = jogos_sportmonks.sincronizar(epocas_atras=1)
    assert segundo.jogos_criados == 0
    assert segundo.jogos_existentes == primeiro.jogos_criados


def test_sem_stats_quando_desligado(bd_com_ligas):
    res = jogos_sportmonks.sincronizar(epocas_atras=1, com_stats=False)
    assert res.stats_criados == 0
    with db_mod.abrir_sessao() as s:
        total_stats = s.scalar(select(func.count(EstatisticasJogo.id)))
        assert total_stats == 0
