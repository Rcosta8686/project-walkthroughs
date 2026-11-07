"""Corrige resultados antigos de jogos sem estatisticas no Sofascore.

Ate 07/11, cantos/remates/cartoes em falta contavam como 0: os "under" desses
jogos ficavam ganhos e os "over" perdidos sem o serem. Este script passa essas
dicas a SD (sem dados). O resolver ja o faz sozinho no fim de cada corrida;
aqui e para ver o efeito.

Uso:
  venv\\Scripts\\python.exe scripts\\reparar_jogos_sem_stats.py --so-ver
  venv\\Scripts\\python.exe scripts\\reparar_jogos_sem_stats.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        getattr(_stream, "reconfigure")(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from apostas.relatorios import sem_stats  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--so-ver", action="store_true", help="Mostra sem alterar o CSV")
    args = parser.parse_args()
    csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    corr = sem_stats.reparar(csv_path, aplicar=not args.so_ver)
    if not corr.n:
        print("Nada a corrigir: nenhum resultado de jogo sem estatisticas.")
        return
    print(corr.texto())
    print("\n(so a ver: nada foi alterado)" if args.so_ver else "\nCSV actualizado.")


if __name__ == "__main__":
    main()
