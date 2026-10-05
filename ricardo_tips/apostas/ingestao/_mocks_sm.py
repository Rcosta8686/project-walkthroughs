"""Respostas sintéticas que imitam a Sportmonks Football API v3.

Mantém a estrutura ``{"data": ..., "pagination": ..., "subscription": ...}``
que a API real devolve, para que o código funcione sem alterações quando
trocamos ``MODO=desenvolvimento`` por ``MODO=producao``.

Docs reais: https://docs.sportmonks.com/football
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Dados sintéticos
# ─────────────────────────────────────────────────────────────────────

# IDs reais da Sportmonks para as 6 ligas do nosso plano.
# Cada liga tem 2 épocas (ano atual = 10xxx; ano anterior = 20xxx).
_LIGAS: dict[int, dict[str, Any]] = {
    8:   {"nome": "Premier League",  "pais": "England",   "pais_id": 462, "epoca_atual_id": 10001, "epoca_anterior_id": 20001},
    564: {"nome": "La Liga",         "pais": "Spain",     "pais_id": 32,  "epoca_atual_id": 10002, "epoca_anterior_id": 20002},
    384: {"nome": "Serie A",         "pais": "Italy",     "pais_id": 251, "epoca_atual_id": 10003, "epoca_anterior_id": 20003},
    82:  {"nome": "Bundesliga",      "pais": "Germany",   "pais_id": 11,  "epoca_atual_id": 10004, "epoca_anterior_id": 20004},
    301: {"nome": "Ligue 1",         "pais": "France",    "pais_id": 17,  "epoca_atual_id": 10005, "epoca_anterior_id": 20005},
    462: {"nome": "Liga Portugal",   "pais": "Portugal",  "pais_id": 20,  "epoca_atual_id": 10006, "epoca_anterior_id": 20006},
}

# Mapeamento inverso: season_id → liga_id (para o mock saber que liga atacar)
_EPOCA_PARA_LIGA = {}
for lid, info in _LIGAS.items():
    _EPOCA_PARA_LIGA[info["epoca_atual_id"]] = (lid, 2024)
    _EPOCA_PARA_LIGA[info["epoca_anterior_id"]] = (lid, 2023)

# IDs e nomes de equipas por época (correspondência 1:1 com liga)
_EQUIPAS_POR_EPOCA: dict[int, list[tuple[int, str]]] = {
    10001: [
        (1,  "Manchester United"),
        (2,  "Newcastle"),
        (3,  "Liverpool"),
        (4,  "Arsenal"),
        (5,  "Chelsea"),
        (6,  "Manchester City"),
    ],
    10002: [
        (101, "Barcelona"),
        (102, "Atletico Madrid"),
        (103, "Real Madrid"),
        (104, "Real Sociedad"),
        (105, "Real Betis"),
        (106, "Valencia"),
    ],
    10003: [
        (201, "AC Milan"),
        (202, "Juventus"),
        (203, "AS Roma"),
        (204, "Inter"),
        (205, "Napoli"),
        (206, "Lazio"),
    ],
    10004: [
        (301, "Bayern Munich"),
        (302, "Borussia Dortmund"),
        (303, "Bayer Leverkusen"),
        (304, "RB Leipzig"),
        (305, "Eintracht Frankfurt"),
        (306, "SC Freiburg"),
    ],
    10005: [
        (401, "Paris Saint Germain"),
        (402, "Monaco"),
        (403, "Marseille"),
        (404, "Lille"),
        (405, "Nice"),
        (406, "Lyon"),
    ],
    10006: [
        (501, "FC Porto"),
        (502, "Benfica"),
        (503, "Sporting CP"),
        (504, "SC Braga"),
        (505, "Vitoria SC"),
        (506, "Boavista"),
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Router de endpoints
# ─────────────────────────────────────────────────────────────────────

def responder(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Devolve a resposta sintética adequada para o endpoint pedido."""
    # /leagues/{id}
    m = re.fullmatch(r"/leagues/(\d+)", path)
    if m:
        return _league_por_id(int(m.group(1)), params)

    # /leagues
    if path == "/leagues":
        return _listar_leagues()

    # /teams/seasons/{season_id}
    m = re.fullmatch(r"/teams/seasons/(\d+)", path)
    if m:
        return _teams_da_epoca(int(m.group(1)))

    # /fixtures/seasons/{season_id}
    m = re.fullmatch(r"/fixtures/seasons/(\d+)", path)
    if m:
        return _fixtures_da_epoca(int(m.group(1)), params)

    # /fixtures/{id}
    m = re.fullmatch(r"/fixtures/(\d+)", path)
    if m:
        return _fixture_por_id(int(m.group(1)), params)

    raise NotImplementedError(
        f"Endpoint Sportmonks '{path}' ainda sem mock. "
        "Adiciona-o em apostas/ingestao/_mocks_sm.py."
    )


