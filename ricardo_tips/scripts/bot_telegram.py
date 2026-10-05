"""Script: bot_telegram.py

Processo de longa duração que mantém o bot Telegram ativo e trata dos
comandos interativos.

Comandos (ver apostas/alertas/bot.py):
    /hoje, /amanha, /stats, /registar, /jogo, /pausar, /retomar, /ajuda

Uso:
    python scripts/bot_telegram.py

Arranque automático no Windows:
  1. Cria um atalho para este script.
  2. Coloca-o na pasta "Arranque" (Win+R → shell:startup)
  Ou usa o Task Scheduler com trigger "Ao iniciar sessão".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas.bot import construir_application  # noqa: E402
from apostas.utils.config import get_env  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    modo = get_env("MODO", "desenvolvimento")
    if modo != "producao":
        print("⚠ MODO=" + modo + " — o bot só liga à API Telegram real em MODO=producao.")
        print("  Para desenvolver, usa pytest / testes/test_bot.py.")
        return

    log.info("A arrancar o bot Telegram em polling.")
    app = construir_application()
    app.run_polling()


if __name__ == "__main__":
    main()
