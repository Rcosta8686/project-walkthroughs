"""Testa a integracao das vozes do treinador na mensagem Telegram live."""
from __future__ import annotations

import pytest

from apostas.alertas import telegram_live


class _Baseline:
    def __init__(self, remates=12.0, posse_pct=55, cards_amarelos=2.5):
        self.remates = remates
        self.posse_pct = posse_pct
        self.cards_amarelos = cards_amarelos


class _PorResultado:
    def __init__(self, remates_L=12.0):
        self.remates_L = remates_L


class _Ficha:
    def __init__(self, nome, jogos=10, posse=55):
        self.nome = nome
        self.jogos_lidos = jogos
        self.baseline = _Baseline(posse_pct=posse)
        self.por_resultado = _PorResultado()


class _Confronto:
    def __init__(self, fc, ff):
        self.ficha_casa = fc
        self.ficha_fora = ff


def test_sem_ids_devolve_vazio():
    assert telegram_live._linhas_treinador({"minuto": 60, "marcador": "1-0"}) == ""


def test_marcador_malformado_devolve_vazio():
    assert telegram_live._linhas_treinador({
        "home_id": 1, "away_id": 2, "minuto": 60, "marcador": "xpto",
    }) == ""


def test_integra_vozes_quando_ha_dados(monkeypatch):
    import apostas.alertas.analise_treinador as at

    def fake_confronto(h, a, *, minuto, score_casa, score_fora):
        return _Confronto(
            _Ficha("Alfa", posse=58),
            _Ficha("Beta", posse=48),
        )
    # Substitui a importacao lazy dentro de analise_treinador
    from apostas.alertas import confronto as confronto_mod
    monkeypatch.setattr(confronto_mod, "analisar_confronto", fake_confronto)

    txt = telegram_live._linhas_treinador({
        "home_id": 1, "away_id": 2, "minuto": 70, "marcador": "2-1",
    })
    assert "Treinador Alfa" in txt
    assert "Treinador Beta" in txt
    assert txt.startswith("\n")


def test_minuto_nao_numerico_devolve_vazio():
    assert telegram_live._linhas_treinador({
        "home_id": 1, "away_id": 2, "minuto": "HT", "marcador": "1-1",
    }) == ""
