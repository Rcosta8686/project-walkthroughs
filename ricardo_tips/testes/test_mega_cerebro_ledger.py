"""Testes do ledger (Fase D) do mega-cerebro: grava + resolve."""
from __future__ import annotations

import pytest

from apostas.alertas.mega_cerebro import (
    SinteseMega,
    classificar_heuristica,
    gravar_sinal,
    resolver_sinais_do_evento,
)


# ─── classificar_heuristica ──────────────────────────────────────

def test_heuristica_amarelos():
    assert classificar_heuristica("amarelos acima do normal") == "H1_amarelos"


def test_heuristica_remates_ambas():
    assert classificar_heuristica("ambas acima do ritmo normal — jogo aberto"
                                   ) == "H2_ambas_remates"


def test_heuristica_pressao_lateral():
    assert classificar_heuristica("equipa a perder com 1.5x — pressao pelas pontas"
                                   ) == "H3_pressao_lateral"


def test_heuristica_dominante_controla():
    assert classificar_heuristica("dominante a controlar, baixou ritmo"
                                   ) == "H4_dominante_controla"


def test_heuristica_peso_muito_alto():
    assert classificar_heuristica("final + ambas acima do normal — jogo nao vai poupar"
                                   ) == "H5_peso_muito_alto"


def test_heuristica_chasing_contexto():
    assert classificar_heuristica("equipa casa precisa pts + adversario ja qualificado — chasing"
                                   ) == "H6_chasing_contexto"


def test_heuristica_ambas_pts():
    assert classificar_heuristica("ambas as equipas a precisar pts — jogo nao fechado"
                                   ) == "H7_ambas_pts"


def test_heuristica_desconhecida():
    assert classificar_heuristica("algo que nao reconheco") == "H?_outra"


# ─── gravar_sinal + resolver_sinais ──────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Setup SQLite temp DB com o schema completo."""
    from apostas.utils import config as cfg_mod
    from apostas.utils import db as db_mod
    monkeypatch.setattr(cfg_mod, "db_path", lambda: tmp_path / "test.db")
    # Reset engine cache
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    yield tmp_path / "test.db"
    db_mod._engine = None
    db_mod._SessionLocal = None


def test_gravar_sinal_persiste(tmp_db):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import SinalMegaCerebro
    from sqlalchemy import select

    sint = SinteseMega(
        mercado="cantos", direccao="over",
        razao="pressao pelas pontas", confianca=2,
    )
    with abrir_sessao() as sess:
        rid = gravar_sinal(
            sess, event_id=1234, liga="L", home="A", away="B",
            minuto=65, score_casa=1, score_fora=0,
            sot_c=5, sot_f=3, cantos_c=4, cantos_f=6,
            cards_c=2, cards_f=1,
            sintese=sint,
        )
        assert rid is not None

    with abrir_sessao() as sess:
        row = sess.execute(
            select(SinalMegaCerebro).where(SinalMegaCerebro.event_id == 1234)
        ).scalar_one()
        assert row.mercado == "cantos"
        assert row.direccao == "over"
        assert row.heuristica == "H3_pressao_lateral"
        assert row.resolvido == "pendente"


def test_resolver_marca_acerto_e_falha(tmp_db):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import SinalMegaCerebro
    from sqlalchemy import select

    # 3 sinais no mesmo jogo: over cantos (vai acertar), under cantos (vai falhar),
    # over remates (vai acertar)
    with abrir_sessao() as sess:
        gravar_sinal(sess, event_id=42, liga="L", home="A", away="B",
                     minuto=50, score_casa=0, score_fora=0,
                     sot_c=None, sot_f=None, cantos_c=None, cantos_f=None,
                     cards_c=None, cards_f=None,
                     sintese=SinteseMega("cantos", "over", "razao", 2))
        gravar_sinal(sess, event_id=42, liga="L", home="A", away="B",
                     minuto=55, score_casa=0, score_fora=0,
                     sot_c=None, sot_f=None, cantos_c=None, cantos_f=None,
                     cards_c=None, cards_f=None,
                     sintese=SinteseMega("under_cantos", "under", "razao", 2))
        gravar_sinal(sess, event_id=42, liga="L", home="A", away="B",
                     minuto=60, score_casa=0, score_fora=0,
                     sot_c=None, sot_f=None, cantos_c=None, cantos_f=None,
                     cards_c=None, cards_f=None,
                     sintese=SinteseMega("remates", "over", "razao", 2))

    # Resultado final: cantos=12 (over acerta, under falha), remates=25 (over acerta)
    totais = {
        "cantos_total": 12,
        "cards_amarelos_total": 4,
        "shots_total": 25,
        "shots_on_target_total": 7,
    }
    with abrir_sessao() as sess:
        n = resolver_sinais_do_evento(sess, 42, totais)
        assert n == 3

    with abrir_sessao() as sess:
        rows = sess.execute(
            select(SinalMegaCerebro).where(SinalMegaCerebro.event_id == 42)
            .order_by(SinalMegaCerebro.id)
        ).scalars().all()
        # over cantos @ 12 >= 11 → acerto
        assert rows[0].resolvido == "acerto"
        assert rows[0].valor_final == 12.0
        # under cantos @ 12 (hi=11, lo=9) → nao <=9 → falha
        assert rows[1].resolvido == "falha"
        # over remates @ 25 >= 22 → acerto
        assert rows[2].resolvido == "acerto"


def test_resolver_pula_se_sem_dado(tmp_db):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import SinalMegaCerebro
    from sqlalchemy import select

    with abrir_sessao() as sess:
        gravar_sinal(sess, event_id=99, liga="L", home="A", away="B",
                     minuto=55, score_casa=0, score_fora=0,
                     sot_c=None, sot_f=None, cantos_c=None, cantos_f=None,
                     cards_c=None, cards_f=None,
                     sintese=SinteseMega("cantos", "over", "razao", 2))

    # Totais sem cantos → nao resolvido
    with abrir_sessao() as sess:
        resolver_sinais_do_evento(sess, 99, {"shots_total": 20})

    with abrir_sessao() as sess:
        row = sess.execute(
            select(SinalMegaCerebro).where(SinalMegaCerebro.event_id == 99)
        ).scalar_one()
        assert row.resolvido == "nao_resolvido"


def test_resolver_so_actua_em_pendentes(tmp_db):
    """Correr resolver 2x e o segundo e no-op."""
    from apostas.utils.db import abrir_sessao

    with abrir_sessao() as sess:
        gravar_sinal(sess, event_id=11, liga="L", home="A", away="B",
                     minuto=55, score_casa=0, score_fora=0,
                     sot_c=None, sot_f=None, cantos_c=None, cantos_f=None,
                     cards_c=None, cards_f=None,
                     sintese=SinteseMega("cantos", "over", "razao", 2))

    totais = {"cantos_total": 15}
    with abrir_sessao() as sess:
        n1 = resolver_sinais_do_evento(sess, 11, totais)
    with abrir_sessao() as sess:
        n2 = resolver_sinais_do_evento(sess, 11, totais)

    assert n1 == 1
    assert n2 == 0  # ja resolvido, nao conta
