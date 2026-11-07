"""Copia de seguranca ja (a mesma que o incremento das 00:15 faz todas as noites).

Uso:
  venv\\Scripts\\python.exe scripts\\copia_seguranca.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        getattr(_stream, "reconfigure")(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from apostas.utils.backup import fazer_backup  # noqa: E402


def main() -> None:
    print(fazer_backup().texto())


if __name__ == "__main__":
    main()
