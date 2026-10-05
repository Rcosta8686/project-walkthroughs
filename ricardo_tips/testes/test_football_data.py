"""Testes da ingestão football-data.co.uk (modo mock)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from apostas.ingestao import _mocks_fd, football_data_uk, ligas_equipas
from apostas.ingestao.sportmonks import cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import Equipa, Jogo, OddsFecho


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


def test_mock_csv_tem_colunas_esperadas():
    csv = _mocks_fd.gerar_csv("E0", 2023)
    linhas = csv.strip().split("\n")
    cols = linhas[0].split(",")
    for obrigatoria in ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "AvgC>2.5", "AvgC<2.5"):
        assert obrigatoria in cols
    # 6 equipas → 30 jogos de ida e volta
    assert len(linhas) - 1 == 30


def test_mock_csv_reprodutivel():
    a = _mocks_fd.gerar_csv("E0", 2023)
    b = _mocks_fd.gerar_csv("E0", 2023)
    assert a == b


def test_sincronizar_cria_jogos_e_odds(bd_com_ligas):
    resultado = football_data_uk.sincronizar(epocas=[2023])
    assert resultado.erros == []
    assert resultado.jogos_criados == 6 * 30  # 6 ligas × 30 jogos
    assert resultado.odds_criadas > 0

    with db_mod.abrir_sessao() as s:
        total_jogos = s.scalar(select(func.count(Jogo.id)))
        total_odds = s.scalar(select(func.count(OddsFecho.id)))
        assert total_jogos == 180
        assert total_odds > 600  # várias odds por jogo (1x2 + OU 2.5 × 2 casas)

        # Odd Over 2.5 Avg Closing deve estar presente para a maioria dos jogos
        n_ou = s.scalar(
            select(func.count(OddsFecho.id)).where(
                OddsFecho.mercado == "golos",
                OddsFecho.linha == 2.5,
                OddsFecho.lado == "over",
                OddsFecho.casa_de_apostas == "Avg_Closing",
            )
        )
        assert n_ou == 180


def test_sincronizar_reutiliza_equipas_via_aliases(bd_com_ligas):
    """Nomes football-data (ex.: 'Man United') são resolvidos para a equipa
    Sportmonks ('Manchester United') via tabela equipa_aliases — não deve
    criar equipas duplicadas."""
    resultado = football_data_uk.sincronizar(epocas=[2023])
    assert resultado.equipas_criadas == 0

    with db_mod.abrir_sessao() as s:
        # Todas as 36 equipas Sportmonks continuam a ter sportmonks_id preenchido
        sem_sm_id = s.scalars(
            select(Equipa).where(Equipa.sportmonks_id.is_(None))
        ).all()
        assert len(sem_sm_id) == 0


def test_sincronizar_e_idempotente(bd_com_ligas):
    primeiro = football_data_uk.sincronizar(epocas=[2023])
    segundo = football_data_uk.sincronizar(epocas=[2023])
    assert segundo.jogos_criados == 0
    assert segundo.jogos_existentes == primeiro.jogos_criados
    assert segundo.odds_criadas == 0
    assert segundo.stats_criados == 0


def test_sincronizar_grava_estatisticas_do_csv(bd_com_ligas):
    """O CSV sintético (como o real) tem HS/HC/HY etc.; devem ser lidos."""
    from apostas.utils.schema import EstatisticasJogo
    from sqlalchemy import func

    res = football_data_uk.sincronizar(epocas=[2023])
    assert res.stats_criados > 0
    with db_mod.abrir_sessao() as s:
        total = s.scalar(select(func.count(EstatisticasJogo.id)))
        assert total > 0
        # Verificar que remates e cantos ficaram lá
        amostra = s.scalars(select(EstatisticasJogo).limit(5)).all()
        for st in amostra:
            assert st.remates is not None
            assert st.cantos is not None
