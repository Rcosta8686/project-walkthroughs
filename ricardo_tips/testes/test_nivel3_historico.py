"""Testes Nivel 3: arbitro + stats avancadas no historico persistente."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select

from apostas.modelos.historico_equipas import _extrair_stats_equipa
from apostas.utils import config as config_mod, db as db_mod
from apostas.utils.schema import EstatisticaEquipaJogo
from apostas.utils.tempo import agora_utc


# ─── Schema: colunas novas existem ─────────────────────────────

@pytest.fixture
def bd_vazia(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    yield tmp_path
    db_mod.reset_engine()


def test_schema_tem_colunas_nivel3(bd_vazia):
    """Schema inicializado tem colunas arbitro + stats avancadas."""
    e = EstatisticaEquipaJogo(
        sofascore_team_id=1, sofascore_event_id=1,
        nome_equipa="X", data_jogo=agora_utc(),
        era_casa=1, golos_marcados=0, golos_sofridos=0, resultado="D",
        arbitro="Pierluigi Collina",
        passes_longos_total=50, passes_longos_pct=0.65,
        duels_won_pct=0.53, interceptions=18, tackles_total=22,
        offsides=3, pass_accuracy_final_third=0.72,
    )
    with db_mod.abrir_sessao() as s:
        s.add(e)
    with db_mod.abrir_sessao() as s:
        row = s.scalar(select(EstatisticaEquipaJogo).where(
            EstatisticaEquipaJogo.sofascore_event_id == 1
        ))
        assert row.arbitro == "Pierluigi Collina"
        assert row.passes_longos_total == 50
        assert row.duels_won_pct == pytest.approx(0.53)
        assert row.offsides == 3


# ─── _extrair_stats_equipa: campos novos ───────────────────────

def test_extrair_stats_captura_passes_longos():
    """Payload com group 'ALL' + item 'Long balls' → stats.passes_longos_total."""
    payload = {
        "statistics": [
            {
                "period": "ALL",
                "groups": [
                    {
                        "groupName": "Attack",
                        "statisticsItems": [
                            {"name": "Long balls", "home": "40", "away": "35"},
                            {"name": "Interceptions", "home": "12", "away": "18"},
                        ],
                    }
                ],
            }
        ]
    }
    stats_h = _extrair_stats_equipa(payload, "home")
    assert stats_h.passes_longos_total == 40
    assert stats_h.interceptions == 12
    stats_a = _extrair_stats_equipa(payload, "away")
    assert stats_a.passes_longos_total == 35
    assert stats_a.interceptions == 18


def test_extrair_stats_captura_offsides_e_tackles():
    payload = {
        "statistics": [
            {
                "period": "ALL",
                "groups": [
                    {
                        "groupName": "Defending",
                        "statisticsItems": [
                            {"name": "Tackles", "home": "22", "away": "19"},
                            {"name": "Offsides", "home": "3", "away": "5"},
                        ],
                    }
                ],
            }
        ]
    }
    stats = _extrair_stats_equipa(payload, "home")
    assert stats.tackles_total == 22
    assert stats.offsides == 3


def test_extrair_stats_todas_novas_none_quando_payload_vazio():
    stats = _extrair_stats_equipa({}, "home")
    assert stats.passes_longos_total is None
    assert stats.duels_won_pct is None
    assert stats.interceptions is None
    assert stats.tackles_total is None
    assert stats.offsides is None


# ─── gravar_jogo_terminado: arbitro vai para o DB ──────────────

def test_gravar_jogo_captura_arbitro(bd_vazia):
    """Mock /event e /event/statistics → arbitro deve ir para a linha."""
    from apostas.modelos import historico_equipas

    event_payload = {
        "event": {
            "status": {"code": 100, "description": "Full time"},
            "homeTeam": {"id": 100, "name": "A"},
            "awayTeam": {"id": 200, "name": "B"},
            "homeScore": {"current": 2, "period1": 1},
            "awayScore": {"current": 1, "period1": 0},
            "startTimestamp": int(datetime(2026, 10, 1, 20, 0).timestamp()),
            "tournament": {"name": "L"},
            "venue": {"name": "Estádio Nivel 3"},
            "referee": {"name": "Howard Webb"},
        }
    }
    stats_payload = {"statistics": []}  # sem stats avancadas aqui

    def _fake_get(path):
        if path.endswith("/statistics"):
            return stats_payload
        return event_payload

    with patch(
        "apostas.modelos.historico_equipas._get_json",
        side_effect=_fake_get,
    ):
        n = historico_equipas.gravar_jogo_terminado(123)
    assert n == 2
    with db_mod.abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo).where(
                EstatisticaEquipaJogo.sofascore_event_id == 123
            )
        ).all()
        assert len(rows) == 2
        for r in rows:
            assert r.arbitro == "Howard Webb"
            assert r.venue == "Estádio Nivel 3"


def test_gravar_jogo_sem_referee_fica_none(bd_vazia):
    from apostas.modelos import historico_equipas

    event_payload = {
        "event": {
            "status": {"code": 100},
            "homeTeam": {"id": 100, "name": "A"},
            "awayTeam": {"id": 200, "name": "B"},
            "homeScore": {"current": 1},
            "awayScore": {"current": 1},
            "startTimestamp": int(datetime(2026, 10, 1, 20, 0).timestamp()),
            "tournament": {"name": "L"},
            # Sem referee
        }
    }
    with patch(
        "apostas.modelos.historico_equipas._get_json",
        return_value=event_payload,
    ):
        n = historico_equipas.gravar_jogo_terminado(456)
    assert n == 2
    with db_mod.abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo).where(
                EstatisticaEquipaJogo.sofascore_event_id == 456
            )
        ).all()
        for r in rows:
            assert r.arbitro is None
