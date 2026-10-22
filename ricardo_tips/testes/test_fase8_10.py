"""Testes Fase 8 (estadios) + Fase 10 (ausencias)."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from apostas.alertas import ausencias, cerebro_live
from apostas.modelos import estadios
from apostas.utils import config as config_mod, db as db_mod
from apostas.utils.schema import EstatisticaEquipaJogo
from apostas.utils.tempo import agora_utc


# ─── Fase 8: estadios ────────────────────────────────────────────

@pytest.fixture
def bd_com_venue(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()

    hoje = agora_utc()
    with db_mod.abrir_sessao() as s:
        # Estadio da Luz: 10 jogos distintos (2 linhas cada = 20 linhas)
        for i in range(10):
            # Casa
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=100, sofascore_event_id=1000 + i,
                nome_equipa="Benfica", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i * 3),
                era_casa=1,
                golos_marcados=2, golos_sofridos=1, resultado="W",
                cantos=6.0, cards_amarelos=2.5,
                venue="Estádio da Luz",
            ))
            # Fora (visitante no mesmo venue)
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=200 + i, sofascore_event_id=1000 + i,
                nome_equipa=f"Visitante {i}", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i * 3),
                era_casa=0,
                golos_marcados=1, golos_sofridos=2, resultado="L",
                cantos=4.0, cards_amarelos=2.0,
                venue="Estádio da Luz",
            ))
        # Jogos com venue=None (nao devem contar)
        s.add(EstatisticaEquipaJogo(
            sofascore_team_id=999, sofascore_event_id=9999,
            nome_equipa="X", liga="Liga Portugal",
            data_jogo=hoje, era_casa=1,
            golos_marcados=0, golos_sofridos=0, resultado="D",
            venue=None,
        ))
    yield tmp_path
    db_mod.reset_engine()


def test_perfil_estadio_devolve_media_golos(bd_com_venue):
    p = estadios.perfil_estadio("Estádio da Luz", min_jogos=5)
    assert p is not None
    assert p.n_jogos == 10
    # Cada jogo: 2 golos (casa) + 1 (fora) = 3
    assert p.golos_marcados_media == pytest.approx(3.0, abs=0.01)


def test_perfil_estadio_min_jogos_filtra(bd_com_venue):
    """Venue com <min_jogos linhas devolve None."""
    with db_mod.abrir_sessao() as s:
        s.add(EstatisticaEquipaJogo(
            sofascore_team_id=1, sofascore_event_id=5555,
            nome_equipa="X", liga="L",
            data_jogo=agora_utc(), era_casa=1,
            golos_marcados=1, golos_sofridos=0, resultado="W",
            venue="Pequeno Estádio",
        ))
    assert estadios.perfil_estadio("Pequeno Estádio", min_jogos=5) is None


def test_perfil_estadio_venue_inexistente(bd_com_venue):
    assert estadios.perfil_estadio("Estadio Fantasma") is None


def test_perfil_estadio_query_vazia_devolve_none(bd_com_venue):
    assert estadios.perfil_estadio("") is None
    assert estadios.perfil_estadio("   ") is None


def test_comparar_com_liga_devolve_delta(bd_com_venue):
    # Adiciona varios jogos noutros estadios da mesma liga para ter media
    hoje = agora_utc()
    with db_mod.abrir_sessao() as s:
        for i in range(20):
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=500 + i, sofascore_event_id=5000 + i,
                nome_equipa=f"Clube {i}", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i),
                era_casa=1,
                golos_marcados=1, golos_sofridos=1, resultado="D",
                venue=f"Outro {i}",
            ))
            s.add(EstatisticaEquipaJogo(
                sofascore_team_id=600 + i, sofascore_event_id=5000 + i,
                nome_equipa=f"Visita {i}", liga="Liga Portugal",
                data_jogo=hoje - timedelta(days=i),
                era_casa=0,
                golos_marcados=1, golos_sofridos=1, resultado="D",
                venue=f"Outro {i}",
            ))
    cmp = estadios.comparar_com_liga("Estádio da Luz", "Liga Portugal", min_jogos=5)
    assert cmp is not None
    # Luz: 10 jogos × 3 golos = 30. Liga: 10×3 + 20×2 = 70 em 30 jogos
    # → media liga = 2.33. Delta = 3/2.33 - 1 = +28.6%.
    assert cmp["delta_golos_pct"] == pytest.approx(28.6, abs=1.0)


# ─── Fase 10: ausencias ──────────────────────────────────────────

def test_extrair_lista_vazia():
    assert ausencias._extrair_lista({}) == []
    assert ausencias._extrair_lista({"missingPlayers": None}) == []


def test_extrair_lista_dois_jogadores():
    team = {"missingPlayers": [
        {"player": {"name": "Ronaldo"}, "type": "missing", "reason": "injury"},
        {"player": {"name": "Pepe"}, "type": "doubtful", "reason": "suspension"},
        {"player": {"name": ""}},  # sem nome, ignora
    ]}
    r = ausencias._extrair_lista(team)
    assert len(r) == 2
    assert r[0].nome == "Ronaldo"
    assert r[0].tipo == "missing"
    assert r[0].razao == "injury"
    assert r[1].nome == "Pepe"
    assert r[1].tipo == "doubtful"


def test_obter_ausencias_payload_vazio():
    with patch("apostas.ingestao.sofascore_live._get_json", return_value=None):
        c, f = ausencias.obter_ausencias(123)
        assert c == [] and f == []


def test_obter_ausencias_payload_valido():
    payload = {
        "homeTeam": {"missingPlayers": [
            {"player": {"name": "A"}, "type": "missing", "reason": "injury"},
        ]},
        "awayTeam": {"missingPlayers": [
            {"player": {"name": "B"}, "type": "doubtful", "reason": "other"},
            {"player": {"name": "C"}, "type": "missing", "reason": "suspension"},
        ]},
    }
    with patch("apostas.ingestao.sofascore_live._get_json", return_value=payload):
        c, f = ausencias.obter_ausencias(1)
    assert len(c) == 1
    assert c[0].nome == "A"
    assert len(f) == 2


def test_resumo_ausencias_agrega():
    missing = [
        ausencias.Ausencia("A", "missing", "injury"),
        ausencias.Ausencia("B", "missing", "suspension"),
        ausencias.Ausencia("C", "doubtful", "national duty"),
    ]
    r = ausencias.resumo_ausencias(missing, [])
    assert r["casa"]["n"] == 3
    assert r["casa"]["n_lesoes"] == 1
    assert r["casa"]["n_suspensoes"] == 1
    assert r["casa"]["n_nacional"] == 1
    assert r["casa"]["n_duvidosos"] == 1


# ─── Integracao cerebro + ausencias ──────────────────────────────

def test_cerebro_aplica_mult_quando_tres_ausencias_casa():
    cerebro_live.limpar_cache()

    class _Res:
        nome_casa = "A"; nome_fora = "B"
        padroes_cruzados = []; alertas = []

    missing_casa = [
        {"nome": "X", "tipo": "missing", "razao": "injury"},
        {"nome": "Y", "tipo": "missing", "razao": "suspension"},
        {"nome": "Z", "tipo": "missing", "razao": "injury"},
    ]
    with patch("apostas.alertas.confronto.analisar_confronto", return_value=_Res()):
        aj = cerebro_live.calcular_ajustes(1, 2, missing_casa=missing_casa)
    assert aj.lam_shots_casa_mult == pytest.approx(0.95)
    assert any("ausencias" in n for n in aj.notas)


def test_cerebro_aplica_mult_golos_quando_cinco_ausencias():
    cerebro_live.limpar_cache()

    class _Res:
        nome_casa = "A"; nome_fora = "B"
        padroes_cruzados = []; alertas = []

    missing_fora = [
        {"nome": f"P{i}", "tipo": "missing", "razao": "injury"}
        for i in range(5)
    ]
    with patch("apostas.alertas.confronto.analisar_confronto", return_value=_Res()):
        aj = cerebro_live.calcular_ajustes(1, 2, missing_fora=missing_fora)
    assert aj.lam_shots_fora_mult == pytest.approx(0.95)
    assert aj.lam_golos_mult == pytest.approx(0.95)


def test_cerebro_ignora_doubtful_para_contagem():
    cerebro_live.limpar_cache()

    class _Res:
        nome_casa = "A"; nome_fora = "B"
        padroes_cruzados = []; alertas = []

    missing = [
        {"nome": "A", "tipo": "doubtful", "razao": "injury"},
        {"nome": "B", "tipo": "doubtful", "razao": "injury"},
        {"nome": "C", "tipo": "doubtful", "razao": "injury"},
    ]
    with patch("apostas.alertas.confronto.analisar_confronto", return_value=_Res()):
        aj = cerebro_live.calcular_ajustes(1, 2, missing_casa=missing)
    assert aj.lam_shots_casa_mult == pytest.approx(1.0)
