"""Testes para apostas/relatorios/ficha_equipa.py."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apostas.modelos import historico_equipas
from apostas.relatorios import ficha_equipa
from apostas.relatorios.ficha_equipa import (
    _pct_diff, ficha_markdown, gerar_ficha,
)
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
                try:
                    self._s.commit()
                except Exception:
                    self._s.rollback()
            else:
                self._s.rollback()
            self._s.close()

    monkeypatch.setattr(historico_equipas, "abrir_sessao", lambda: _Ctx(engine))
    return engine


def _seed(engine, team_id=42, nome="AS Roma", n_W=5, n_L=3, n_D=2):
    """Insere jogos com padrao: a perder -> +remates, 2T > 1T."""
    now = agora_utc()
    base = n_W + n_L + n_D
    rows = []
    for i in range(n_W):
        rows.append(dict(
            sofascore_team_id=team_id, sofascore_event_id=base * 10 + i,
            nome_equipa=nome, liga="Serie A",
            data_jogo=now - timedelta(days=i * 2),
            era_casa=1, golos_marcados=2, golos_sofridos=0,
            resultado="W", resultado_1t="W",
            remates=12, sot=5, cantos=5, cards_amarelos=2, fouls=10,
            remates_1t=5, remates_2t=7, cantos_1t=2, cantos_2t=3,
            sot_1t=2, sot_2t=3, posse_pct=55,
        ))
    for i in range(n_D):
        rows.append(dict(
            sofascore_team_id=team_id,
            sofascore_event_id=base * 10 + 100 + i,
            nome_equipa=nome, liga="Serie A",
            data_jogo=now - timedelta(days=10 + i * 2),
            era_casa=0, golos_marcados=1, golos_sofridos=1,
            resultado="D", resultado_1t="D",
            remates=11, sot=4, cantos=5, cards_amarelos=2, fouls=11,
            remates_1t=4, remates_2t=7, posse_pct=50,
        ))
    for i in range(n_L):
        rows.append(dict(
            sofascore_team_id=team_id,
            sofascore_event_id=base * 10 + 200 + i,
            nome_equipa=nome, liga="Serie A",
            data_jogo=now - timedelta(days=15 + i * 2),
            era_casa=0, golos_marcados=0, golos_sofridos=2,
            resultado="L", resultado_1t="L",
            remates=18, sot=6, cantos=7, cards_amarelos=3, fouls=12,
            remates_1t=6, remates_2t=12, cantos_1t=3, cantos_2t=4,
            sot_1t=2, sot_2t=4, posse_pct=48,
        ))
    with Session(engine) as s:
        for r in rows:
            s.add(EstatisticaEquipaJogo(**r))
        s.commit()


def test_pct_diff():
    assert _pct_diff(10, 15) == "+50% 2T"
    assert _pct_diff(10, 8) == "-20% 2T"
    assert _pct_diff(10, 11) is None  # <15% delta
    assert _pct_diff(None, 10) is None
    assert _pct_diff(0, 5) is None  # evita div/0


def test_gerar_ficha_completa(engine_test):
    _seed(engine_test, team_id=42, nome="AS Roma")

    f = gerar_ficha(42, n_jogos=20)
    assert f is not None
    assert f.nome == "AS Roma"
    assert f.jogos_lidos == 10
    assert f.liga == "Serie A"

    # Baseline
    assert f.baseline.remates == pytest.approx((12*5 + 11*2 + 18*3) / 10, abs=0.5)
    assert f.baseline.posse_pct is not None

    # Por parte
    assert f.por_parte.diferenca_remates is not None
    assert "+" in f.por_parte.diferenca_remates  # 2T > 1T

    # Por resultado
    assert f.por_resultado.n_W == 5
    assert f.por_resultado.n_L == 3
    assert f.por_resultado.n_D == 2
    # Perdeu: 18 remates > ganhou: 12 -> deve estar nas notas
    assert any("A perder" in n or "dispara" in n for n in f.notas)


def test_gerar_ficha_poucos_jogos_devolve_none(engine_test):
    _seed(engine_test, team_id=99, nome="Falha", n_W=1, n_L=0, n_D=0)
    assert gerar_ficha(99, n_jogos=20) is None


def test_padrao_amarelos_explodem_2T(engine_test):
    """Caso Roma 2026/27: 0.4 am 1T, 1.4 am 2T → +250% → deve dar nota."""
    from datetime import datetime, timedelta
    now = agora_utc()
    with Session(engine_test) as s:
        for i in range(8):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=777, sofascore_event_id=50000 + i,
                nome_equipa="RomaMock", liga="Serie A",
                data_jogo=now - timedelta(days=i * 2),
                era_casa=1, golos_marcados=2, golos_sofridos=1,
                resultado="W", resultado_1t="W",
                remates=15, sot=5, cantos=5,
                cards_amarelos=2, cards_amarelos_1t=0.4, cards_amarelos_2t=1.4,
                remates_1t=7, remates_2t=8,
            ))
        s.commit()

    f = gerar_ficha(777, n_jogos=20)
    assert f is not None
    assert any("Amarelos explodem" in n for n in f.notas), (
        f"Esperava nota 'Amarelos explodem 2a parte', obtive {f.notas}"
    )


def test_padrao_perder_remata_menos_dominante(engine_test):
    """Equipa dominante (perde a jogar menos) → nota 'A perder, remata MENOS'."""
    from datetime import datetime, timedelta
    now = agora_utc()
    with Session(engine_test) as s:
        # 6 W com 18 remates (muito), 4 L com 10 remates (menos que baseline 15)
        for i in range(6):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=888, sofascore_event_id=60000 + i,
                nome_equipa="Dominante", liga="L",
                data_jogo=now - timedelta(days=i),
                era_casa=1, golos_marcados=3, golos_sofridos=0,
                resultado="W", resultado_1t="W",
                remates=18,
            ))
        for i in range(4):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=888, sofascore_event_id=60100 + i,
                nome_equipa="Dominante", liga="L",
                data_jogo=now - timedelta(days=10 + i),
                era_casa=0, golos_marcados=0, golos_sofridos=2,
                resultado="L", resultado_1t="L",
                remates=10,  # baseline media ~15.2, so 10 = -34%
            ))
        s.commit()

    f = gerar_ficha(888, n_jogos=20)
    assert f is not None
    assert any("remata MENOS" in n or "equipa dominante" in n.lower()
               for n in f.notas)


def test_markdown_tem_seccoes(engine_test):
    _seed(engine_test, team_id=55, nome="Benfica")
    f = gerar_ficha(55, n_jogos=20)
    assert f is not None
    md = ficha_markdown(f)
    assert "# Benfica" in md
    assert "## Baseline" in md
    assert "## Por parte" in md
    assert "## Por resultado" in md
    assert "## Fadiga" in md
    assert "Padroes-chave" in md  # notas presentes
