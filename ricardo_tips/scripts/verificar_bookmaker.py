"""Script: verificar_bookmaker.py

Pergunta directamente a Odds API: 'devolve odds de BET365 (ou outro)?'
Útil para confirmar se o plano actual suporta bookmakers específicos.

Uso:
    python scripts/verificar_bookmaker.py
    python scripts/verificar_bookmaker.py --bookmakers bet365,betano,1xbet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import get_env  # noqa: E402

_BASE = "https://api.the-odds-api.com/v4"
_SPORT_TESTE = "soccer_epl"  # tipicamente tem cobertura em todos os planos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bookmakers", default="bet365,betano,1xbet,marathonbet,bwin,ladbrokes",
        help="Lista de keys de bookmakers a testar, separadas por virgula.",
    )
    parser.add_argument(
        "--regions", default="eu,uk,us,au",
        help="Regioes a pedir.",
    )
    args = parser.parse_args()

    apikey = get_env("ODDS_API_KEY", required=True)
    casas = [b.strip() for b in args.bookmakers.split(",")]

    print(f"\n{'=' * 90}")
    print(f"  VERIFICAR BOOKMAKERS no plano actual")
    print(f"{'=' * 90}")
    print(f"  Sport teste: {_SPORT_TESTE}")
    print(f"  Regioes:     {args.regions}")
    print(f"  Bookmakers:  {casas}")
    print(f"  {'-' * 50}")

    # Primeiro: pedir TUDO em `--regions` para ver que bookmakers vêm
    print(f"\n  [A] Chamada sem filtro, para ver cobertura total do plano...")
    url = f"{_BASE}/sports/{_SPORT_TESTE}/odds"
    resp = requests.get(
        url,
        params={
            "apiKey": apikey, "regions": args.regions,
            "markets": "h2h", "oddsFormat": "decimal",
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"  ERRO HTTP {resp.status_code}: {resp.text[:300]}")
        return
    jogos = resp.json()
    todos_bms = set()
    for j in jogos:
        for bm in j.get("bookmakers", []):
            todos_bms.add(bm.get("key", ""))
    print(f"  Jogos devolvidos:    {len(jogos)}")
    print(f"  Credits restantes:   {resp.headers.get('x-requests-remaining', '?')}")
    print(f"  Credits usados hoje: {resp.headers.get('x-requests-used', '?')}")
    print(f"  Bookmakers no feed:  {sorted(todos_bms)}")

    # Segundo: para cada bookmaker da lista, perguntar se ela devolve
    print(f"\n  [B] Chamada POR bookmaker (confirma suporte individual):")
    print(f"  {'Bookmaker':<16} {'Devolveu odds?':<18} {'Mensagem':<40}")
    print(f"  {'-' * 16} {'-' * 18} {'-' * 40}")
    for casa in casas:
        r = requests.get(
            url,
            params={
                "apiKey": apikey, "regions": args.regions,
                "markets": "h2h", "oddsFormat": "decimal",
                "bookmakers": casa,
            },
            timeout=30,
        )
        if r.status_code == 422:
            print(f"  {casa:<16} {'NAO SUPORTADA':<18} {r.text[:40]:<40}")
            continue
        if not r.ok:
            print(f"  {casa:<16} {'ERRO ' + str(r.status_code):<18} {r.text[:40]:<40}")
            continue
        jj = r.json()
        n_jogos = sum(1 for j in jj if j.get("bookmakers"))
        print(f"  {casa:<16} {('SIM (' + str(n_jogos) + ' jogos)'):<18} {'':<40}")

    print(f"\n{'=' * 90}\n")


if __name__ == "__main__":
    main()
