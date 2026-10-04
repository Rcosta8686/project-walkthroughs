"""Respostas sintéticas que imitam a API-Football.

Mantém a mesma forma (chaves, tipos, estrutura) que a API real devolve,
para que o resto do código funcione sem alterações quando trocamos
``MODO=desenvolvimento`` por ``MODO=producao``.

Documentação da API real: https://www.api-football.com/documentation-v3
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Dados sintéticos
# ─────────────────────────────────────────────────────────────────────

_LIGAS: dict[int, dict[str, Any]] = {
    39: {"nome": "Premier League", "pais": "England", "codigo_pais": "GB"},
    140: {"nome": "La Liga", "pais": "Spain", "codigo_pais": "ES"},
    135: {"nome": "Serie A", "pais": "Italy", "codigo_pais": "IT"},
    78: {"nome": "Bundesliga", "pais": "Germany", "codigo_pais": "DE"},
    61: {"nome": "Ligue 1", "pais": "France", "codigo_pais": "FR"},
}

# Para cada liga, 6 equipas sintéticas (chega para validar fluxo e schema)
_EQUIPAS: dict[int, list[tuple[int, str]]] = {
    39: [
        (33, "Manchester United"),
        (34, "Newcastle"),
        (40, "Liverpool"),
        (42, "Arsenal"),
        (49, "Chelsea"),
        (50, "Manchester City"),
    ],
    140: [
        (529, "Barcelona"),
        (530, "Atletico Madrid"),
        (541, "Real Madrid"),
        (548, "Real Sociedad"),
        (543, "Real Betis"),
        (532, "Valencia"),
    ],
    135: [
        (489, "AC Milan"),
        (496, "Juventus"),
        (497, "AS Roma"),
        (505, "Inter"),
        (492, "Napoli"),
        (487, "Lazio"),
    ],
    78: [
        (157, "Bayern Munich"),
        (165, "Borussia Dortmund"),
        (168, "Bayer Leverkusen"),
        (173, "RB Leipzig"),
        (169, "Eintracht Frankfurt"),
        (160, "SC Freiburg"),
    ],
    61: [
        (85, "Paris Saint Germain"),
        (91, "Monaco"),
        (81, "Marseille"),
        (79, "Lille"),
        (84, "Nice"),
        (80, "Lyon"),
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Router de endpoints
# ─────────────────────────────────────────────────────────────────────

def responder(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Devolve a resposta sintética adequada para o endpoint pedido."""
    if path == "/leagues":
        return _leagues(params)
    if path == "/teams":
        return _teams(params)
    raise NotImplementedError(
        f"Endpoint '{path}' ainda sem mock. Adiciona-o em apostas/ingestao/_mocks.py."
    )


def _envelope(response: list[Any]) -> dict[str, Any]:
    return {
        "get": "mock",
        "parameters": {},
        "errors": [],
        "results": len(response),
        "paging": {"current": 1, "total": 1},
        "response": response,
    }


def _leagues(params: dict[str, Any]) -> dict[str, Any]:
    pedido = params.get("id")
    if pedido is None:
        encontradas = list(_LIGAS.items())
    else:
        pedido_int = int(pedido)
        if pedido_int not in _LIGAS:
            return _envelope([])
        encontradas = [(pedido_int, _LIGAS[pedido_int])]

    response = [
        {
            "league": {"id": lid, "name": info["nome"], "type": "League"},
            "country": {"name": info["pais"], "code": info["codigo_pais"]},
            "seasons": [
                {"year": y, "start": f"{y}-08-01", "end": f"{y + 1}-05-31", "current": y == 2024}
                for y in range(2020, 2025)
            ],
        }
        for lid, info in encontradas
    ]
    return _envelope(response)


def _teams(params: dict[str, Any]) -> dict[str, Any]:
    liga_id = params.get("league")
    if liga_id is None:
        raise ValueError("mock /teams exige o parâmetro 'league'")
    liga_id = int(liga_id)
    equipas = _EQUIPAS.get(liga_id, [])

    response = [
        {
            "team": {
                "id": tid,
                "name": nome,
                "code": nome[:3].upper(),
                "country": _LIGAS[liga_id]["pais"],
                "founded": 1900,
                "national": False,
            },
            "venue": {"name": f"Estádio {nome}", "capacity": 50000},
        }
        for tid, nome in equipas
    ]
    return _envelope(response)
