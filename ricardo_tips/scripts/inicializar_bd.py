"""Script: inicializar_bd.py

Cria o ficheiro SQLite (se não existir) e todas as tabelas definidas em
`apostas/utils/schema.py`. Idempotente — correr várias vezes é seguro,
não apaga dados existentes.

Uso:
    python scripts/inicializar_bd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite correr sem instalar o pacote
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import db_path  # noqa: E402
from apostas.utils.db import criar_schema  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    caminho = db_path()
    log.info("A inicializar base de dados em %s", caminho)
    criar_schema()
    print(f"✓ Base de dados pronta em: {caminho}")


if __name__ == "__main__":
    main()
