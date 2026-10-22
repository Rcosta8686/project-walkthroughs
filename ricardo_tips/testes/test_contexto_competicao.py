"""Testes do contexto de competicao — peso + classificacao de necessidade."""
from __future__ import annotations

import pytest

from apostas.segundo_cerebro.contexto_competicao import (
    ContextoJogo,
    _classificar_necessidade,
    _classificar_peso,
    obter_contexto,
    resumo_telegram,
)


# ─── peso ──────────────────────────────────────────────────────────

def test_peso_ucl_e_alto():
    peso, _ = _classificar_peso("UEFA Champions League")
    assert peso == "alto"


def test_peso_final_e_muito_alto():
    peso, _ = _classificar_peso("Champions League Final")
    assert peso == "muito_alto"


def test_peso_semi_e_muito_alto():
    peso, _ = _classificar_peso("Copa do Rei", fase="Semi-Final")
    assert peso == "muito_alto"


def test_peso_premier_e_medio():
    peso, _ = _classificar_peso("Premier League")
    assert peso == "medio"


def test_peso_youth_e_baixo():
    peso, _ = _classificar_peso("Bundesliga U19")
    assert peso == "baixo"


def test_peso_regional_e_baixo():
    peso, _ = _classificar_peso("Regionalliga Nordost")
    assert peso == "baixo"


# ─── necessidade ──────────────────────────────────────────────────

def test_liga_top2_ja_qualificada():
    ja, prec = _classificar_necessidade(
        "Premier League", posicao=2, pontos=50, jogos=20, total_rows=20,
    )
    assert ja is True
    assert prec is False


def test_liga_zona_descida_precisa_pts():
    ja, prec = _classificar_necessidade(
        "Premier League", posicao=18, pontos=15, jogos=20, total_rows=20,
    )
    assert ja is False
    assert prec is True


def test_ucl_liguilha_top6_ja_qualificada():
    ja, prec = _classificar_necessidade(
        "Champions League", posicao=4, pontos=12, jogos=5, total_rows=36,
    )
    assert ja is True
    assert prec is False


def test_ucl_liguilha_bottom_precisa():
    ja, prec = _classificar_necessidade(
        "Champions League", posicao=28, pontos=2, jogos=5, total_rows=36,
    )
    assert ja is False
    assert prec is True


def test_sem_posicao_devolve_False_False():
    ja, prec = _classificar_necessidade("X", None, None, None, 20)
    assert ja is False
    assert prec is False


# ─── obter_contexto ──────────────────────────────────────────────

def test_contexto_sem_ids_devolve_so_peso(monkeypatch):
    ctx = obter_contexto(liga_nome="UEFA Champions League")
    assert ctx.peso_jogo == "alto"
    assert ctx.posicao_casa is None


def test_contexto_com_standings_mocked(monkeypatch):
    """Mocka o fetch de standings para evitar rede."""
    from apostas.segundo_cerebro import contexto_competicao as cc

    def fake_fetch(tid, sid):
        return [
            {"team_id": 1, "team_name": "A", "posicao": 2, "pontos": 50,
             "jogos": 20},
            {"team_id": 2, "team_name": "B", "posicao": 18, "pontos": 15,
             "jogos": 20},
        ]
    monkeypatch.setattr(cc, "_fetch_standings", fake_fetch)
    # Usar 20 equipas no fetch mock para ter total >= 16
    def fake_fetch_full(tid, sid):
        base = fake_fetch(tid, sid)
        base.extend([
            {"team_id": i + 10, "team_name": f"X{i}", "posicao": i + 2,
             "pontos": 20, "jogos": 20} for i in range(1, 19)
        ])
        return base
    monkeypatch.setattr(cc, "_fetch_standings", fake_fetch_full)
    ctx = obter_contexto(
        liga_nome="Premier League",
        tournament_id=17, season_id=100,
        home_id=1, away_id=2,
    )
    assert ctx.posicao_casa == 2
    assert ctx.posicao_fora == 18
    assert ctx.casa_ja_qualificada is True
    assert ctx.fora_precisa_pts is True


# ─── resumo_telegram ──────────────────────────────────────────────

def test_resumo_vazio_quando_peso_baixo_sem_standings():
    ctx = ContextoJogo(peso_jogo="baixo", razao="liga regular")
    assert resumo_telegram(ctx, "A", "B") == ""


def test_resumo_mostra_peso_alto():
    ctx = ContextoJogo(peso_jogo="muito_alto", razao="final")
    txt = resumo_telegram(ctx, "A", "B")
    assert "MUITO ALTO" in txt
    assert "final" in txt


def test_resumo_mostra_necessidade():
    ctx = ContextoJogo(
        peso_jogo="alto", razao="ucl",
        posicao_casa=2, posicao_fora=18,
        casa_ja_qualificada=True, fora_precisa_pts=True,
    )
    txt = resumo_telegram(ctx, "A", "B")
    assert "A segura" in txt
    assert "B precisa pts" in txt
