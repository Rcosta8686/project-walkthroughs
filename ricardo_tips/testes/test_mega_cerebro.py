"""Testes do mega-cerebro — sintese das 4 heuristicas base."""
from __future__ import annotations

import pytest

from apostas.alertas.mega_cerebro import (
    SinteseMega,
    _ratio_vs_baseline,
    linha_para_telegram,
    sintetizar,
    sintetizar_a_partir_confronto,
)


# ─── ratio_vs_baseline ─────────────────────────────────────────────

def test_ratio_sem_dados_devolve_none():
    assert _ratio_vs_baseline(None, 10.0, 60) is None
    assert _ratio_vs_baseline(5, None, 60) is None
    assert _ratio_vs_baseline(5, 10.0, 0) is None


def test_ratio_calculo():
    # baseline 10/90, minuto 60 => esperado 6.67; actual 9 => ratio ~1.35
    r = _ratio_vs_baseline(9, 10.0, 60)
    assert 1.3 < r < 1.4


# ─── Heuristica 1: amarelos ──────────────────────────────────────

def test_amarelos_disparam_quando_acima_do_normal():
    sint = sintetizar(
        minuto=70, score_casa=1, score_fora=1,
        cards_c=3, cards_f=3,
        baseline_cards_c=2.0, baseline_cards_f=2.0,
    )
    assert sint is not None
    assert sint.mercado == "cards"
    assert sint.direccao == "over"


def test_amarelos_nao_disparam_cedo():
    """Antes de 55' nao dispara heuristica de amarelos."""
    sint = sintetizar(
        minuto=40, score_casa=0, score_fora=0,
        cards_c=3, cards_f=3,
        baseline_cards_c=2.0, baseline_cards_f=2.0,
    )
    # Pode nao ser None (outras heuristicas), mas se for, nao e cards
    if sint is not None:
        assert sint.mercado != "cards"


# ─── Heuristica 2: remates ambos acima ────────────────────────────

def test_remates_ambos_acima_abre_jogo():
    sint = sintetizar(
        minuto=50, score_casa=1, score_fora=1,
        sot_c=8, sot_f=8,
        baseline_sot_c=5.0, baseline_sot_f=5.0,
    )
    assert sint is not None
    assert sint.mercado == "remates"
    assert sint.confianca == 3


def test_remates_so_um_lado_nao_dispara_heuristica2():
    """Se so uma equipa esta acima, heuristica 2 nao faz match."""
    sint = sintetizar(
        minuto=50, score_casa=1, score_fora=0,
        sot_c=10, sot_f=2,
        baseline_sot_c=5.0, baseline_sot_f=5.0,
    )
    # Pode disparar h3 (pressao lateral) se tiver cantos. Sem cantos nem
    # baseline cantos, nao disparar nenhuma.
    if sint is not None:
        assert sint.mercado != "remates"


# ─── Heuristica 3: pressao lateral ────────────────────────────────

def test_cantos_sobem_sem_remates_igual_pressao_lateral():
    sint = sintetizar(
        minuto=65, score_casa=1, score_fora=0,
        # Fora a perder, muitos cantos, poucos SoT
        cantos_c=3, cantos_f=8,
        sot_c=4, sot_f=3,
        baseline_cantos_c=5.0, baseline_cantos_f=5.0,
        baseline_sot_c=5.0, baseline_sot_f=5.0,
    )
    assert sint is not None
    assert sint.mercado == "cantos"
    assert sint.direccao == "over"


# ─── Heuristica 4: equipa dominante a controlar ───────────────────

def test_dominante_a_controlar_dispara_under_cantos():
    sint = sintetizar(
        minuto=70, score_casa=2, score_fora=0,
        cantos_c=3, cantos_f=2,
        baseline_cantos_c=6.0, baseline_cantos_f=5.0,
        posse_c=62, posse_f=42,   # casa dominante
        remates_a_perder_f=10.0, baseline_remates_f=12.0,  # fora NAO chasing
    )
    # Deveria entrar na heuristica 4 — equipa dominante a controlar
    assert sint is not None
    assert sint.mercado == "under_cantos"
    assert sint.direccao == "under"


