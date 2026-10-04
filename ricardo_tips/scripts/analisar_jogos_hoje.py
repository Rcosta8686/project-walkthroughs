"""Script: analisar_jogos_hoje.py

Para cada jogo das próximas 24h:
  1. Calcula a probabilidade de cada linha ativa (over/under 1.5, 2.5, 3.5).
  2. Procura jogos com kickoff daqui a 2h (configurável).
  3. Pede-te via Telegram as odds correntes da 22bet.
  4. Compara prob modelo vs odds reais → EV.
  5. Se EV >= config.value_betting.ev_minimo, envia sugestão.

Uso:
    python scripts/analisar_jogos_hoje.py

Correr de 15 em 15 minutos. No Windows: Task Scheduler com trigger
"A cada 15 minutos" e condição "só com utilizador autenticado".
"""


def main() -> None:
    raise NotImplementedError(
        "Motor de value betting ainda não implementado. "
        "Ver ARQUITETURA.md §4.3 e §4.5."
    )


if __name__ == "__main__":
    main()
