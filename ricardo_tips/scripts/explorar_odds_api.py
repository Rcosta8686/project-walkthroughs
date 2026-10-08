"""Script: explorar_odds_api.py

Explora toda a cobertura da Odds API free:
  1. Lista todos os sports/competições suportados
  2. Para cada um, amostra uma chamada curta (1 jogo) e vê:
     - Quantos bookmakers devolvem odds
     - Pinnacle está entre eles?
     - Softs (bet365, betano, 1xbet, williamhill, unibet, marathonbet)?
  3. Produz tabela que mostra onde há cobertura mista para o scanner.

Objectivo: encontrar ligas/desportos onde Pinnacle + várias softs
devolvem odds em pre-match. Esses são os candidatos a edge executável.

Uso:
    python scripts/explorar_odds_api.py --categorias soccer,basketball
    python scripts/explorar_odds_api.py --limite 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import get_env  # noqa: E402

_BASE_URL = "https://api.the-odds-api.com/v4"
_SOFTS_INTERESSANTES = {
    "pinnacle", "bet365", "betano", "unibet", "williamhill",
    "1xbet", "marathonbet", "bwin", "ladbrokes", "coral",
}


def listar_sports(apikey: str) -> list[dict]:
    url = f"{_BASE_URL}/sports?apiKey={apikey}&all=false"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def amostrar_odds(apikey: str, sport_key: str, regions: str = "eu,uk") -> tuple[int, list[str]]:
    """Chama o endpoint com `markets=h2h` só para ver que bookmakers devolvem.

    Devolve (n_jogos, lista_bookmakers_unicos). Gasta 1-2 credits.
    """
    url = f"{_BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": apikey,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    resp = requests.get(url, params=params, timeout=30)
    if not resp.ok:
        return 0, []
    jogos = resp.json()
    bookmakers = set()
    for jogo in jogos:
        for bm in jogo.get("bookmakers", []):
            bookmakers.add(bm.get("key", ""))
    return len(jogos), sorted(bookmakers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--categorias", default="soccer,basketball,tennis",
        help="Categorias a varrer (prefixos do sport_key). Default: soccer,basketball,tennis",
    )
    parser.add_argument(
        "--limite", type=int, default=15,
        help="Max competicoes a amostrar por categoria (poupa credits).",
    )
    parser.add_argument(
        "--regions", default="eu,uk",
        help="Regioes a pedir. Default eu,uk.",
    )
    args = parser.parse_args()

    apikey = get_env("ODDS_API_KEY", required=True)
    categorias = tuple(c.strip() for c in args.categorias.split(","))

    print(f"\n{'=' * 100}")
    print(f"  EXPLORADOR DA ODDS API — categorias: {categorias}")
    print(f"{'=' * 100}\n")

    print("A listar sports...")
    sports = listar_sports(apikey)
    print(f"  {len(sports)} sports/competições activos.\n")

    # Filtra por categoria
    filtrados = [s for s in sports if any(s["key"].startswith(c) for c in categorias)]
    print(f"  {len(filtrados)} em {categorias}:\n")

    for s in filtrados[:40]:
        print(f"    {s['key']:<42} {s['title']}")

    if len(filtrados) > args.limite:
        print(f"\n  (Vou amostrar as primeiras {args.limite} para poupar credits.)")
        filtrados = filtrados[:args.limite]

    print(f"\n{'=' * 100}")
    print(f"  AMOSTRAGEM: bookmakers por competição (regions={args.regions})")
    print(f"{'=' * 100}")
    print(f"  {'Competição':<42} {'N jogos':>8} {'Pin':>4} {'Softs interessantes':<40}")
    print(f"  {'-' * 42} {'-' * 8} {'-' * 4} {'-' * 40}")

    bons = []
    for s in filtrados:
        n_jogos, bms = amostrar_odds(apikey, s["key"], args.regions)
        tem_pin = "pinnacle" in bms
        softs = [bm for bm in bms if bm in _SOFTS_INTERESSANTES and bm != "pinnacle"]
        marca = ""
        if tem_pin and len(softs) >= 2:
            marca = " <-- bom"
            bons.append((s["key"], s["title"], n_jogos, softs))
        print(
            f"  {s['title'][:40]:<42} {n_jogos:>8} "
            f"{'sim' if tem_pin else '---':>4} {str(softs)[:40]:<40}{marca}"
        )

    print(f"\n{'=' * 100}")
    print(f"  COMPETIÇÕES COM COBERTURA MISTA (Pinnacle + >= 2 softs)")
    print(f"{'=' * 100}")
    if not bons:
        print("  Nenhuma — plano free provavelmente insuficiente.")
    else:
        for key, titulo, n, softs in bons:
            print(f"  {titulo:<42} ({key}) · {n} jogos · softs: {softs}")

    print(f"\n{'=' * 100}\n")


if __name__ == "__main__":
    main()