# ─── Sinal fraco → silencio ──────────────────────────────────────

def test_sem_dados_devolve_none():
    sint = sintetizar(minuto=50, score_casa=0, score_fora=0)
    assert sint is None


def test_tudo_dentro_do_normal_devolve_none():
    sint = sintetizar(
        minuto=50, score_casa=0, score_fora=0,
        sot_c=3, sot_f=3,
        cantos_c=3, cantos_f=3,
        cards_c=1, cards_f=1,
        baseline_sot_c=5.0, baseline_sot_f=5.0,
        baseline_cantos_c=5.0, baseline_cantos_f=5.0,
        baseline_cards_c=2.0, baseline_cards_f=2.0,
    )
    assert sint is None


# ─── to_telegram ─────────────────────────────────────────────────

def test_to_telegram_formata_linha():
    s = SinteseMega(
        mercado="cantos", direccao="over",
        razao="pressao pelas pontas", confianca=2,
    )
    linha = s.to_telegram()
    assert "🧠 Mega" in linha
    assert "cantos" in linha
    assert "over" in linha
    assert "pressao" in linha


# ─── sintetizar_a_partir_confronto ───────────────────────────────

class _Base:
    def __init__(self, remates=12, sot=5, cantos=5, cards=2.0, posse=50):
        self.remates = remates
        self.sot = sot
        self.cantos = cantos
        self.cards_amarelos = cards
        self.posse_pct = posse


class _PorRes:
    def __init__(self, remates_L=12.0):
        self.remates_L = remates_L


class _Ficha:
    def __init__(self, jogos=10, baseline=None, por_resultado=None):
        self.jogos_lidos = jogos
        self.baseline = baseline or _Base()
        self.por_resultado = por_resultado or _PorRes()


def test_wrapper_devolve_none_quando_ficha_fraca():
    r = sintetizar_a_partir_confronto(
        minuto=60, score_casa=1, score_fora=0,
        sot_c=10, sot_f=10, cantos_c=5, cantos_f=5,
        cards_c=2, cards_f=2,
        ficha_casa=_Ficha(jogos=2), ficha_fora=_Ficha(jogos=10),
    )
    assert r is None


def test_wrapper_devolve_sintese_quando_sinal():
    r = sintetizar_a_partir_confronto(
        minuto=50, score_casa=1, score_fora=1,
        sot_c=10, sot_f=10, cantos_c=5, cantos_f=5,
        cards_c=1, cards_f=1,
        ficha_casa=_Ficha(baseline=_Base(sot=5)),
        ficha_fora=_Ficha(baseline=_Base(sot=5)),
    )
    assert r is not None
    assert r.mercado == "remates"


# ─── linha_para_telegram ─────────────────────────────────────────

def test_linha_telegram_vazio_quando_confronto_falha():
    def confronto_falha(*a, **k):
        raise RuntimeError("sem rede")
    assert linha_para_telegram(
        home_id=1, away_id=2, minuto=50,
        score_casa=0, score_fora=0,
        confronto_fn=confronto_falha,
    ) == ""


def test_linha_telegram_prefixa_newline_quando_sinal():
    class _Res:
        ficha_casa = _Ficha(baseline=_Base(sot=5))
        ficha_fora = _Ficha(baseline=_Base(sot=5))
    def confronto_ok(*a, **k):
        return _Res()
    linha = linha_para_telegram(
        home_id=1, away_id=2, minuto=55,
        score_casa=1, score_fora=1,
        sot_c=10, sot_f=10,
        confronto_fn=confronto_ok,
    )
    assert linha.startswith("\n")
    assert "🧠" in linha
