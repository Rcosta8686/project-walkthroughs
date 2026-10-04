"""Script: atualizar_dados.py

Puxa dados mais recentes das 5 ligas configuradas:
  - jogos terminados (resultados + estatísticas)
  - próximos jogos (fixtures, alinhamentos previstos)
  - lesões e suspensões
  - odds de fecho históricas (football-data.co.uk)

Grava tudo em dados/ricardo_tips.db (SQLite).

Uso:
    python scripts/atualizar_dados.py            # modo normal (incremental)
    python scripts/atualizar_dados.py --full     # recarrega as últimas 5 épocas

Correr diariamente de manhã. No Windows: adiciona ao Task Scheduler
com o trigger "Diariamente 07:00".
"""


def main() -> None:
    raise NotImplementedError(
        "Módulo de ingestão ainda não implementado. "
        "Ver ARQUITETURA.md §4.1 para o plano."
    )


if __name__ == "__main__":
    main()
