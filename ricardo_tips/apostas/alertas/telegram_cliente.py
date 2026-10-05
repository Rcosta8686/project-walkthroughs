"""Cliente Telegram minimal com modo mock.

Em modo ``desenvolvimento`` grava as mensagens num ficheiro em
``dados/telegram_outbox.log`` para inspeção, sem chamadas reais.

Em modo ``producao`` usa a Bot API via python-telegram-bot.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from apostas.utils import config as _config
from apostas.utils.config import get_env
from apostas.utils.logger import get_logger

log = get_logger(__name__)


def caminho_outbox() -> Path:
    return _config.project_root() / "dados" / "telegram_outbox.log"


class ClienteTelegram:
    def __init__(self, modo: str | None = None):
        self.modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
        self._bot = None  # lazy

    def _criar_bot(self):
        if self._bot is None:
            from telegram import Bot
            token = get_env("TELEGRAM_BOT_TOKEN", required=True)
            self._bot = Bot(token=token)
        return self._bot

    def _chat_id(self) -> int:
        raw = get_env("TELEGRAM_CHAT_ID", required=True)
        return int(raw)

    def enviar(self, texto: str, chat_id: int | None = None) -> None:
        """Envia uma mensagem. Em dev faz append ao ficheiro de outbox."""
        if self.modo == "desenvolvimento":
            self._gravar_mock(texto, chat_id)
            return

        destino = chat_id if chat_id is not None else self._chat_id()
        bot = self._criar_bot()
        asyncio.run(bot.send_message(
            chat_id=destino,
            text=texto,
            parse_mode="Markdown",
        ))
        log.info("Telegram → chat %s (%d chars)", destino, len(texto))

    def _gravar_mock(self, texto: str, chat_id: int | None) -> None:
        p = caminho_outbox()
        p.parent.mkdir(parents=True, exist_ok=True)
        entrada = {
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "chat_id": chat_id,
            "texto": texto,
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        log.debug("[mock] Telegram outbox += %d chars", len(texto))


def cliente(modo: str | None = None) -> ClienteTelegram:
    return ClienteTelegram(modo=modo)


def ler_outbox() -> list[dict[str, Any]]:
    """Lê o ficheiro de outbox do mock (útil para testes/inspeção)."""
    p = caminho_outbox()
    if not p.exists():
        return []
    out = []
    for linha in p.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            try:
                out.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
    return out


def limpar_outbox() -> None:
    p = caminho_outbox()
    if p.exists():
        p.unlink()
