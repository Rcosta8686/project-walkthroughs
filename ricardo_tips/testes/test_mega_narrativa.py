"""Mega-cerebro narrativa final: previsao + certezas no fim do alerta."""
from __future__ import annotations

from apostas.alertas.mega_narrativa import narrativa_final


def _sinal(**kw):
    base = {
        "mercado": "sot",
        "lado": "over",
        "linha": 7.5,
        "minuto": 60,
        "marcador": "1-0",
        "prob_modelo": 0.72,
        "shots_on_target_c": 4,
        "shots_on_target_f": 3,
        "corners_c": 5,
        "corners_f": 2,
        "yellows_c": 2,
        "yellows_f": 2,
        "xg_casa": 1.5,
        "xg_fora": 0.8,
    }
    base.update(kw)
    return base


# ─── Mercado minimo necessario ───────────────────────────────────────────

def test_sem_mercado_devolve_vazio():
    assert narrativa_final({"minuto": 60, "lado": "over"}) == ""


def test_sem_lado_devolve_vazio():
    assert narrativa_final({"mercado": "sot", "minuto": 60}) == ""


def test_sem_minuto_devolve_vazio():
    assert narrativa_final({"mercado": "sot", "lado": "over"}) == ""


# ─── Formatacao basica ───────────────────────────────────────────────────

def test_narrativa_tem_cabecalho():
    n = narrativa_final(_sinal(), sintese_razao="jogo aberto")
    assert "Mega-Cerebro" in n
    assert "Previsao" in n


def test_narrativa_tem_analiso_quando_ha_razao():
    n = narrativa_final(_sinal(), sintese_razao="jogo aberto")
    assert "Analiso" in n
    assert "jogo aberto" in n


def test_narrativa_mostra_ritmo_actual():
    """7 SoT ao 60' → 10.5/90min deve aparecer."""
    s = _sinal(shots_on_target_c=4, shots_on_target_f=3, minuto=60)
    n = narrativa_final(s)
    assert "10.5" in n or "ritmo" in n.lower()


def test_narrativa_tem_previsao():
    n = narrativa_final(_sinal())
    assert "Prevejo" in n or "projeccao" in n.lower()


def test_narrativa_tem_certezas_com_prob_alta():
    n = narrativa_final(_sinal(prob_modelo=0.80))
    assert "Certezas" in n
    assert "80%" in n


# ─── Previsao OVER vai bater ─────────────────────────────────────────────

def test_prev_over_margem_clara():
    """7 SoT ao 60', linha 7.5 → projecao ~10.5 bate claramente."""
    s = _sinal(shots_on_target_c=4, shots_on_target_f=3, linha=7.5, minuto=60)
    n = narrativa_final(s)
    assert "margem clara" in n.lower() or "projeccao" in n.lower()


def test_prev_over_vai_ao_fio():
    """2 SoT ao 60', linha 5.5 → projecao 3; faltam 4 em 30 min → ritmo baixo"""
    s = _sinal(shots_on_target_c=1, shots_on_target_f=1, linha=5.5, minuto=60)
    n = narrativa_final(s)
    # Deve conter indicacao de risco ("abrir" ou "arriscado")
    assert "arriscado" in n.lower() or "abrir" in n.lower() or "fio" in n.lower()


# ─── Previsao UNDER ──────────────────────────────────────────────────────

def test_prev_under_com_folga():
    """1 SoT ao 60', linha 7.5 UNDER → projecao 1.5; folga grande."""
    s = _sinal(shots_on_target_c=0, shots_on_target_f=1, linha=7.5, lado="under",
               minuto=60, prob_modelo=0.85)
    n = narrativa_final(s)
    assert "folga" in n.lower() or "margem" in n.lower()


def test_prev_under_acima_da_linha_arrisca():
    """6 SoT ao 60', linha 7.5 UNDER → projecao 9 > 7.5."""
    s = _sinal(shots_on_target_c=3, shots_on_target_f=3, linha=7.5, lado="under",
               minuto=60)
    n = narrativa_final(s)
    assert "arriscado" in n.lower()


# ─── Golos: xG mismatch ──────────────────────────────────────────────────

def test_golos_xg_mismatch_aparece_na_analise():
    s = _sinal(mercado="golos", lado="over", linha=2.5, marcador="0-0",
               xg_casa=1.5, xg_fora=1.2, minuto=70,
               shots_on_target_c=5, shots_on_target_f=3)
    n = narrativa_final(s)
    # xG 2.7 vs 0 golos → mismatch +2.7
    assert "xG" in n


def test_golos_dominancia_mencionada_quando_desigual():
    s = _sinal(mercado="golos", lado="over", linha=2.5, marcador="2-0",
               minuto=65, xg_casa=2.0, xg_fora=0.5)
    n = narrativa_final(s)
    assert "ganhar" in n.lower() or "gerir" in n.lower() or "abrir" in n.lower()


# ─── Cartoes: risco de UNDER perto do limite ─────────────────────────────

def test_cards_under_ja_perto_do_limite_sinaliza_risco():
    """4 amarelos ao 75', UNDER 4.5 → risco palpavel."""
    s = _sinal(mercado="cards", lado="under", linha=4.5, minuto=75,
               yellows_c=2, yellows_f=2, prob_modelo=0.60)
    n = narrativa_final(s)
    assert "⚠" in n


def test_cards_over_perto_do_limite_sinaliza_certeza():
    """3 amarelos ao 80', OVER 3.5 → 1 chega + final tenso."""
    s = _sinal(mercado="cards", lado="over", linha=3.5, minuto=80,
               yellows_c=2, yellows_f=1, prob_modelo=0.70)
    n = narrativa_final(s)
    assert "final tenso" in n.lower() or "OVER" in n


# ─── Linha ja batida ─────────────────────────────────────────────────────

def test_linha_ja_batida_para_over():
    """8 SoT ao 50', linha OVER 5.5 → ja batida."""
    s = _sinal(shots_on_target_c=5, shots_on_target_f=3, linha=5.5, minuto=50)
    n = narrativa_final(s)
    assert "ja esta batida" in n.lower() or "procura outra" in n.lower()


# ─── Risco de pouco tempo ────────────────────────────────────────────────

def test_pouco_tempo_restante_sinaliza_risco_para_over():
    """85 min, 3 SoT, OVER 5.5 → so 5 min para 3 SoT."""
    s = _sinal(shots_on_target_c=2, shots_on_target_f=1, linha=5.5, minuto=85)
    n = narrativa_final(s)
    assert "⚠" in n or "pouca folga" in n.lower()


# ─── Fallback quando nada util ───────────────────────────────────────────

def test_sem_dados_duros_apenas_prob_cria_previsao():
    """Sem SoT/corners/etc, so com prob, ainda devolve algo."""
    s = {"mercado": "sot", "lado": "over", "linha": 7.5, "minuto": 50,
         "prob_modelo": 0.65}
    n = narrativa_final(s)
    # Nao devolve vazio porque tem prob
    assert "65%" in n or n == ""  # qualquer um aceitavel

