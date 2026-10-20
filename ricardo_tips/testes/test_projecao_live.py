"""Testes para apostas/modelos/projecao_live.py."""
from __future__ import annotations

import pytest

from apostas.modelos import projecao_live as pl


def test_projecao_total_simples():
    """Minuto 55, equipas iguais, sem fadiga, 0-0."""
    proj = pl.projetar(
        mercado="sot",
        minuto=55,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=2,
        valor_atual_fora=2,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
    )
    assert proj.valor_atual_total == 4
    # Sem contexto (empate): proj = 4 + 2 + 2 = 8
    assert proj.projecao_final_total == pytest.approx(8.0, abs=0.1)
    assert proj.projecao_final_casa == pytest.approx(4.0, abs=0.1)
    assert proj.projecao_final_fora == pytest.approx(4.0, abs=0.1)
    # Deve ter 3 linhas a volta do centro (7.5, 8.5, 9.5 ou similar)
    assert len(proj.linhas_total) == 3


def test_ajuste_a_ganhar_reduz():
    """Casa a ganhar 1-0: lam_rest_casa reduzido 15%, lam_rest_fora aumentado 25%.
    So com aplicar_contexto=True (default e False — delega ao live_predictor)."""
    proj = pl.projetar(
        mercado="sot",
        minuto=55,
        score_casa=1,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=2,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
        aplicar_contexto=True,
    )
    # Casa (a ganhar): 3 + 2*0.85 = 4.7
    assert proj.projecao_final_casa == pytest.approx(4.7, abs=0.1)
    # Fora (a perder 1): 2 + 2*1.25 = 4.5
    assert proj.projecao_final_fora == pytest.approx(4.5, abs=0.1)
    # Total: 4.7 + 4.5 = 9.2
    assert proj.projecao_final_total == pytest.approx(9.2, abs=0.1)


def test_perder_por_2_mais_remates():
    """Equipa a perder 0-2: boost +45% em SOT. So com aplicar_contexto=True."""
    proj = pl.projetar(
        mercado="sot",
        minuto=65,
        score_casa=0,
        score_fora=2,
        valor_atual_casa=4,
        valor_atual_fora=3,
        lam_rest_casa=1.5,
        lam_rest_fora=1.5,
        nome_casa="A",
        nome_fora="B",
        aplicar_contexto=True,
    )
    # Casa (a perder 2+): 4 + 1.5*1.45 = 6.175
    assert proj.projecao_final_casa == pytest.approx(6.2, abs=0.1)
    # Fora (a ganhar 2+): 3 + 1.5*0.85 = 4.275
    assert proj.projecao_final_fora == pytest.approx(4.3, abs=0.1)


def test_fadiga_so_2a_parte():
    """Fadiga aplica-se apenas se minuto >= 46."""
    # Minuto 30 (1a parte): fadiga nao aplica
    proj_1t = pl.projetar(
        mercado="sot",
        minuto=30,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=1,
        valor_atual_fora=1,
        lam_rest_casa=3.0,
        lam_rest_fora=3.0,
        dias_descanso_casa=2,  # fadiga
        nome_casa="A",
        nome_fora="B",
    )
    assert proj_1t.aviso_fadiga is None
    assert proj_1t.projecao_final_casa == pytest.approx(4.0, abs=0.1)  # 1 + 3

    # Minuto 60 (2a parte): fadiga aplica
    proj_2t = pl.projetar(
        mercado="sot",
        minuto=60,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=3,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        dias_descanso_casa=2,
        nome_casa="A",
        nome_fora="B",
    )
    assert proj_2t.aviso_fadiga is not None
    assert "A jogou ha 2 dias" in proj_2t.aviso_fadiga
    # Casa com fadiga: 3 + 2*0.92 = 4.84
    assert proj_2t.projecao_final_casa == pytest.approx(4.84, abs=0.1)


