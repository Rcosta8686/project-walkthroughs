"""Script: atualizar_dados.py

Puxa dados mais recentes das 5 ligas configuradas:
  - [implementado] ligas e equipas
  - [em breve] jogos terminados e estatísticas
  - [em breve] próximos jogos, alinhamentos e lesões
  - [em breve] odds de fecho históricas (football-data.co.uk)

Grava tudo em dados/ricardo_tips.db (SQLite).

Uso:
    python scripts/atualizar_dados.py

Correr diariamente de manhã. No Windows: Task Scheduler com trigger
"Diariamente 07:00".

O modo de operação (mock vs. API real) é lido da variável de ambiente
MODO no .env — ver .env.example.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import ligas_equipas  # noqa: E402
from apostas.utils.config import get_env  # noqa: E402
from apostas.utils.db import criar_schema  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    modo = get_env("MODO", "desenvolvimento")
    log.info("A correr em modo: %s", modo)

    criar_schema()

    resultado = ligas_equipas.sincronizar()
    print(
        f"✓ Ligas: {resultado.ligas_criadas} novas, "
        f"{resultado.ligas_existentes} já existentes."
    )
    print(
        f"✓ Equipas: {resultado.equipas_criadas} novas, "
        f"{resultado.equipas_existentes} já existentes."
    )


if __name__ == "__main__":
    main()
