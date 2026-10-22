"""Testes para apostas/modelos/historico_jogadores.py + confronto_tactico.py."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apostas.alertas import confronto_tactico
from apostas.modelos import historico_jogadores
from apostas.utils.schema import Base, EstatisticaJogadorJogo
from apostas.utils.tempo import agora_utc


@pytest.fixture
def engine_test(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class _Ctx:
        def __init__(self, eng):
            self._s = Session(eng)
        def __enter__(self):
            return self._s
        def __exit__(self, exc_type, *_):
            if exc_type is None:
                try: self._s.commit()
                except Exception: self._s.rollback()
            else: self._s.rollback()
            self._s.close()

    monkeypatch.setattr(historico_jogadores, "abrir_sessao", lambda: _Ctx(engine))
    return engine


def test_perfil_jogador_calcula_metricas_90(engine_test):
    """Jogador com 10 jogos × 90 min, 1.5 faltas por jogo → agressividade 1.5."""
    now = agora_utc()
    with Session(engine_test) as s:
        for i in range(10):
            s.add(EstatisticaJogadorJogo(
                sofascore_player_id=100,
                sofascore_event_id=i,
                sofascore_team_id=1,
                nome_jogador="Driblador",
                posicao="LW",
                liga="Serie A",
                data_jogo=now - timedelta(days=i * 3),
                era_casa=1,
                foi_titular=1,
                minutos=90,
                rating=7.5,
                golos=0 if i < 7 else 1,
                assistencias=1 if i < 5 else 0,
                remates=4,
                sot=2,
                dribles_tentados=10,
                dribles_sucesso=7,
                duels_won=10, duels_lost=5,
                fouls_committed=0,  # driblador NAO comete faltas
                cards_amarelos=0,
            ))
        s.commit()
    p = historico_jogadores.perfil_jogador(100, n=10)
    assert p is not None
    assert p["papel"] == "Criador"
    assert p["drible_pct"] == 0.70
    assert p["agressividade"] == 0.0


def test_perfil_defesa_agressivo(engine_test):
    now = agora_utc()
    with Session(engine_test) as s:
        for i in range(10):
            s.add(EstatisticaJogadorJogo(
                sofascore_player_id=200,
                sofascore_event_id=100 + i,
                sofascore_team_id=2,
                nome_jogador="DefesaMau",
                posicao="RB",
                liga="L",
                data_jogo=now - timedelta(days=i),
                era_casa=0,
                foi_titular=1,
                minutos=90,
                rating=6.5,
                fouls_committed=3,  # 3 faltas/jogo = agressivo
                cards_amarelos=1 if i < 5 else 0,  # 5 amarelos em 10 = 0.56/90
                tackles=4,
                duels_won=6, duels_lost=4,
            ))
        s.commit()
    p = historico_jogadores.perfil_jogador(200, n=10)
    assert p is not None
    assert p["papel"] == "Defesa"
    assert p["agressividade"] >= 2.9
    assert p["tendencia_amarelo"] >= 0.4


def test_perfil_poucos_jogos_devolve_none(engine_test):
    assert historico_jogadores.perfil_jogador(999, n=10) is None


def test_confronto_tactico_identifica_matchup(engine_test):
    """Driblador LW vs defesa agressivo RB → matchup severidade alta."""
    now = agora_utc()
    with Session(engine_test) as s:
        # driblador LW
        for i in range(10):
            s.add(EstatisticaJogadorJogo(
                sofascore_player_id=10,
                sofascore_event_id=i,
                sofascore_team_id=1,
                nome_jogador="Vinicius",
                posicao="LW",
                liga="L",
                data_jogo=now - timedelta(days=i),
                era_casa=1, foi_titular=1, minutos=90,
                rating=8.0,
                dribles_tentados=10, dribles_sucesso=7,  # 70% drible
                golos=1 if i < 5 else 0, assistencias=0,
                remates=4, sot=2, duels_won=5, duels_lost=3,
                fouls_committed=0, cards_amarelos=0,
            ))
        # defesa RB agressivo
        for i in range(10):
            s.add(EstatisticaJogadorJogo(
                sofascore_player_id=20,
                sofascore_event_id=100 + i,
                sofascore_team_id=2,
                nome_jogador="DefesaMau",
                posicao="RB",
                liga="L",
                data_jogo=now - timedelta(days=i),
                era_casa=0, foi_titular=1, minutos=90,
                rating=6.5,
                fouls_committed=3, cards_amarelos=1 if i < 4 else 0,
                tackles=4, duels_won=6, duels_lost=4,
            ))
        s.commit()

    res = confronto_tactico.analisar_tactico(
        titulares_casa_ids=[10],
        titulares_fora_ids=[20],
    )
    assert len(res.matchups) >= 1
    mu = res.matchups[0]
    assert "Vinicius" in mu.atacante_nome
    assert "DefesaMau" in mu.defesa_nome


def test_perfil_destaca_jogador_amarelado_e_em_forma(engine_test):
    now = agora_utc()
    with Session(engine_test) as s:
        # Jogador com forma e amarelado
        for i in range(10):
            s.add(EstatisticaJogadorJogo(
                sofascore_player_id=50,
                sofascore_event_id=200 + i,
                sofascore_team_id=1,
                nome_jogador="EstrelaAmarelada",
                posicao="CDM",
                liga="L",
                data_jogo=now - timedelta(days=i),
                era_casa=1, foi_titular=1, minutos=90,
                rating=7.8,
                fouls_committed=2, cards_amarelos=1 if i < 5 else 0,
                tackles=3, duels_won=5, duels_lost=3,
            ))
        s.commit()

    res = confronto_tactico.analisar_tactico(
        titulares_casa_ids=[50],
        titulares_fora_ids=[],
    )
    nomes_destaque = " ".join(res.perfis_destaque)
    assert "EstrelaAmarelada" in nomes_destaque


def test_formatar_telegram_vazio_quando_sem_matchups():
    res = confronto_tactico.ResultadoTactico()
    assert confronto_tactico.formatar_telegram(res) == ""
