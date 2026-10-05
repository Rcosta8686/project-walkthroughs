"""Respostas sintéticas que imitam a Sportmonks Football API v3.

Mantém a estrutura ``{"data": ..., "pagination": ..., "subscription": ...}``
que a API real devolve, para que o código funcione sem alterações quando
trocamos ``MODO=desenvolvimento`` por ``MODO=producao``.

Docs reais: https://docs.sportmonks.com/football
"""

from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Dados sintéticos
# ─────────────────────────────────────────────────────────────────────

# IDs reais da Sportmonks para as 6 ligas do nosso plano
_LIGAS: dict[int, dict[str, Any]] = {
    8:   {"nome": "Premier League",  "pais": "England",   "pais_id": 462, "epoca_atual_id": 10001},
    564: {"nome": "La Liga",         "pais": "Spain",     "pais_id": 32,  "epoca_atual_id": 10002},
    384: {"nome": "Serie A",         "pais": "Italy",     "pais_id": 251, "epoca_atual_id": 10003},
    82:  {"nome": "Bundesliga",      "pais": "Germany",   "pais_id": 11,  "epoca_atual_id": 10004},
    301: {"nome": "Ligue 1",         "pais": "France",    "pais_id": 17,  "epoca_atual_id": 10005},
    462: {"nome": "Liga Portugal",   "pais": "Portugal",  "pais_id": 20,  "epoca_atual_id": 10006},
}

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
        return _league_por_id(int(m.group(1)))

    # /leagues
    if path == "/leagues":
        return _listar_leagues()

    # /teams/seasons/{season_id}
    m = re.fullmatch(r"/teams/seasons/(\d+)", path)
    if m:
        return _teams_da_epoca(int(m.group(1)))

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


def _league_por_id(lid: int) -> dict[str, Any]:
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
        "last_played_at": None,
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
    equipas = _EQUIPAS_POR_EPOCA.get(season_id, [])
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
