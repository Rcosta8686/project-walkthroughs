"""Repara o sugestoes_live_v2.csv: volta a alinhar as linhas com as colunas.

O ficheiro guardava o cabecalho do dia em que foi criado; as linhas escritas
depois de cada mudanca de colunas ficaram deslocadas. Sintomas: numeros
/apostei repetidos e alertas que o resolver nunca resolve ("Pendentes: 0").

Faz copia de seguranca antes de mexer e nao toca em nada se nao reconhecer
o ficheiro. O scanner faz o mesmo sozinho ao arrancar.

Uso (com o scanner parado):
  venv\\Scripts\\python.exe scripts\\reparar_csv_live.py               # diagnostica e repara
  venv\\Scripts\\python.exe scripts\\reparar_csv_live.py --so-ver      # so diagnostica
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: acentos sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.relatorios import alertas_enviados, csv_live  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Repara o CSV de alertas live.")
    parser.add_argument("--so-ver", action="store_true", help="So diagnostica, nao altera")
    args = parser.parse_args()
    f = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    if not f.exists():
        print(f"Nao existe {f}")
        return
    print(f"Ficheiro: {f}\n")
    diag = csv_live.realinhar(f, aplicar=not args.so_ver,
                              registo_enviados=alertas_enviados.caminho())
    print(diag.texto())
    if diag.reparado:
        print("\nAgora corre o resolver para fechar os alertas que estavam escondidos:\n"
              "  venv\\Scripts\\python.exe scripts\\resolver_sugestoes_live.py --desde 2026-10-18")


if __name__ == "__main__":
    main()
