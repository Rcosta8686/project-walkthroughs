"""Testes para apostas/alertas/confronto.py."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apostas.alertas import confronto
from apostas.alertas.confronto import (
    analisar_confronto, formatar_confronto_markdown,
    formatar_confronto_telegram,
)
from apostas.modelos import historico_equipas
from apostas.utils.schema import Base, EstatisticaEquipaJogo
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
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                try: self._s.commit()
                except Exception: self._s.rollback()
            else: self._s.rollback()
            self._s.close()

    monkeypatch.setattr(historico_equipas, "abrir_sessao", lambda: _Ctx(engine))
    return engine


def _seed_equipa(engine, team_id, nome, remates_base, cards_base,
                  diferenca_2t=True, carrega_a_perder=True):
    now = agora_utc()
    rows = []
    for i in range(8):
        rem_1t = remates_base * 0.4
        rem_2t = remates_base * 0.6 if diferenca_2t else remates_base * 0.5
        rows.append(dict(
            sofascore_team_id=team_id,
            sofascore_event_id=team_id * 1000 + i,
            nome_equipa=nome, liga="L",
            data_jogo=now - timedelta(days=i * 2),
            era_casa=i % 2,
            golos_marcados=2 if i < 5 else 0,
            golos_sofridos=0 if i < 5 else 1,
            resultado="W" if i < 5 else "L",
            resultado_1t="W" if i < 5 else "L",
            remates=remates_base if i < 5 else int(remates_base * 1.4) if carrega_a_perder else remates_base,
            sot=5, cantos=5, cards_amarelos=cards_base,
            remates_1t=rem_1t, remates_2t=rem_2t,
            cantos_1t=2, cantos_2t=3,
            posse_pct=55,
        ))
    with Session(engine) as s:
        for r in rows:
            s.add(EstatisticaEquipaJogo(**r))
        s.commit()


def test_confronto_basico(engine_test):
    _seed_equipa(engine_test, 10, "A", remates_base=12, cards_base=2)
    _seed_equipa(engine_test, 20, "B", remates_base=10, cards_base=2)

    res = analisar_confronto(10, 20, n_jogos_ficha=20)
    assert res is not None
    assert res.nome_casa == "A"
    assert res.nome_fora == "B"
    assert res.remates_esperados > 15  # soma de A + B


def test_confronto_devolve_none_se_sem_dados(engine_test):
    _seed_equipa(engine_test, 10, "A", remates_base=12, cards_base=2)
    # team 999 nao existe
    assert analisar_confronto(10, 999) is None
    assert analisar_confronto(999, 10) is None


def test_padroes_ambas_carregam_2t(engine_test):
    _seed_equipa(engine_test, 10, "A", remates_base=12, cards_base=2,
                 diferenca_2t=True)
    _seed_equipa(engine_test, 20, "B", remates_base=12, cards_base=2,
                 diferenca_2t=True)
    res = analisar_confronto(10, 20)
    assert res is not None
    assert any("carregam na 2" in p for p in res.padroes_cruzados)


def test_padroes_discrepancia_disciplinar(engine_test):
    _seed_equipa(engine_test, 10, "Agressiva", remates_base=12, cards_base=4)
    _seed_equipa(engine_test, 20, "Disciplinada", remates_base=12, cards_base=1)
    res = analisar_confronto(10, 20)
    assert res is not None
    assert any("Discrepancia disciplinar" in p for p in res.padroes_cruzados)


def test_padrao_live_casa_a_perder(engine_test):
    _seed_equipa(engine_test, 10, "Casa", remates_base=12, cards_base=2,
                 carrega_a_perder=True)
    _seed_equipa(engine_test, 20, "Fora", remates_base=12, cards_base=2)
    res = analisar_confronto(
        10, 20, minuto=60, score_casa=0, score_fora=1,
    )
    assert res is not None
    assert any("Casa a perder" in p for p in res.padroes_cruzados)


def test_markdown_tem_seccoes_principais(engine_test):
    _seed_equipa(engine_test, 10, "A", remates_base=12, cards_base=2)
    _seed_equipa(engine_test, 20, "B", remates_base=10, cards_base=2)
    res = analisar_confronto(10, 20)
    md = formatar_confronto_markdown(res)
    assert "# A vs B" in md
    assert "Projeccoes esperadas" in md
    assert "Perfil Casa" in md
    assert "Perfil Fora" in md


def test_telegram_compacto(engine_test):
    _seed_equipa(engine_test, 10, "A", remates_base=12, cards_base=2)
    _seed_equipa(engine_test, 20, "B", remates_base=10, cards_base=2)
    res = analisar_confronto(10, 20, minuto=55, score_casa=1, score_fora=0)
    msg = formatar_confronto_telegram(res)
    assert "CONFRONTO" in msg
    assert "A vs B" in msg
    assert "1-0" in msg
    assert "remates" in msg
