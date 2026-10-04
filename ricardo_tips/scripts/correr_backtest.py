"""Script: correr_backtest.py

Corre o backtest walk-forward sobre os dados históricos e produz um
relatório HTML com ROI, drawdown, CLV e número de apostas por época.

Uso:
    python scripts/correr_backtest.py --mercado golos --linha 2.5
    python scripts/correr_backtest.py --mercado golos --linha 2.5 --inicio 2022-07-01
    python scripts/correr_backtest.py --todas-linhas

O relatório fica em apostas/dashboard/output/backtest.html.

Correr sempre que:
  - Mudas parâmetros do modelo em config.yaml
  - Adicionas dados mais recentes (depois de atualizar_dados.py)
  - Experimentas uma nova feature
"""


def main() -> None:
    raise NotImplementedError(
        "Backtest ainda não implementado. Ver ARQUITETURA.md §4.4."
    )


if __name__ == "__main__":
    main()
