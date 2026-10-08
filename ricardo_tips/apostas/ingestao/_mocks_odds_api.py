"""Mock de respostas do The Odds API para desenvolvimento/testes.

Estrutura em espelho da API real:
https://the-odds-api.com/liveapi/guides/v4/#get-odds
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

# Jogos sintéticos por sport_key — poucas equipas por liga para o mock
_JOGOS_POR_SPORT = {
    "soccer_epl": [
        ("Arsenal", "Chelsea"),
        ("Liverpool", "Manchester City"),
    ],
    "soccer_spain_la_liga": [
        ("Barcelona", "Real Madrid"),
        ("Atlético Madrid", "Sevilla"),
    ],
    "soccer_italy_serie_a": [
        ("Juventus", "AC Milan"),
    ],
    "soccer_germany_bundesliga": [
        ("Bayern München", "Borussia Dortmund"),
    ],
    "soccer_france_ligue_one": [
        ("Paris Saint-Germain", "Olympique Marseille"),
    ],
    "soccer_portugal_primeira_liga": [
        ("FC Porto", "SL Benfica"),
    ],
}


def jogos_sport(sport_key: str) -> list[dict]:
    pares = _JOGOS_POR_SPORT.get(sport_key, [])
    agora = datetime.utcnow()
    rng = random.Random(hash(sport_key) & 0xFFFFFFFF)
    out = []
    for i, (casa, fora) in enumerate(pares):
        kickoff = agora + timedelta(days=i + 1, hours=15)
        # Odds plausíveis
        odd_casa = round(rng.uniform(1.5, 3.5), 2)
        odd_fora = round(rng.uniform(1.5, 3.5), 2)
        odd_empate = round(rng.uniform(3.0, 4.0), 2)
        odd_over = round(rng.uniform(1.6, 2.3), 2)
        odd_under = round(rng.uniform(1.6, 2.3), 2)

        # Pinnacle (sharp) e Betano (soft). Betano com margem maior → odds
        # ligeiramente diferentes, cria sinal de divergência para testes.
        soft_boost = 1.08  # soft book preço médio 8% acima do pinnacle (margem)
        out.append({
            "id": f"mock_{sport_key}_{i}",
            "sport_key": sport_key,
            "sport_title": sport_key.replace("soccer_", "").replace("_", " ").title(),
            "commence_time": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home_team": casa,
            "away_team": fora,
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "last_update": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": casa, "price": odd_casa},
                                {"name": fora, "price": odd_fora},
                                {"name": "Draw", "price": odd_empate},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": odd_over, "point": 2.5},
                                {"name": "Under", "price": odd_under, "point": 2.5},
                            ],
                        },
                    ],
                },
                {
                    "key": "betano",
                    "title": "Betano",
                    "last_update": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": casa, "price": round(odd_casa * soft_boost, 2)},
                                {"name": fora, "price": round(odd_fora * soft_boost, 2)},
                                {"name": "Draw", "price": round(odd_empate * soft_boost, 2)},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": round(odd_over * soft_boost, 2), "point": 2.5},
                                {"name": "Under", "price": round(odd_under * soft_boost, 2), "point": 2.5},
                            ],
                        },
                    ],
                },
            ],
        })
    return out
