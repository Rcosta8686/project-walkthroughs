"""Diagnostico: lista jogos live Sofascore sem filtro de ligas.

Util quando o scanner_live_v2 diz "sem jogos live nas ligas que seguimos" — ves
aqui o que realmente esta a decorrer. Mostra tambem o nome exacto de cada
torneio tal como aparece na Sofascore (serve para calibrar o filtro de ligas
se os nomes mudarem).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import sofascore_live  # noqa: E402


def main() -> None:
    print("\nA perguntar a Sofascore pelos jogos live via flaresolverr...")
    jogos = sofascore_live.listar_jogos_live()
    print(f"\nTOTAL jogos live futebol: {len(jogos)}")
    if not jogos:
        print("(nenhum jogo a decorrer agora em futebol mundial)")
        return

    # Agrupa por torneio para veres se as tuas ligas estao presentes
    por_torneio: dict[str, list] = {}
    for j in jogos:
        liga = j.get("tournament", {}).get("name", "?")
        por_torneio.setdefault(liga, []).append(j)

    print("\nTorneios com jogos live (ordenados por quantidade):")
    for liga, lista in sorted(por_torneio.items(), key=lambda x: -len(x[1])):
        print(f"\n  === {liga} ({len(lista)} jogos) ===")
        for j in lista:
            home = j.get("homeTeam", {}).get("name", "?")
            away = j.get("awayTeam", {}).get("name", "?")
            status = j.get("status", {}).get("description", "?")
            hc = (j.get("homeScore") or {}).get("current", 0)
            ac = (j.get("awayScore") or {}).get("current", 0)
            print(f"    [{status}] {home} {hc}-{ac} {away}")


if __name__ == "__main__":
    main()