def test_odd_justa_coerente():
    """Odd justa over * prob = 1. Linha abaixo da projeccao deve ter over < 2.0."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=50,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=3,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
    )
    for lo in proj.linhas_total:
        assert lo.over_justa * lo.prob_over == pytest.approx(1.0, abs=0.1)
        assert lo.under_justa * (1 - lo.prob_over) == pytest.approx(1.0, abs=0.1)


def test_formatar_telegram_contem_essencial():
    """Mensagem Telegram tem todos os campos chave."""
    proj = pl.projetar(
        mercado="sot",
        minuto=56,
        score_casa=1,
        score_fora=0,
        valor_atual_casa=4,
        valor_atual_fora=0,
        lam_rest_casa=1.5,
        lam_rest_fora=1.5,
        nome_casa="AS Roma",
        nome_fora="Genoa",
    )
    msg = pl.formatar_telegram(proj, "AS Roma", "Genoa", 1, 0)
    assert "PROJECAO" in msg
    assert "56'" in msg
    assert "AS Roma" in msg
    assert "Genoa" in msg
    assert "1-0" in msg
    assert "Remates a baliza" in msg
    assert "projeccao" in msg
    assert "⭐" in msg
    assert "🎯" in msg  # emoji SoT no topo e footer
    assert "Analista Projecao" in msg
    # Mudanca: nao devem aparecer as linhas de over/under por linha
    assert "over @" not in msg
    assert "under @" not in msg
    # Mudanca: nao deve ter a linha duplicada do estado
    assert "reduz, nao abre" not in msg


def test_formatar_telegram_com_extras():
    """Extras (vozes de treinadores) sao injectados antes da nota final."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=55,
        score_casa=1,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=2,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
    )
    msg = pl.formatar_telegram(
        proj, "A", "B", 1, 0,
        extras="\n🎙 Treinador A: a ganhar 1 ao 55'",
    )
    assert "🎙 Treinador A" in msg
    # Extras aparecem antes da nota final
    pos_extras = msg.index("🎙 Treinador A")
    pos_nota = msg.index("So aposta")
    assert pos_extras < pos_nota


def test_deve_projetar_dispara_so_uma_vez():
    """deve_projetar devolve target apenas se nao foi disparado."""
    ja = set()
    # Minuto 30: nenhum target
    assert pl.deve_projetar(30, ja) is None
    # Minuto 42: dispara 40
    assert pl.deve_projetar(42, ja) == 40
    ja.add(40)
    # Minuto 44: nao re-dispara 40
    assert pl.deve_projetar(44, ja) is None
    # Minuto 56: dispara 55
    assert pl.deve_projetar(56, ja) == 55
    ja.add(55)
    # Minuto 72: dispara 70
    assert pl.deve_projetar(72, ja) == 70


def test_tem_valor_actionavel_todas_quase_certas_devolve_false():
    """Se projeccao e muito alta e linhas sao baixas, todas as probs ~1
    -> nao ha linha interessante, nao devolve valor accionavel."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=55,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=10,
        valor_atual_fora=10,
        lam_rest_casa=0.1,
        lam_rest_fora=0.1,
        nome_casa="A",
        nome_fora="B",
    )
    # valor_actual = 20, lam_rest ~0 -> projecao ~20.2, linhas 19.5/20.5/21.5
    # 19.5 ja passou (prob=1), 21.5 improvavel (prob<0.15)
    # Todas as linhas estao em "quase certo" -> False
    assert not pl.tem_valor_actionavel(proj)


def test_tem_valor_actionavel_linha_media_devolve_true():
    """Projeccao razoavel com linha centrada -> prob ~50% -> True."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=50,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=3,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
    )
    assert pl.tem_valor_actionavel(proj)


def test_estrelas_projecao():
    """Estrelas 1-3 consoante o numero de linhas accionaveis (0.25-0.75)."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=55,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=3,
        valor_atual_fora=3,
        lam_rest_casa=2.0,
        lam_rest_fora=2.0,
        nome_casa="A",
        nome_fora="B",
    )
    n = pl._estrelas_projecao(proj)
    assert 1 <= n <= 3


def test_valor_actual_ja_passou_linha():
    """Se o valor actual ja ultrapassou a linha, prob = 1.0 (over certo)."""
    proj = pl.projetar(
        mercado="cantos",
        minuto=85,
        score_casa=0,
        score_fora=0,
        valor_atual_casa=6,
        valor_atual_fora=5,
        lam_rest_casa=0.3,
        lam_rest_fora=0.3,
        nome_casa="A",
        nome_fora="B",
    )
    # linhas: valor actual = 11, projeccao ~11.6, linhas 10.5/11.5/12.5
    # Linha 10.5 ja esta ultrapassada -> prob = 1
    linha_abaixo = [l for l in proj.linhas_total if l.linha < 11.0][0]
    assert linha_abaixo.prob_over == 1.0
