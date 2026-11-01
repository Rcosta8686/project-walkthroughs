"""Testes de fixtures_fallback — Odds API events → shape Sofascore."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from apostas.ingestao import fixtures_fallback as ff


# ─── Helpers ──────────────────────────────────────────────────────────────

def _ev(home: str, away: str, iso_ts: str, ev_id: str = "e1") -> dict:
    return {
        "id": ev_id,
        "sport_key": "soccer_italy_serie_a",
        "home_team": home,
        "away_team": away,
        "commence_time": iso_ts,
    }


@pytest.fixture(autouse=True)
def _limpa_cache():
    """Entre testes, resetamos a cache de sofa_id para forçar re-load."""
    ff._CACHE_NOME_SOFA_ID.clear()
    yield
    ff._CACHE_NOME_SOFA_ID.clear()


@pytest.fixture
def _cache_pop(monkeypatch):
    """Popula a cache manualmente para evitar hit à BD nos testes."""
    def pop(mapa: dict[str, int]):
        for n, i in mapa.items():
            ff._CACHE_NOME_SOFA_ID[ff._normaliza(n)] = i
        # Impede re-load da BD
        monkeypatch.setattr(ff, "_carregar_cache_sofa_id", lambda: None)
    return pop


# ─── _parse_commence ──────────────────────────────────────────────────────

def test_parse_commence_z_terminator():
    dt = ff._parse_commence("2026-11-01T15:00:00Z")
    assert dt == datetime(2026, 11, 1, 15, 0, 0, tzinfo=timezone.utc)


def test_parse_commence_plus_zero():
    dt = ff._parse_commence("2026-11-01T15:00:00+00:00")
    assert dt == datetime(2026, 11, 1, 15, 0, 0, tzinfo=timezone.utc)


def test_parse_commence_invalido():
    assert ff._parse_commence("not-a-date") is None
    assert ff._parse_commence("") is None


# ─── _hash_event_id ───────────────────────────────────────────────────────

def test_hash_event_id_determinista():
    a = ff._hash_event_id("evento-xpto-123")
    b = ff._hash_event_id("evento-xpto-123")
    assert a == b
    assert isinstance(a, int)
    assert a > 0


def test_hash_event_id_diferentes():
    assert ff._hash_event_id("a") != ff._hash_event_id("b")


# ─── _canonical_name ──────────────────────────────────────────────────────

def test_canonical_name_odds_api_alias():
    # "FC Porto" é alias de "Porto" em _ALIAS_ODDS_API
    assert ff._canonical_name("FC Porto") == "Porto"


def test_canonical_name_sem_alias_passa():
    assert ff._canonical_name("Equipa Qualquer") == "Equipa Qualquer"


# ─── _sofa_id_por_nome ────────────────────────────────────────────────────

def test_sofa_id_nome_exacto(_cache_pop):
    _cache_pop({"Benfica": 999})
    assert ff._sofa_id_por_nome("Benfica") == 999


def test_sofa_id_case_insensitive(_cache_pop):
    _cache_pop({"Benfica": 999})
    assert ff._sofa_id_por_nome("benfica") == 999
    assert ff._sofa_id_por_nome("BENFICA") == 999


def test_sofa_id_sem_sufixo_fc(_cache_pop):
    _cache_pop({"Porto": 777})
    # Odds API devolve "FC Porto", BD tem "Porto"
    assert ff._sofa_id_por_nome("FC Porto") == 777


def test_sofa_id_prefix_match(_cache_pop):
    _cache_pop({"Manchester City": 123})
    # Query "Man City" não tem match directo, mas "Manchester City" contém "City"
    # — na nossa heuristica, substring de 5+ chars suficiente
    assert ff._sofa_id_por_nome("manchester") == 123


def test_sofa_id_nao_encontra(_cache_pop):
    _cache_pop({"Benfica": 999})
    assert ff._sofa_id_por_nome("Equipa Inexistente") is None


# ─── listar_jogos_do_dia ──────────────────────────────────────────────────

def test_listar_data_invalida_retorna_vazio():
    assert ff.listar_jogos_do_dia("not-a-date") == []


def test_listar_sem_api_key(monkeypatch, _cache_pop):
    _cache_pop({})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: None)
    assert ff.listar_jogos_do_dia("2026-11-01") == []


def test_listar_filtra_fora_do_dia(monkeypatch, _cache_pop):
    _cache_pop({"Porto": 1, "Benfica": 2})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")
    # 2 eventos: 1 dentro do dia, 1 fora
    def fake_list(sport, api_key, **kw):
        if sport != "soccer_portugal_primeira_liga":
            return []
        return [
            _ev("FC Porto", "SL Benfica", "2026-11-01T19:00:00Z", "a"),
            _ev("FC Porto", "SL Benfica", "2026-11-02T19:00:00Z", "b"),  # fora
        ]
    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)

    out = ff.listar_jogos_do_dia("2026-11-01")
    assert len(out) == 1
    assert out[0]["homeTeam"]["name"] == "FC Porto"
    assert out[0]["awayTeam"]["name"] == "SL Benfica"
    assert out[0]["homeTeam"]["id"] == 1
    assert out[0]["awayTeam"]["id"] == 2
    assert out[0]["tournament"]["name"] == "Liga Portugal"


def test_listar_shape_sofascore_compativel(monkeypatch, _cache_pop):
    """Shape devolvido tem de bater com o que preview._construir_linha espera."""
    _cache_pop({"Benfica": 10, "Porto": 20})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")

    def fake_list(sport, api_key, **kw):
        if sport != "soccer_portugal_primeira_liga":
            return []
        return [_ev("Benfica", "FC Porto", "2026-11-01T20:30:00Z", "x")]

    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)
    out = ff.listar_jogos_do_dia("2026-11-01")
    assert len(out) == 1
    ev = out[0]
    # Chaves obrigatorias
    assert "id" in ev
    assert "startTimestamp" in ev
    assert "homeTeam" in ev and {"id", "name"} <= set(ev["homeTeam"].keys())
    assert "awayTeam" in ev and {"id", "name"} <= set(ev["awayTeam"].keys())
    assert "tournament" in ev and "name" in ev["tournament"]
    # Tipos
    assert isinstance(ev["id"], int)
    assert isinstance(ev["startTimestamp"], int)
    # startTimestamp em unix UTC
    expected_ts = int(datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc).timestamp())
    assert ev["startTimestamp"] == expected_ts


def test_listar_skip_sem_sofa_id(monkeypatch, _cache_pop):
    """Se nao conseguir mapear nome → sofa_id, salta o jogo (nao polui preview)."""
    _cache_pop({"Benfica": 10})  # Porto não existe na cache
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")

    def fake_list(sport, api_key, **kw):
        if sport != "soccer_portugal_primeira_liga":
            return []
        return [_ev("Equipa Desconhecida XPTO", "Benfica", "2026-11-01T19:00:00Z")]

    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)
    out = ff.listar_jogos_do_dia("2026-11-01")
    assert out == []


def test_listar_dedupe_mesmo_jogo(monkeypatch, _cache_pop):
    """Se o mesmo event_id vem em 2 sports, só uma entrada sai."""
    _cache_pop({"Porto": 1, "Benfica": 2})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")

    def fake_list(sport, api_key, **kw):
        # Devolve o mesmo evento em ambos (improvavel mas defensivo)
        return [_ev("FC Porto", "SL Benfica", "2026-11-01T19:00:00Z", "evento-x")]

    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)
    out = ff.listar_jogos_do_dia("2026-11-01")
    assert len(out) == 1


def test_listar_erro_por_sport_isola(monkeypatch, _cache_pop):
    """Se um sport falhar HTTP, os outros continuam."""
    _cache_pop({"Benfica": 2, "Porto": 1})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")

    def fake_list(sport, api_key, **kw):
        if sport == "soccer_portugal_primeira_liga":
            return [_ev("FC Porto", "SL Benfica", "2026-11-01T19:00:00Z", "ok")]
        raise RuntimeError(f"HTTP 500 em {sport}")

    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)
    out = ff.listar_jogos_do_dia("2026-11-01")
    assert len(out) == 1


def test_listar_filtro_por_liga(monkeypatch, _cache_pop):
    """ligas_filtro restringe quais sports são consultados."""
    _cache_pop({"Benfica": 2, "Porto": 1})
    monkeypatch.setattr(ff, "get_env", lambda *a, **k: "fake_key")

    sports_chamados = []

    def fake_list(sport, api_key, **kw):
        sports_chamados.append(sport)
        return []

    monkeypatch.setattr(ff, "_listar_eventos_sport", fake_list)
    ff.listar_jogos_do_dia("2026-11-01", ligas_filtro=("Liga Portugal",))
    assert sports_chamados == ["soccer_portugal_primeira_liga"]


# ─── listar_com_fallback ──────────────────────────────────────────────────

def test_fallback_usa_sofascore_quando_devolve(monkeypatch):
    """Se Sofascore devolve >0 jogos, Odds API nao é chamada."""
    import apostas.ingestao.sofascore_live as sl
    jogos_sofa = [{
        "id": 1, "startTimestamp": 1700000000,
        "homeTeam": {"id": 100, "name": "A"},
        "awayTeam": {"id": 200, "name": "B"},
        "tournament": {"name": "X"},
    }]
    monkeypatch.setattr(sl, "listar_jogos_do_dia", lambda *a, **kw: jogos_sofa)

    nao_chamar = []

    def nao_devia_ser_chamada(*a, **kw):
        nao_chamar.append(1)
        return []

    monkeypatch.setattr(ff, "listar_jogos_do_dia", nao_devia_ser_chamada)
    out = ff.listar_com_fallback("2026-11-01")
    assert out == jogos_sofa
    assert nao_chamar == []


def test_fallback_usa_odds_api_quando_sofascore_vazio(monkeypatch):
    """Se Sofascore devolve 0 jogos, cai no fallback."""
    import apostas.ingestao.sofascore_live as sl
    monkeypatch.setattr(sl, "listar_jogos_do_dia", lambda *a, **kw: [])

    jogos_oa = [{
        "id": 7, "startTimestamp": 1700000000,
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "tournament": {"name": "Liga Portugal"},
    }]
    monkeypatch.setattr(ff, "listar_jogos_do_dia", lambda *a, **kw: jogos_oa)

    out = ff.listar_com_fallback("2026-11-01")
    assert out == jogos_oa


def test_fallback_usa_odds_api_quando_sofascore_lanca(monkeypatch):
    """Se Sofascore levanta excepção, cai no fallback."""
    import apostas.ingestao.sofascore_live as sl

    def boom(*a, **kw):
        raise RuntimeError("Cloudflare 403")

    monkeypatch.setattr(sl, "listar_jogos_do_dia", boom)
    monkeypatch.setattr(ff, "listar_jogos_do_dia", lambda *a, **kw: [{"id": 42}])
    out = ff.listar_com_fallback("2026-11-01")
    assert out == [{"id": 42}]


def test_fallback_tudo_falha_devolve_vazio(monkeypatch):
    import apostas.ingestao.sofascore_live as sl
    monkeypatch.setattr(sl, "listar_jogos_do_dia", lambda *a, **kw: [])

    def boom(*a, **kw):
        raise RuntimeError("tambem falhou")

    monkeypatch.setattr(ff, "listar_jogos_do_dia", boom)
    out = ff.listar_com_fallback("2026-11-01")
    assert out == []
