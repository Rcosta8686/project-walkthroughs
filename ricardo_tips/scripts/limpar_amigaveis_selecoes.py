"""Tira da BD e do CSV os amigaveis, jogos de seleccoes e de formacao.

So contam jogos oficiais de clubes (campeonato, tacas, Champions, Liga Europa,
Conference). O incremento das 00:15 ja o faz todas as noites; aqui e para ver
ou fazer ja.

Uso:
  venv\\Scripts\\python.exe scripts\\limpar_amigaveis_selecoes.py --so-ver
  venv\\Scripts\\python.exe scripts\\limpar_amigaveis_selecoes.py
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

from apostas.utils.db import criar_schema  # noqa: E402
from apostas.utils.limpeza_competicoes import limpar  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--so-ver", action="store_true", help="Mostra sem apagar nada")
    args = parser.parse_args()
    criar_schema()
    r = limpar(aplicar=not args.so_ver)
    print(r.texto())
    for nome, n in sorted(r.linhas_bd.items(), key=lambda x: -x[1]):
        print(f"   {nome}: {n} linhas")
    if not r.vazio:
        print("\n(so a ver: nada foi apagado)" if args.so_ver else "\nApagado.")


if __name__ == "__main__":
    main()
