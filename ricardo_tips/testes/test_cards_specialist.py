"""Especialista em cartoes: 7 factores combinados numa projeccao rica."""
from __future__ import annotations

from apostas.modelos.cards_specialist import (
    _rate_fase, narrativa_para_telegram, projetar_cartoes,
)


# ─── _rate_fase ──────────────────────────────────────────────────────────

def test_rate_fase_cedo_e_baixo():
    assert _rate_fase(15) == 0.85
    assert _rate_fase(10) == 0.85


def test_rate_fase_reta_final_alto():
    assert _rate_fase(80) == 1.60
    assert _rate_fase(89) == 1.60


def test_rate_fase_descontos_mais_alto_ainda():
    assert _rate_fase(93) == 1.80


def test_rate_fase_meio_jogo_moderado():
    assert _rate_fase(60) == 1.15
    assert _rate_fase(50) == 1.00


# ─── projetar_cartoes baseline ───────────────────────────────────────────

def test_baseline_simples_sem_factores_extras():
    """Sem arbitro, derby, vermelhos. Marcador 1-0 ao 60'. Baseline 4/90."""
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60,
        marcador_casa=1, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    assert p.cards_actual == 2
    assert p.minuto == 60
    # 30 min restantes, baseline 4/90 * 0.33 * mult_marcador 1.05
    # * mult_fase (meio do resto = 75) 1.60 ≈ 2.22 → final ~4.2
    assert 3.5 <= p.cards_proj_final <= 5.0
    # Factores
    assert p.factor_arbitro == 1.0
    assert p.factor_derby == 1.0
    assert p.factor_vermelho == 1.0


def test_arbitro_severo_puxa_proj_para_cima():
    """Mateu Lahoz é severo (6.5 cards/jogo → fator ~1.51)."""
    kw = dict(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=50,
        marcador_casa=0, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    sem_arb = projetar_cartoes(**kw)
    com_arb = projetar_cartoes(nome_arbitro="Mateu Lahoz", **kw)
    assert com_arb.cards_proj_final > sem_arb.cards_proj_final
    assert com_arb.factor_arbitro > 1.3


def test_derby_acrescenta_15pct():
    """Derby Benfica vs Sp Lisbon da +15% cartoes."""
    # Verifica se o derby esta na lista
    from apostas.modelos.referencias import eh_derby
    assert eh_derby("Benfica", "Sp Lisbon") is True
    p_derby = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=50,
        marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
        nome_casa="Benfica", nome_fora="Sp Lisbon",
    )
    assert p_derby.factor_derby == 1.15


def test_sem_derby_fator_e_1():
    p = projetar_cartoes(
        cards_actual_casa=0, cards_actual_fora=0,
        minuto=10, marcador_casa=0, marcador_fora=0,
        baseline_cards_por_90=4.0,
        nome_casa="Qualquer Equipa", nome_fora="Outra Qualquer",
    )
    assert p.factor_derby == 1.0


def test_vermelho_aumenta_fator():
    sem_vermelho = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
    )
    com_vermelho = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
        vermelhos_em_campo=1,
    )
    assert com_vermelho.factor_vermelho == 1.35
    assert com_vermelho.cards_proj_final > sem_vermelho.cards_proj_final


def test_empate_tem_mais_tensao_que_goleada():
    empate = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
    )
    goleada = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=3, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    assert empate.factor_marcador > goleada.factor_marcador


