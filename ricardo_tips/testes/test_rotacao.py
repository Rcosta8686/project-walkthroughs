"""Testes do modulo rotacao (deteccao de taca + lineup rotado)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apostas.alertas.rotacao import (
    RelatorioRotacao,
    analisar_lineup_actual,
    factor_taca,
    is_competicao_rotacao_alta,
    titulares_habituais,
)


# ─── Camada 1: deteccao de taca ───────────────────────────────────

def test_taca_portugal_detectada():
    assert is_competicao_rotacao_alta("Taca de Portugal") is True
    assert is_competicao_rotacao_alta("TACA DE PORTUGAL — Allianz Cup") is True


def test_fa_cup_detectada():
    assert is_competicao_rotacao_alta("FA Cup") is True
    assert is_competicao_rotacao_alta("Carabao Cup") is True
    assert is_competicao_rotacao_alta("EFL Cup") is True


def test_copa_italia_e_espanha():
    assert is_competicao_rotacao_alta("Coppa Italia") is True
    assert is_competicao_rotacao_alta("Copa del Rey") is True


def test_liga_regular_nao_e_taca():
    assert is_competicao_rotacao_alta("Primeira Liga") is False
    assert is_competicao_rotacao_alta("La Liga") is False
    assert is_competicao_rotacao_alta("Premier League") is False
    assert is_competicao_rotacao_alta(None) is False
    assert is_competicao_rotacao_alta("") is False


def test_factor_taca_default_0_7():
    assert factor_taca() == 0.7


# ─── Camada 2: lineup vs titulares habituais ────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from apostas.utils import config as cfg_mod
    from apostas.utils import db as db_mod
    monkeypatch.setattr(cfg_mod, "db_path", lambda: tmp_path / "test.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    yield tmp_path / "test.db"
    db_mod._engine = None
    db_mod._SessionLocal = None


def _add_jogador(sess, event_id, team_id, player_id, nome, minutos, data=None):
    from apostas.utils.schema import EstatisticaJogadorJogo
    sess.add(EstatisticaJogadorJogo(
        sofascore_event_id=event_id, sofascore_team_id=team_id,
        sofascore_player_id=player_id, nome_jogador=nome,
        data_jogo=data or datetime(2026, 10, 1),
        era_casa=True, foi_titular=True, minutos=minutos,
    ))


def test_titulares_habituais_devolve_top_por_minutos(tmp_db):
    from apostas.utils.db import abrir_sessao

    with abrir_sessao() as sess:
        # 3 jogos, cada um com 2 jogadores
        for ev_id, data_off in [(1, 0), (2, -3), (3, -7)]:
            data = datetime(2026, 10, 20) - timedelta(days=data_off)
            _add_jogador(sess, ev_id, 100, 1, "Habitual A", 90, data)
            _add_jogador(sess, ev_id, 100, 2, "Habitual B", 85, data)
        # 1 jogador apenas 1 jogo
        _add_jogador(sess, 1, 100, 3, "Suplente", 10, datetime(2026, 10, 20))

    habituais = titulares_habituais(100, n_jogos=3)
    assert len(habituais) == 3
    # Ordenado por minutos: A (270) > B (255) > Suplente (10)
    assert habituais[0][1] == "Habitual A"
    assert habituais[1][1] == "Habitual B"
    assert habituais[0][2] >= habituais[1][2]


def test_titulares_habituais_vazio_quando_sem_historico(tmp_db):
    assert titulares_habituais(999) == []


def test_analisar_lineup_actual_sem_rotacao(tmp_db):
    from apostas.utils.db import abrir_sessao

    with abrir_sessao() as sess:
        for i in range(1, 12):  # 11 jogadores
            for ev in range(1, 6):
                _add_jogador(sess, ev, 100, i, f"J{i}", 90)

    # Lineup actual tem todos os 11 habituais
    ids_actuais = list(range(1, 12))
    rel = analisar_lineup_actual(100, "Porto", ids_actuais)
    assert rel is not None
    assert rel.habituais_em_campo == 11
    assert rel.tem_rotacao(limiar=3) is False
    assert rel.factor_rotacao == 1.0


def test_analisar_lineup_actual_rotacao_total(tmp_db):
    from apostas.utils.db import abrir_sessao

    with abrir_sessao() as sess:
        for i in range(1, 12):  # 11 habituais
            for ev in range(1, 6):
                _add_jogador(sess, ev, 100, i, f"Habitual{i}", 90)

    # Lineup actual: todos jogadores B novos (ids 100-110)
    ids_actuais = list(range(100, 111))
    rel = analisar_lineup_actual(100, "Porto", ids_actuais)
    assert rel is not None
    assert rel.habituais_em_campo == 0
    assert rel.tem_rotacao(limiar=3) is True
    assert rel.factor_rotacao == 0.6  # 0.6 + 0 * 0.4


def test_analisar_lineup_actual_rotacao_parcial(tmp_db):
    """6 dos 11 habituais em campo → rotacao detectada (limiar 3)."""
    from apostas.utils.db import abrir_sessao

    with abrir_sessao() as sess:
        for i in range(1, 12):  # 11 habituais
            for ev in range(1, 6):
                _add_jogador(sess, ev, 100, i, f"Habitual{i}", 90)

    # 6 habituais + 5 novos
    ids_actuais = list(range(1, 7)) + list(range(100, 105))
    rel = analisar_lineup_actual(100, "Porto", ids_actuais)
    assert rel is not None
    assert rel.habituais_em_campo == 6
    assert rel.tem_rotacao(limiar=3) is True
    assert rel.tem_rotacao(limiar=6) is False


def test_mensagem_curta_formato():
    rel = RelatorioRotacao(
        team_id=100, nome_equipa="Porto",
        habituais_em_campo=8, habituais_total=11,
        jogadores_novos=["B1", "B2", "B3"],
        titulares_fora=["A1", "A2", "A3"],
        factor_rotacao=0.71,
    )
    msg = rel.mensagem_curta()
    assert "Porto sem 3 habituais titulares" in msg
    assert "⚠" in msg


def test_tem_rotacao_requer_dados_minimos():
    """Com <6 habituais no total (ficha fraca), nao arrisca."""
    rel = RelatorioRotacao(
        team_id=100, nome_equipa="Equipa X",
        habituais_em_campo=0, habituais_total=3,  # so 3 habituais conhecidos
        jogadores_novos=[], titulares_fora=[],
        factor_rotacao=0.6,
    )
    assert rel.tem_rotacao(limiar=3) is False
