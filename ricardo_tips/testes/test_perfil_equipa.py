"""Testes de perfil_equipa (sem rede — payloads mock)."""
from __future__ import annotations

from datetime import datetime

import pytest

from apostas.modelos.perfil_equipa import _agregar_jogo, _mean, _stat_valor


# ─── _stat_valor ───

def _stats_sofascore_fake(home_shots: str = "15", away_shots: str = "9",
                            home_corners: str = "6", away_corners: str = "3"):
    """Payload mock conforme formato /event/{id}/statistics do Sofascore."""
    return {
        "statistics": [{
            "period": "ALL",
            "groups": [
                {
                    "groupName": "Shots",
                    "statisticsItems": [
                        {"name": "Total shots", "home": home_shots, "away": away_shots},
                        {"name": "Shots on target", "home": "5", "away": "3"},
                    ],
                },
                {
                    "groupName": "TV data",
                    "statisticsItems": [
                        {"name": "Corner kicks", "home": home_corners, "away": away_corners},
                    ],
                },
            ],
        }],
    }


def test_stat_valor_extrai_remates_home():
    s = _stats_sofascore_fake(home_shots="18")
    assert _stat_valor(s, "shots", "total shots", "home") == 18.0


def test_stat_valor_extrai_remates_away():
    s = _stats_sofascore_fake(away_shots="12")
    assert _stat_valor(s, "shots", "total shots", "away") == 12.0


def test_stat_valor_cantos():
    s = _stats_sofascore_fake(home_corners="8")
    assert _stat_valor(s, "tv", "corner kicks", "home") == 8.0


def test_stat_valor_devolve_none_se_grupo_falta():
    s = _stats_sofascore_fake()
    assert _stat_valor(s, "goalkeeping", "saves", "home") is None


def test_stat_valor_so_processa_period_all():
    """Deve ignorar outros periodos (ex 1ST, 2ND)."""
    payload = {
        "statistics": [
            {"period": "1ST", "groups": [{"groupName": "Shots",
                "statisticsItems": [{"name": "Total shots", "home": "5", "away": "3"}]}]},
            {"period": "ALL", "groups": [{"groupName": "Shots",
                "statisticsItems": [{"name": "Total shots", "home": "10", "away": "7"}]}]},
        ],
    }
    assert _stat_valor(payload, "shots", "total shots", "home") == 10.0


def test_stat_valor_formato_com_percentagem():
    s = {"statistics": [{"period": "ALL", "groups": [{"groupName": "Possession",
        "statisticsItems": [{"name": "Ball possession", "home": "58%", "away": "42%"}]}]}]}
    assert _stat_valor(s, "possession", "ball possession", "home") == 58.0


def test_stat_valor_formato_com_parenteses():
    """Sofascore às vezes usa '12 (5)' para total shots (on target)."""
    s = {"statistics": [{"period": "ALL", "groups": [{"groupName": "Shots",
        "statisticsItems": [{"name": "Total shots", "home": "12 (5)", "away": "8 (3)"}]}]}]}
    assert _stat_valor(s, "shots", "total shots", "home") == 12.0


# ─── _agregar_jogo ───

def _event_mock(team_id, era_casa, hs, aws, status_code=100):
    other_id = 999 if era_casa else team_id - 1
    return {
        "id": 12345,
        "status": {"code": status_code},
        "homeTeam": {"id": team_id if era_casa else other_id},
        "awayTeam": {"id": other_id if era_casa else team_id},
        "homeScore": {"current": hs},
        "awayScore": {"current": aws},
        "startTimestamp": int(datetime(2026, 10, 10, 20, 0).timestamp()),
    }


def test_agregar_jogo_vitoria_em_casa():
    ag = _agregar_jogo(100, _event_mock(100, era_casa=True, hs=3, aws=1))
    assert ag.era_casa is True
    assert ag.golos_marcados == 3
    assert ag.golos_sofridos == 1
    assert ag.ganhou is True
    assert ag.perdeu is False


def test_agregar_jogo_derrota_fora():
    ag = _agregar_jogo(100, _event_mock(100, era_casa=False, hs=2, aws=0))
    assert ag.era_casa is False
    assert ag.golos_marcados == 0  # era fora, hs eram os golos da casa adversaria
    assert ag.golos_sofridos == 2
    assert ag.perdeu is True


def test_agregar_jogo_ignora_nao_terminados():
    assert _agregar_jogo(100, _event_mock(100, True, 1, 0, status_code=0)) is None  # not started
    assert _agregar_jogo(100, _event_mock(100, True, 1, 0, status_code=7)) is None  # live


# ─── _mean ───

def test_mean_ignora_nones():
    assert _mean([1, 2, None, 3]) == pytest.approx(2.0)


def test_mean_vazio_devolve_none():
    assert _mean([]) is None
    assert _mean([None, None]) is None
