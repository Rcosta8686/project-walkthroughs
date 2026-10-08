"""Script: diagnosticar_understat.py

Descarrega UMA página da Understat (EPL 2024 por default), guarda o HTML
inteiro em `dados/raw/understat/diagnostico.html`, e imprime:
  - Tamanho do HTML
  - Posição de cada ocorrência de "JSON.parse"
  - Primeiros 10 nomes de variáveis em "var X =" (dentro de scripts)
  - Primeiros 300 chars de contexto em cada JSON.parse
  - Primeiros 300 chars de contexto nas definições de datesData, matchesData,
    teamsData, statisticsData

Usar depois para colar o output e ajustar o regex.

Uso:
    python scripts/diagnosticar_understat.py                # EPL 2024
    python scripts/diagnosticar_understat.py EPL 2025
    python scripts/diagnosticar_understat.py La_liga 2024
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402


def main() -> None:
    liga = sys.argv[1] if len(sys.argv) > 1 else "EPL"
    ano = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
    url = f"https://understat.com/league/{liga}/{ano}"

    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        timeout=60,
    )
    resp.raise_for_status()
    html = resp.text

    destino = project_root() / "dados" / "raw" / "understat" / "diagnostico.html"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")

    print(f"\n✓ HTML gravado em: {destino}")
    print(f"  Tamanho: {len(html):,} chars")

    print("\n── Ocorrências de 'JSON.parse' ─────────────────────────────────")
    pos = 0
    ocorrencias = []
    while True:
        idx = html.find("JSON.parse", pos)
        if idx == -1:
            break
        ocorrencias.append(idx)
        pos = idx + 1
    print(f"  Total: {len(ocorrencias)} ocorrências")
    for i, idx in enumerate(ocorrencias[:5]):
        contexto = html[max(0, idx - 30):idx + 150].replace("\n", " ")
        print(f"  [{i+1}] pos={idx}: {contexto!r}")

    print("\n── Nomes de variáveis 'var X =' (primeiros 20) ─────────────────")
    nomes = re.findall(r"var\s+(\w+)\s*=", html)
    for n in nomes[:20]:
        print(f"  - {n}")

    print("\n── Posições de datesData / matchesData / teamsData / stats ─────")
    for nome in ("datesData", "matchesData", "teamsData", "statisticsData"):
        idx = html.find(nome)
        if idx != -1:
            contexto = html[idx:idx + 200].replace("\n", " ")
            print(f"  {nome} @ pos={idx}: {contexto!r}")
        else:
            print(f"  {nome}: NÃO ENCONTRADO")

    print("\n── Snippet à volta do primeiro script com dados ─────────────────")
    # Procurar o primeiro <script> depois do fim do <head>
    body_start = html.find("<body")
    if body_start != -1:
        script_start = html.find("<script", body_start)
        if script_start != -1:
            print(f"  <script> em pos={script_start}:")
            print(html[script_start:script_start + 600])


if __name__ == "__main__":
    main()
