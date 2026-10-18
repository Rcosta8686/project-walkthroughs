"""Testes para Fase 2c: CLV automatico no fecho de ApostaRealLive."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from apostas.modelos import clv_tracker
from apostas.utils import config as config_mod, db as db_mod
from apostas.utils.schema import ApostaRealLive


def _carregar_resolver():
    """Carrega o script como modulo (nao tem __init__.py)."""
    raiz = Path(__file__).resolve().parent.parent
    caminho = raiz / "scripts" / "resolver_sugestoes_live.py"
    spec = importlib.util.spec_from_file_location("resolver_sugestoes_live", caminho)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolver_sugestoes_live"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bd_vazia(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    yield tmp_path
    db_mod.reset_engine()


# ─── clv_live (unit) ────────────────────────────────────────────────

def test_clv_live_user_bateu_odd_recomendada():
    # Esperou mercado mover: entrou a 2.10 vs recomendada 1.95 → +7.7%
    clv = clv_tracker.clv_live(2.10, 1.95)
    assert clv == pytest.approx(2.10 / 1.95 - 1.0)
    assert clv > 0


def test_clv_live_user_apanhou_odd_pior():
    # Entrou tarde: 1.80 vs recomendada 1.95 → -7.7%
    clv = clv_tracker.clv_live(1.80, 1.95)
    assert clv < 0


def test_clv_live_odds_invalidas_devolvem_none():
    assert clv_tracker.clv_live(2.0, 1.0) is None
    assert clv_tracker.clv_live(1.0, 2.0) is None
    assert clv_tracker.clv_live(2.0, None) is None  # type: ignore[arg-type]


# ─── _resolver_apostas_reais_live (fecha + grava CLV) ───────────────

def _criar_aposta(aid: int, odd_rec: float | None, odd_exec: float, stake: float) -> int:
    with db_mod.abrir_sessao() as s:
        aposta = ApostaRealLive(
            alert_id=aid, event_id=100, liga="Serie A",
            home="Roma", away="Milan", minuto=55,
            mercado="golos", lado="over", linha="2.5",
            casa_recomendada="betano",
            odd_recomendada=odd_rec, odd_executada=odd_exec, stake=stake,
            prob_modelo=0.58, resultado="pendente",
        )
        s.add(aposta)
        s.flush()
        return aposta.id


def test_resolver_fecha_aposta_W_com_clv(bd_vazia):
    rid = _criar_aposta(42, odd_rec=1.95, odd_exec=2.10, stake=2.0)
    mod = _carregar_resolver()

    n = mod._resolver_apostas_reais_live([42], {42: "W"}, {})
    assert n == 1

    with db_mod.abrir_sessao() as s:
        row = s.get(ApostaRealLive, rid)
        assert row.resultado == "W"
        assert row.lucro == pytest.approx((2.10 - 1) * 2.0)  # (odd-1)*stake
        assert row.clv == pytest.approx(2.10 / 1.95 - 1.0)


def test_resolver_fecha_aposta_L_com_lucro_negativo(bd_vazia):
    rid = _criar_aposta(43, odd_rec=1.95, odd_exec=1.90, stake=3.0)
    mod = _carregar_resolver()

    mod._resolver_apostas_reais_live([43], {43: "L"}, {})
    with db_mod.abrir_sessao() as s:
        row = s.get(ApostaRealLive, rid)
        assert row.resultado == "L"
        assert row.lucro == -3.0  # stake inteiro
        assert row.clv == pytest.approx(1.90 / 1.95 - 1.0)
        assert row.clv < 0  # entrou tarde


def test_resolver_push_nao_altera_banca_mas_marca(bd_vazia):
    rid = _criar_aposta(44, odd_rec=1.95, odd_exec=1.95, stake=1.0)
    mod = _carregar_resolver()

    mod._resolver_apostas_reais_live([44], {44: "push"}, {})
    with db_mod.abrir_sessao() as s:
        row = s.get(ApostaRealLive, rid)
        assert row.resultado == "push"
        assert row.lucro == 0.0


def test_resolver_e_idempotente(bd_vazia):
    rid = _criar_aposta(45, odd_rec=1.95, odd_exec=2.00, stake=1.0)
    mod = _carregar_resolver()

    assert mod._resolver_apostas_reais_live([45], {45: "W"}, {}) == 1
    # Segunda chamada nao re-abre: resultado ja nao e 'pendente'
    assert mod._resolver_apostas_reais_live([45], {45: "L"}, {}) == 0
    with db_mod.abrir_sessao() as s:
        row = s.get(ApostaRealLive, rid)
        assert row.resultado == "W"  # mantem-se


def test_resolver_sem_odd_recomendada_deixa_clv_a_none(bd_vazia):
    _criar_aposta(46, odd_rec=None, odd_exec=2.00, stake=1.0)
    mod = _carregar_resolver()

    mod._resolver_apostas_reais_live([46], {46: "W"}, {})
    with db_mod.abrir_sessao() as s:
        row = s.scalar(select(ApostaRealLive).where(ApostaRealLive.alert_id == 46))
        assert row.resultado == "W"
        assert row.clv is None
