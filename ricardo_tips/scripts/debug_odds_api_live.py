"""Diagnostico: porque e que a Odds API devolve 'sem odds live' para jogos a decorrer.

Faz:
  1. Lista eventos da Odds API para cada sport Big-5 + LigaPT
  2. Mostra o estado de cada (home, away, commence_time, h2 Pinnacle etc)
  3. Para cada evento, chama /events/{id}/odds e mostra:
     - Numero de bookmakers que devolveram odds
     - Lista de mercados disponiveis por bookmaker
     - Se nenhum devolveu nada, mostra o status da resposta

Util quando o scanner diz 'sem odds live' em jogos que claramente
estao a decorrer.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from apostas.ingestao.odds_api import LIGAS_ODDS_API  # noqa: E402
from apostas.utils.config import get_env  # noqa: E402

_BASE = "https://api.the-odds-api.com/v4"


def main() -> None:
    api_key = get_env("ODDS_API_KEY", required=True)
    agora = datetime.utcnow()

    for nome_liga, sport in LIGAS_ODDS_API.items():
        print(f"\n{'=' * 80}")
        print(f"  {nome_liga} ({sport})")
        print("=" * 80)
        # /events (gratis)
        r = requests.get(f"{_BASE}/sports/{sport}/events", params={"apiKey": api_key}, timeout=20)
        if not r.ok:
            print(f"  /events HTTP {r.status_code}: {r.text[:150]}")
            continue
        eventos = r.json() if isinstance(r.json(), list) else []
        print(f"  {len(eventos)} eventos totais em /events")

        # Filtra apenas os que ja comecaram ou vao comecar em breve
        em_curso = []
        proximos = []
        for ev in eventos:
            ts_raw = (ev.get("commence_time") or "").replace("+00:00", "Z")
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
            delta_min = (agora - ts).total_seconds() / 60
            if -5 < delta_min < 150:
                em_curso.append((ev, delta_min))
            elif 0 > delta_min > -180:
                proximos.append((ev, delta_min))

        print(f"  → {len(em_curso)} em curso (0-150 min desde kickoff)")
        for ev, dm in em_curso:
            print(f"\n    * {ev.get('home_team')} vs {ev.get('away_team')}  (min {int(dm)})")
            # Testa /events/{id}/odds
            event_id = ev.get("id")
            if event_id is None:
                print(f"      sem event_id (?!)")
                continue
            url = f"{_BASE}/sports/{sport}/events/{event_id}/odds"
            params = {
                "apiKey": api_key,
                "regions": "eu,uk",
                "markets": "h2h,totals,btts,double_chance",
                "oddsFormat": "decimal",
            }
            r2 = requests.get(url, params=params, timeout=20)
            if r2.status_code == 404:
                print(f"      HTTP 404 (jogo nao tem odds live)")
                continue
            if not r2.ok:
                print(f"      HTTP {r2.status_code}: {r2.text[:200]}")
                continue
            data = r2.json()
            bms = data.get("bookmakers", []) if isinstance(data, dict) else []
            if not bms:
                print(f"      HTTP 200 mas 0 bookmakers (odds live vazias)")
                continue
            print(f"      HTTP 200 — {len(bms)} bookmakers:")
            for bm in bms[:5]:
                mkts = [m.get("key") for m in bm.get("markets", [])]
                print(f"        {bm.get('key'):>15}  mercados: {mkts}")

        if proximos and not em_curso:
            print(f"  (nenhum em curso. Proximos:)")
            for ev, dm in proximos[:3]:
                print(f"    {ev.get('home_team')} vs {ev.get('away_team')}  (daqui a {int(-dm)} min)")


if __name__ == "__main__":
    main()
