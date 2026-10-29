"""Testes do filtro modo exigente do scanner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Importa o helper directamente do scanner
from scanner_live_v2 import _passa_modo_exigente  # noqa: E402


def test_sem_ev_minimo_rejeita():
    s = {
        "ev": 0.05,
        "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
    }
    assert _passa_modo_exigente(s) is False


def test_sem_ids_rejeita():
    s = {"ev": 0.15, "marcador": "1-0", "minuto": 60}
    assert _passa_modo_exigente(s) is False


def test_sem_confronto_rejeita(monkeypatch):
    """Se confronto devolve None (fichas fracas), rejeita."""
    from apostas.alertas import confronto as _confronto
    monkeypatch.setattr(_confronto, "analisar_confronto",
                         lambda *a, **k: None)
    s = {
        "ev": 0.15, "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
    }
    assert _passa_modo_exigente(s) is False


def test_fichas_fracas_rejeita(monkeypatch):
    """Fichas <5 jogos → rejeita."""
    from apostas.alertas import confronto as _confronto

    class _Ficha:
        jogos_lidos = 3
        nome = "X"
        baseline = None
        por_resultado = None

    class _Res:
        ficha_casa = _Ficha()
        ficha_fora = _Ficha()

    monkeypatch.setattr(_confronto, "analisar_confronto",
                         lambda *a, **k: _Res())
    s = {
        "ev": 0.15, "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
    }
    assert _passa_modo_exigente(s) is False


def test_sem_mega_rejeita(monkeypatch):
    """Mega-cerebro sem sintese → rejeita."""
    from apostas.alertas import confronto as _confronto
    from apostas.alertas import mega_cerebro as _mega

    class _Ficha:
        jogos_lidos = 10
        nome = "X"
        baseline = None
        por_resultado = None

    class _Res:
        ficha_casa = _Ficha()
        ficha_fora = _Ficha()

    monkeypatch.setattr(_confronto, "analisar_confronto",
                         lambda *a, **k: _Res())
    monkeypatch.setattr(_mega, "sintetizar_a_partir_confronto",
                         lambda **k: None)
    s = {
        "ev": 0.15, "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
    }
    assert _passa_modo_exigente(s) is False


def test_mega_confianca_baixa_rejeita(monkeypatch):
    """Mega com confianca=1 (<2) → rejeita."""
    from apostas.alertas import confronto as _confronto
    from apostas.alertas import mega_cerebro as _mega

    class _Ficha:
        jogos_lidos = 10
        nome = "X"
        baseline = None
        por_resultado = None

    class _Res:
        ficha_casa = _Ficha()
        ficha_fora = _Ficha()

    class _Sint:
        confianca = 1

    monkeypatch.setattr(_confronto, "analisar_confronto",
                         lambda *a, **k: _Res())
    monkeypatch.setattr(_mega, "sintetizar_a_partir_confronto",
                         lambda **k: _Sint())
    s = {
        "ev": 0.15, "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
    }
    assert _passa_modo_exigente(s) is False


def test_tudo_passa_quando_filtros_batem(monkeypatch):
    """EV>=0.10 + fichas boas + mega confianca>=2 → passa."""
    from apostas.alertas import confronto as _confronto
    from apostas.alertas import mega_cerebro as _mega

    class _Ficha:
        jogos_lidos = 12
        nome = "X"
        baseline = None
        por_resultado = None

    class _Res:
        ficha_casa = _Ficha()
        ficha_fora = _Ficha()

    class _Sint:
        confianca = 3

    monkeypatch.setattr(_confronto, "analisar_confronto",
                         lambda *a, **k: _Res())
    monkeypatch.setattr(_mega, "sintetizar_a_partir_confronto",
                         lambda **k: _Sint())
    s = {
        "ev": 0.15, "home_id": 1, "away_id": 2,
        "marcador": "1-0", "minuto": 60, "liga": "L",
        "shots_on_target_c": 5, "shots_on_target_f": 3,
    }
    assert _passa_modo_exigente(s) is True
