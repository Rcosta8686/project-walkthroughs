"""Mock de HTML do Understat para testes.

Gera uma página mínima com um bloco ``var matchesData = JSON.parse('...')``
que o parser real sabe interpretar, usando as equipas do mock Sportmonks.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

# Equipas por liga Understat → usamos nomes que já casam com os mocks sportmonks
_EQUIPAS = {
    "EPL": ["Manchester United", "Newcastle", "Liverpool", "Arsenal", "Chelsea", "Manchester City"],
    "La_liga": ["Barcelona", "Atletico Madrid", "Real Madrid", "Real Sociedad", "Real Betis", "Valencia"],
    "Serie_A": ["AC Milan", "Juventus", "AS Roma", "Inter", "Napoli", "Lazio"],
    "Bundesliga": ["Bayern Munich", "Borussia Dortmund", "Bayer Leverkusen", "RB Leipzig", "Eintracht Frankfurt", "SC Freiburg"],
    "Ligue_1": ["Paris Saint Germain", "Monaco", "Marseille", "Lille", "Nice", "Lyon"],
}


def gerar_html(liga_slug: str, ano: int) -> str:
    equipas = _EQUIPAS.get(liga_slug, [])
    if not equipas:
        return "<html></html>"

    rng = random.Random(hash((liga_slug, ano)) & 0xFFFFFFFF)
    inicio = datetime(ano, 8, 1)
    matches = []
    jornada = 0
    for i, casa in enumerate(equipas):
        for j, fora in enumerate(equipas):
            if i == j:
                continue
            jornada += 1
            kickoff = inicio + timedelta(days=(jornada - 1) * 3, hours=15)
            xg_casa = round(rng.uniform(0.5, 2.5), 2)
            xg_fora = round(rng.uniform(0.3, 2.0), 2)
            matches.append({
                "h": {"title": casa},
                "a": {"title": fora},
                "xG": {"h": xg_casa, "a": xg_fora},
                "datetime": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
            })

    payload = json.dumps(matches)
    # Simula o escape single-quote style do Understat
    escaped = payload.replace("\\", "\\\\").replace("'", "\\'")
    html = f"""<!DOCTYPE html>
<html>
<head><title>Understat mock</title></head>
<body>
<script>
    var matchesData = JSON.parse('{escaped}');
</script>
</body>
</html>"""
    return html
