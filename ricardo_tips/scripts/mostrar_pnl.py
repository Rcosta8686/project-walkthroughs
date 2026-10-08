"""Script: mostrar_pnl.py

Dashboard texto do P&L cumulativo das sugestões rastreadas.

Correr sempre que queres ver como o live-tracking está a ir:
    python scripts/mostrar_pnl.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes.csv"


def _stats(linhas: list[dict]) -> tuple[int, int, float, float]:
    n = len(linhas)
    v = sum(1 for l in linhas if l["resultado"] == "ganho")
    stake = sum(float(l["stake"]) for l in linhas)
    lucro = sum(float(l["lucro"]) for l in linhas if l["lucro"])
    return n, v, stake, lucro


def _imprimir_bloco(titulo: str, linhas: list[dict]) -> None:
    n, v, stake, lucro = _stats(linhas)
    if n == 0:
        print(f"  {titulo:<35} | sem dados")
        return
    hit = v / n * 100
    roi = (lucro / stake * 100) if stake else 0
    print(
        f"  {titulo:<35} | {n:>4} apostas | hit {hit:5.1f}% "
        f"| ROI {roi:+7.2f}% | lucro {lucro:+8.2f}"
    )


def main() -> None:
    f = _ficheiro_csv()
    if not f.exists():
        print(f"Sem CSV em {f}. Corre primeiro `trackear_sugestoes_softs.py`.")
        return

    with f.open(encoding="utf-8") as fp:
        linhas = list(csv.DictReader(fp))
    resolvidas = [l for l in linhas if l["resultado"]]
    pendentes = [l for l in linhas if not l["resultado"]]

    print("\n" + "═" * 80)
    print("  LIVE TRACKING — Pinnacle→softs")
    print("═" * 80)
    print(f"  Total sugestões:    {len(linhas)}")
    print(f"  Resolvidas:         {len(resolvidas)}")
    print(f"  Pendentes (jogos ainda não jogados): {len(pendentes)}")
    print()

    if not resolvidas:
        print("  Sem apostas resolvidas ainda. Corre `resolver_sugestoes.py` após os jogos.")
        print("═" * 80)
        return

    _imprimir_bloco("── TOTAL", resolvidas)
    print()

    # Por liga
    print("  ── Por liga ────────────────────────────────────────────────────────────")
    por_liga: dict[str, list] = defaultdict(list)
    for l in resolvidas:
        por_liga[l["liga"]].append(l)
    for liga, aps in sorted(por_liga.items()):
        _imprimir_bloco(liga, aps)
    print()

    # Por casa_soft
    print("  ── Por casa (soft book) ────────────────────────────────────────────────")
    por_casa: dict[str, list] = defaultdict(list)
    for l in resolvidas:
        por_casa[l["casa_soft"]].append(l)
    for casa, aps in sorted(por_casa.items()):
        _imprimir_bloco(casa, aps)
    print()

    # Por lado
    print("  ── Por lado ────────────────────────────────────────────────────────────")
    for lado in ("over", "under", "casa", "empate", "fora"):
        aps = [l for l in resolvidas if l["lado"] == lado]
        if aps:
            _imprimir_bloco(lado, aps)
    print("═" * 80)


if __name__ == "__main__":
    main()