def test_ritmo_quente_amplifica():
    """6 cards ao 40' quando esperado era ~1.8 → ritmo 3x → factor 1.2."""
    p = projetar_cartoes(
        cards_actual_casa=3, cards_actual_fora=3,
        minuto=40, marcador_casa=0, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    assert p.factor_ritmo == 1.20


def test_ritmo_normal_nao_amplifica():
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=50, marcador_casa=0, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    assert p.factor_ritmo == 1.0


def test_arbitro_observacionalmente_severo():
    """Se faltas/amarelo <= 5, soma +10% ao factor arbitro."""
    kw = dict(
        cards_actual_casa=2, cards_actual_fora=2,
        minuto=60, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
    )
    sem_fouls = projetar_cartoes(**kw)
    # 4 cards + 15 faltas → ratio 3.75 → severo observacionalmente
    com_fouls = projetar_cartoes(fouls_actual_total=15, **kw)
    assert com_fouls.factor_arbitro > sem_fouls.factor_arbitro


# ─── Prob OVER por linha ─────────────────────────────────────────────────

def test_prob_over_decresce_com_linha_mais_alta():
    p = projetar_cartoes(
        cards_actual_casa=2, cards_actual_fora=2,
        minuto=60, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
    )
    probs = p.prob_over_por_linha
    # OVER 2.5 (ja superou) deve ser quase certo
    assert probs[2.5] >= 0.95
    # OVER 7.5 (longe) deve ser pequeno
    assert probs[7.5] < probs[2.5]


def test_prob_linha_ja_batida_e_1():
    p = projetar_cartoes(
        cards_actual_casa=3, cards_actual_fora=3,
        minuto=50, marcador_casa=1, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    # 6 cards > 5.5 → prob 100%
    assert p.prob_over_por_linha[5.5] == 1.0


# ─── Confianca ───────────────────────────────────────────────────────────

def test_confianca_minima_sem_desvios():
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=45, marcador_casa=1, marcador_fora=0,
        baseline_cards_por_90=4.0,
    )
    assert 1 <= p.confianca <= 5


def test_confianca_cresce_com_factores():
    """Mateu Lahoz + derby + vermelho + apertado + reta final → confianca max."""
    p = projetar_cartoes(
        cards_actual_casa=2, cards_actual_fora=2,
        minuto=80, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.5,
        nome_arbitro="Mateu Lahoz",
        nome_casa="Benfica", nome_fora="Sp Lisbon",
        vermelhos_em_campo=1,
    )
    assert p.confianca >= 4


# ─── Razoes textuais ─────────────────────────────────────────────────────

def test_razoes_arbitro_severo():
    p = projetar_cartoes(
        cards_actual_casa=0, cards_actual_fora=0,
        minuto=40, marcador_casa=0, marcador_fora=0,
        baseline_cards_por_90=4.0,
        nome_arbitro="Mateu Lahoz",
    )
    assert any("Lahoz" in r for r in p.razoes)


def test_razoes_derby():
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=50, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
        nome_casa="Benfica", nome_fora="Sp Lisbon",
    )
    assert any("Derby" in r or "rivalidade" in r.lower() for r in p.razoes)


def test_razoes_empate_tenso():
    p = projetar_cartoes(
        cards_actual_casa=2, cards_actual_fora=2,
        minuto=70, marcador_casa=1, marcador_fora=1,
        baseline_cards_por_90=4.0,
    )
    assert any("empate" in r.lower() or "tenso" in r.lower() or "apertado" in r.lower()
               or "reta final" in r.lower() or "pico" in r.lower()
               for r in p.razoes)


# ─── Narrativa Telegram ──────────────────────────────────────────────────

def test_narrativa_tem_cabecalho():
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=1, marcador_fora=0,
        baseline_cards_por_90=4.0,
        nome_arbitro="Mateu Lahoz",
    )
    n = narrativa_para_telegram(p, linha_alvo=5.5, lado="over")
    assert "Mega-Especialista" in n
    assert "Cartoes" in n
    assert "OVER 5.5" in n


def test_narrativa_escolhe_linha_mais_acionavel_se_none():
    p = projetar_cartoes(
        cards_actual_casa=1, cards_actual_fora=1,
        minuto=60, marcador_casa=1, marcador_fora=0,
        baseline_cards_por_90=4.5,
    )
    n = narrativa_para_telegram(p, linha_alvo=None, lado="over")
    assert "OVER" in n


def test_narrativa_vazia_sem_dados():
    from apostas.modelos.cards_specialist import ProjecaoCartoes
    p = ProjecaoCartoes(
        cards_actual=0, minuto=0, cards_proj_final=0.0, lam_restante=0.0,
        factor_arbitro=1.0, factor_derby=1.0, factor_marcador=1.0,
        factor_fase=1.0, factor_ritmo=1.0, factor_vermelho=1.0,
        prob_over_por_linha={},
    )
    assert narrativa_para_telegram(p) == ""
