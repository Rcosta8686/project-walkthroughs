"""Testes dos bullets dinamicos de analise."""
from __future__ import annotations

import pytest

from apostas.alertas.analise_mensagem import (
    _bullet_cantos_pressao,
    _bullet_cards_acumulando,
    _bullet_ritmo_actual,
    _bullet_shots_on_target,
    _bullet_xg_vs_golos,
    _risco_cards_proximo_expulsao,
    _risco_minutos_restantes,
    construir_analise,
    construir_riscos,
    seccao_analise_telegram,
    seccao_risco_telegram,
)


# ─── Ritmo actual ──────────────────────────────────────────────────

def test_ritmo_actual_cantos_acima_da_linha():
    s = {
        "mercado": "cantos", "minuto": 60, "linha": 9.5,
        "corners_c": 5, "corners_f": 4,
        # valor atual 9 ao 60 → 90/60*9 = 13.5
    }
    b = _bullet_ritmo_actual(s)
    assert b is not None
    assert "13.5" in b
    assert "acima" in b


def test_ritmo_actual_sem_dado_devolve_none():
    s = {"mercado": "cantos", "minuto": 60, "linha": 9.5}
    assert _bullet_ritmo_actual(s) is None


def test_ritmo_actual_cedo_demais_devolve_none():
    s = {"mercado": "cantos", "minuto": 10, "linha": 9.5,
         "corners_c": 2, "corners_f": 1}
    assert _bullet_ritmo_actual(s) is None


# ─── SoT ──────────────────────────────────────────────────────────

def test_sot_bullet_mostra_lado_dominante():
    s = {"shots_on_target_c": 6, "shots_on_target_f": 2,
         "home": "A", "away": "B"}
    b = _bullet_shots_on_target(s)
    assert b is not None
    assert "6-2" in b
    assert "A" in b


def test_sot_bullet_vazio_sem_dados():
    assert _bullet_shots_on_target({"shots_on_target_c": None,
                                     "shots_on_target_f": None}) is None


# ─── xG vs golos ──────────────────────────────────────────────────

def test_xg_acima_dos_golos_indica_producao_sem_conversao():
    s = {"xg_casa": 1.5, "xg_fora": 1.2, "marcador": "1-0"}
    b = _bullet_xg_vs_golos(s)
    assert b is not None
    assert "produzir mas nao a converter" in b


def test_xg_abaixo_dos_golos_indica_sorte():
    s = {"xg_casa": 0.3, "xg_fora": 0.2, "marcador": "2-0"}
    b = _bullet_xg_vs_golos(s)
    assert b is not None
    assert "sorte" in b


def test_xg_equilibrado_devolve_none():
    s = {"xg_casa": 1.0, "xg_fora": 0.9, "marcador": "1-1"}
    assert _bullet_xg_vs_golos(s) is None


# ─── Cantos assimetricos ───────────────────────────────────────────

def test_cantos_assimetria_detecta_pressao():
    s = {"corners_c": 7, "corners_f": 2, "home": "A", "away": "B"}
    b = _bullet_cantos_pressao(s)
    assert b is not None
    assert "pressionar" in b
    assert "A" in b


def test_cantos_equilibrados_devolve_none():
    s = {"corners_c": 4, "corners_f": 3, "home": "A", "away": "B"}
    assert _bullet_cantos_pressao(s) is None


# ─── Cards acumulando ─────────────────────────────────────────────

def test_cards_jogo_quente_detectado():
    s = {"yellows_c": 3, "yellows_f": 2, "minuto": 60}
    # total 5 ao 60 → 7.5/90 → quente
    b = _bullet_cards_acumulando(s)
    assert b is not None
    assert "quente" in b


def test_cards_baixos_devolve_none():
    s = {"yellows_c": 1, "yellows_f": 1, "minuto": 60}
    assert _bullet_cards_acumulando(s) is None


# ─── Riscos ───────────────────────────────────────────────────────

def test_risco_minutos_restantes_para_over():
    """Linha over 2.5 golos @ 80' com 1 golo → faltam 2 em 10min."""
    s = {
        "mercado": "golos", "lado": "over", "linha": 2.5,
        "minuto": 80, "marcador": "1-0",
    }
    r = _risco_minutos_restantes(s)
    assert r is not None
    assert "exigente" in r


def test_risco_minutos_restantes_nao_dispara_para_under():
    s = {
        "mercado": "golos", "lado": "under", "linha": 2.5,
        "minuto": 80, "marcador": "0-0",
    }
    assert _risco_minutos_restantes(s) is None


def test_risco_cards_proximo_de_expulsao():
    s = {"yellows_c": 4, "yellows_f": 1, "home": "Porto", "away": "Benfica"}
    r = _risco_cards_proximo_expulsao(s)
    assert r is not None
    assert "Porto" in r
    assert "2o amarelo" in r


def test_risco_cards_poucos_devolve_none():
    s = {"yellows_c": 1, "yellows_f": 1, "home": "A", "away": "B"}
    assert _risco_cards_proximo_expulsao(s) is None


# ─── Composition ──────────────────────────────────────────────────

def test_construir_analise_combina_varios_bullets():
    s = {
        "mercado": "cantos", "linha": 9.5, "minuto": 60,
        "corners_c": 5, "corners_f": 2,
        "shots_on_target_c": 7, "shots_on_target_f": 2,
        "home": "Porto", "away": "Benfica",
        "marcador": "1-0",
    }
    bullets = construir_analise(s)
    assert len(bullets) >= 2
    # Verifica que ha dedup leve: nao repete pressao se ja mencionada


def test_construir_analise_vazio_sem_dados():
    assert construir_analise({"mercado": "golos"}) == []


def test_construir_riscos_vazio_sem_risco():
    s = {"mercado": "golos", "lado": "over", "linha": 2.5, "minuto": 30,
         "marcador": "0-0", "yellows_c": 0, "yellows_f": 1}
    riscos = construir_riscos(s)
    assert riscos == []


def test_seccao_analise_telegram_formato():
    s = {
        "mercado": "cantos", "linha": 9.5, "minuto": 60,
        "corners_c": 5, "corners_f": 2,
        "home": "A", "away": "B",
        "ev": 0.12, "prob_modelo": 0.65,
    }
    txt = seccao_analise_telegram(s)
    assert "🧠" in txt
    assert "Analise" in txt
    assert "edge 12%" in txt
    assert "modelo 65%" in txt


def test_seccao_risco_telegram_vazio_sem_risco():
    s = {"mercado": "golos", "lado": "over", "linha": 2.5, "minuto": 30,
         "marcador": "0-0"}
    assert seccao_risco_telegram(s) == ""


def test_dedupe_exclui_frase_ja_mencionada():
    """Se 'pressionar' ja esta na seccao treinador, nao repete no bullet."""
    s = {
        "corners_c": 7, "corners_f": 2, "home": "A", "away": "B",
    }
    # Sem exclusao, bullet de pressao aparece
    bullets = construir_analise(s)
    assert any("pressionar" in b.lower() for b in bullets)
    # Com exclusao, nao aparece
    bullets2 = construir_analise(s, excluir_frases={"pressionar"})
    assert not any("pressionar" in b.lower() for b in bullets2)
