"""Script: testar_sofascore.py

Teste rápido do scraper Sofascore:
  1. Lista jogos de hoje em futebol
  2. Para o primeiro jogo que já terminou, puxa estatísticas e extrai xG
  3. Imprime o resultado

Serve para confirmar que o scraping funciona antes de o integrar no
pipeline. Se falhar (Cloudflare), o output diz claramente.

Uso:
    python scripts/testar_sofascore.py
    python scripts/testar_sofascore.py --data 2026-10-05
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import sofascore  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data", default=None,
        help="Data YYYY-MM-DD (default: hoje).",
    )
    args = parser.parse_args()

    data = datetime.fromisoformat(args.data) if args.data else datetime.utcnow()

    print(f"\n{'=' * 78}")
    print(f"  TESTE SOFASCORE — jogos de {data.date()}")
    print(f"{'=' * 78}")

    jogos = sofascore.listar_jogos_do_dia(data)
    if not jogos:
        print(
            "  ⚠ Sem jogos devolvidos. Possíveis causas:\n"
            "    - curl_cffi não instalado (pip install curl-cffi>=0.7)\n"
            "    - Cloudflare bloqueou (403)\n"
            "    - Data sem jogos\n"
        )
        return

    print(f"  Jogos encontrados: {len(jogos)}")
    # Filtra top-5 ligas Europa + Portugal para brevidade
    ligas_interessantes = {
        "Premier League", "LaLiga", "Serie A", "Bundesliga", "Ligue 1",
        "Liga Portugal", "Primeira Liga",
    }
    jogos_top = [
        j for j in jogos
        if (j.get("tournament", {}).get("name") in ligas_interessantes
            or any(k in (j.get("tournament", {}).get("name") or "")
                   for k in ligas_interessantes))
    ]
    print(f"  Jogos top-5 Europa + Portugal: {len(jogos_top)}")
    print()

    for j in jogos_top[:5]:
        home = j["homeTeam"]["name"]
        away = j["awayTeam"]["name"]
        liga = j.get("tournament", {}).get("name", "?")
        status = j.get("status", {}).get("description", "?")
        print(f"  [{j['id']:>10}] {home} vs {away} ({liga}) — {status}")

    # Encontra um jogo terminado para puxar xG
    terminados = [
        j for j in jogos_top
        if (j.get("status", {}).get("code", 0) == 100)  # 100 = ended
    ]
    if not terminados:
        print("\n  (Nenhum jogo top-5 já terminado para testar xG. "
              "Experimenta --data ontem.)")
        return

    j = terminados[0]
    event_id = j["id"]
    home = j["homeTeam"]["name"]
    away = j["awayTeam"]["name"]
    print(f"\n{'=' * 78}")
    print(f"  TESTE xG: {home} vs {away} (event {event_id})")
    print(f"{'=' * 78}")

    stats = sofascore.obter_estatisticas(event_id)
    if stats is None:
        print("  ⚠ Sem estatísticas disponíveis para este jogo.")
        return

    xg = sofascore.extrair_xg(stats)
    if xg is None:
        print("  ⚠ xG não encontrado no payload (jogo sem este stat).")
    else:
        print(f"  ✓ xG: {home} {xg.xg_casa} - {xg.xg_fora} {away}")

    print(f"\n{'=' * 78}")
    print(f"  ✓ Teste completo. HTML/JSON cacheado em dados/raw/sofascore/")
    print(f"{'=' * 78}\n")


if __name__ == "__main__":
    main()
