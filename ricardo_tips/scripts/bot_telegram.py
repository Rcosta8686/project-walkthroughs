"""Script: bot_telegram.py

Processo de longa duração que mantém o bot Telegram ativo e trata dos
comandos interativos:

    /hoje        - jogos de hoje com EV positivo
    /amanha      - jogos de amanhã com EV positivo
    /stats       - ROI do mês, nº de apostas, hit rate
    /registar    - registar uma aposta efetivamente feita
    /pausar      - suspender alertas automáticos
    /retomar     - reativar alertas
    /jogo <ID>   - análise detalhada de um jogo

Uso:
    python scripts/bot_telegram.py

Arranque automático no Windows:
  1. Cria um atalho para este script
  2. Coloca-o na pasta "Arranque" (Win+R → shell:startup)
  Ou usa o Task Scheduler com trigger "Ao iniciar sessão".
"""


def main() -> None:
    raise NotImplementedError(
        "Bot Telegram ainda não implementado. Ver ARQUITETURA.md §4.5."
    )


if __name__ == "__main__":
    main()
