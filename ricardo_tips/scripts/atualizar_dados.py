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

from apostas.ingestao import football_data_uk, jogos_sportmonks, ligas_equipas  # noqa: E402
from apostas.utils.config import get_env  # noqa: E402
from apostas.utils.db import criar_schema  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    modo = get_env("MODO", "desenvolvimento")
    log.info("A correr em modo: %s", modo)

    criar_schema()

    # 1) Ligas e equipas via Sportmonks (ou mock)
    res_lf = ligas_equipas.sincronizar()
    print(
        f"✓ Ligas: {res_lf.ligas_criadas} novas, "
        f"{res_lf.ligas_existentes} já existentes."
    )
    print(
        f"✓ Equipas (Sportmonks): {res_lf.equipas_criadas} novas, "
        f"{res_lf.equipas_existentes} já existentes."
    )

    # 2) Jogos + estatísticas (remates, cantos, cartões) via Sportmonks
    res_sm = jogos_sportmonks.sincronizar(epocas_atras=2)
    print(
        f"✓ Jogos (Sportmonks): {res_sm.jogos_criados} novos, "
        f"{res_sm.jogos_existentes} já existentes, "
        f"{res_sm.stats_criados} estatísticas criadas."
    )
    if res_sm.equipas_em_falta:
        print(
            f"  ⚠ {res_sm.equipas_em_falta} fixtures ignorados por equipas em falta na BD."
        )
    for erro in res_sm.erros:
        print(f"⚠ {erro}")

    # 3) Odds de fecho históricas via football-data.co.uk (grátis)
    res_fd = football_data_uk.sincronizar(epocas=[2023, 2024])
    print(
        f"✓ Odds (football-data): {res_fd.odds_criadas} novas, "
        f"{res_fd.odds_existentes} já existentes."
    )
    if res_fd.jogos_criados:
        print(
            f"  (criados {res_fd.jogos_criados} jogos adicionais do football-data; "
            "estes têm odds mas podem não ter stats detalhadas)"
        )
    for erro in res_fd.erros:
        print(f"⚠ {erro}")


if __name__ == "__main__":
    main()