def _envelope(data: Any, paginated: bool = False) -> dict[str, Any]:
    env = {
        "data": data,
        "subscription": [{"meta": {"trial_ends_at": None, "ends_at": None, "current_timestamp": 0}}],
        "rate_limit": {"resets_in_seconds": 3600, "remaining": 2999, "requested_entity": "Mock"},
        "timezone": "UTC",
    }
    if paginated:
        n = len(data) if isinstance(data, list) else 1
        env["pagination"] = {
            "count": n, "per_page": 25, "current_page": 1,
            "next_page": None, "has_more": False,
        }
    return env


def _league_por_id(lid: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if lid not in _LIGAS:
        raise ValueError(f"Liga Sportmonks desconhecida no mock: {lid}")
    info = _LIGAS[lid]
    data = {
        "id": lid,
        "sport_id": 1,
        "country_id": info["pais_id"],
        "name": info["nome"],
        "active": True,
        "short_code": None,
        "image_path": None,
        "type": "league",
        "sub_type": "domestic",
        "category": 1,
        "has_jerseys": False,
        "currentseason": {
            "id": info["epoca_atual_id"],
            "sport_id": 1,
            "league_id": lid,
            "name": "2024/2025",
            "finished": False,
            "pending": False,
            "is_current": True,
            "starting_at": "2024-08-01",
            "ending_at": "2025-05-31",
        },
    }

    # ?include=seasons devolve todas as épocas
    include = (params or {}).get("include", "")
    if "seasons" in include:
        data["seasons"] = [
            {
                "id": info["epoca_anterior_id"],
                "league_id": lid,
                "name": "2023/2024",
                "finished": True,
                "is_current": False,
                "starting_at": "2023-08-01",
                "ending_at": "2024-05-31",
            },
            {
                "id": info["epoca_atual_id"],
                "league_id": lid,
                "name": "2024/2025",
                "finished": False,
                "is_current": True,
                "starting_at": "2024-08-01",
                "ending_at": "2025-05-31",
            },
        ]
    return _envelope(data)


def _listar_leagues() -> dict[str, Any]:
    data = []
    for lid, info in _LIGAS.items():
        data.append({
            "id": lid,
            "sport_id": 1,
            "country_id": info["pais_id"],
            "name": info["nome"],
            "active": True,
        })
    return _envelope(data, paginated=True)


def _teams_da_epoca(season_id: int) -> dict[str, Any]:
    # A época atual (10xxx) e a anterior (20xxx) partilham o roster do mock.
    equipas = _EQUIPAS_POR_EPOCA.get(season_id, [])
    if not equipas and season_id in _EPOCA_PARA_LIGA:
        liga_id, _ = _EPOCA_PARA_LIGA[season_id]
        epoca_atual = _LIGAS[liga_id]["epoca_atual_id"]
        equipas = _EQUIPAS_POR_EPOCA.get(epoca_atual, [])

    data = []
    for tid, nome in equipas:
        data.append({
            "id": tid,
            "sport_id": 1,
            "country_id": None,
            "venue_id": None,
            "gender": "male",
            "name": nome,
            "short_code": nome[:3].upper(),
            "image_path": None,
            "founded": 1900,
            "type": "domestic",
            "placeholder": False,
        })
    return _envelope(data, paginated=True)


# ─── Fixtures e estatísticas ──────────────────────────────────────────

# IDs das estatísticas na Sportmonks v3 (ver docs oficiais quando ligar à real).
# Mantemos aqui espelhados; o parser real usa estes mesmos IDs.
STAT_SHOTS_TOTAL = 42
STAT_SHOTS_ON_TARGET = 86
STAT_CORNERS = 34
STAT_POSSESSION = 45
STAT_FOULS = 56
STAT_YELLOW_CARDS = 84
STAT_RED_CARDS = 83


def _fixtures_da_epoca(season_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """Gera fixtures sintéticos para uma época (determinístico por season_id)."""
    if season_id not in _EPOCA_PARA_LIGA:
        return _envelope([], paginated=True)

    liga_id, ano = _EPOCA_PARA_LIGA[season_id]
    epoca_atual = _LIGAS[liga_id]["epoca_atual_id"]
    equipas = _EQUIPAS_POR_EPOCA.get(epoca_atual, [])
    rng = random.Random(season_id)

    include = params.get("include", "")
    inc_stats = "statistics" in include

    inicio = datetime(ano, 8, 1)
    data = []
    fixture_id_base = season_id * 1000
    jornada = 0
    for i, (tid_c, nome_c) in enumerate(equipas):
        for j, (tid_f, nome_f) in enumerate(equipas):
            if i == j:
                continue
            jornada += 1
            kickoff = inicio + timedelta(days=(jornada - 1) * 3, hours=15)
            golos_c = rng.randint(0, 4)
            golos_f = rng.randint(0, 3)
            fixture = _construir_fixture(
                fixture_id_base + jornada,
                liga_id, season_id,
                kickoff, tid_c, nome_c, tid_f, nome_f,
                golos_c, golos_f,
                rng, inc_stats,
            )
            data.append(fixture)

    return _envelope(data, paginated=True)


def _fixture_por_id(fixture_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """Devolve um fixture específico. Não usado no fluxo normal (fixtures/seasons chega)."""
    include = params.get("include", "")
    inc_stats = "statistics" in include
    # Procura em todas as épocas até encontrar
    for season_id in _EPOCA_PARA_LIGA:
        resp = _fixtures_da_epoca(season_id, {"include": include})
        for f in resp["data"]:
            if f["id"] == fixture_id:
                return _envelope(f)
    raise ValueError(f"Fixture {fixture_id} não encontrado no mock")


def _construir_fixture(
    fid: int, liga_id: int, season_id: int,
    kickoff: datetime, tid_c: int, nome_c: str, tid_f: int, nome_f: str,
    golos_c: int, golos_f: int,
    rng: random.Random, inc_stats: bool,
) -> dict[str, Any]:
    fixture = {
        "id": fid,
        "sport_id": 1,
        "league_id": liga_id,
        "season_id": season_id,
        "stage_id": season_id + 1,
        "group_id": None,
        "aggregate_id": None,
        "round_id": None,
        "state_id": 5,  # 5 = terminado (FT)
        "venue_id": None,
        "name": f"{nome_c} vs {nome_f}",
        "starting_at": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
        "result_info": f"{nome_c} won" if golos_c > golos_f else (f"{nome_f} won" if golos_f > golos_c else "Draw"),
        "leg": "1/1",
        "details": None,
        "length": 90,
        "placeholder": False,
        "has_odds": True,
        "starting_at_timestamp": int(kickoff.timestamp()),
        "participants": [
            {"id": tid_c, "name": nome_c, "meta": {"location": "home"}},
            {"id": tid_f, "name": nome_f, "meta": {"location": "away"}},
        ],
        "scores": [
            {"id": fid * 10 + 1, "fixture_id": fid, "type_id": 1525, "participant_id": tid_c,
             "score": {"goals": golos_c, "participant": "home"}, "description": "CURRENT"},
            {"id": fid * 10 + 2, "fixture_id": fid, "type_id": 1525, "participant_id": tid_f,
             "score": {"goals": golos_f, "participant": "away"}, "description": "CURRENT"},
        ],
    }
    if inc_stats:
        fixture["statistics"] = _stats_para_fixture(
            fid, tid_c, tid_f, golos_c, golos_f, rng,
        )
    return fixture


def _stats_para_fixture(
    fid: int, tid_c: int, tid_f: int,
    golos_c: int, golos_f: int, rng: random.Random,
) -> list[dict[str, Any]]:
    """Devolve lista de stats no formato Sportmonks."""
    stats = []
    sid = fid * 100
    for pid, golos in ((tid_c, golos_c), (tid_f, golos_f)):
        remates = max(golos + rng.randint(3, 15), golos)
        remates_baliza = min(remates, max(golos, rng.randint(2, 8)))
        cantos = rng.randint(2, 10)
        posse = round(rng.uniform(35, 65), 1)
        faltas = rng.randint(5, 18)
        amarelos = rng.randint(0, 4)
        vermelhos = 1 if rng.random() < 0.07 else 0

        for type_id, valor in (
            (STAT_SHOTS_TOTAL, remates),
            (STAT_SHOTS_ON_TARGET, remates_baliza),
            (STAT_CORNERS, cantos),
            (STAT_POSSESSION, posse),
            (STAT_FOULS, faltas),
            (STAT_YELLOW_CARDS, amarelos),
            (STAT_RED_CARDS, vermelhos),
        ):
            sid += 1
            stats.append({
                "id": sid,
                "fixture_id": fid,
                "type_id": type_id,
                "participant_id": pid,
                "data": {"value": valor},
                "location": "home" if pid == tid_c else "away",
            })
    return stats
