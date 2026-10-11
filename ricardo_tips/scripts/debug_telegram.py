"""Diagnostico Telegram: mostra o que esta a passar.

Verifica:
  - MODO (producao vs desenvolvimento)
  - TELEGRAM_BOT_TOKEN presente?
  - TELEGRAM_CHAT_ID presente?
  - Se MODO=desenvolvimento → onde o ficheiro mock esta a gravar
  - Se MODO=producao → tenta enviar e reporta erro completo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import get_env, project_root  # noqa: E402


def _mascarar(s: str | None, chars_visiveis: int = 4) -> str:
    if not s:
        return "<NAO DEFINIDO>"
    if len(s) <= chars_visiveis * 2:
        return "*" * len(s)
    return s[:chars_visiveis] + "..." + s[-chars_visiveis:]


def main() -> None:
    print("\n" + "=" * 70)
    print("  DIAGNOSTICO TELEGRAM")
    print("=" * 70)

    env_path = project_root() / ".env"
    print(f"\n.env path: {env_path}")
    print(f".env existe: {env_path.exists()}")
    if env_path.exists():
        print(f".env tamanho: {env_path.stat().st_size} bytes")

    modo = get_env("MODO", "desenvolvimento")
    token = get_env("TELEGRAM_BOT_TOKEN", default=None)
    chat_id = get_env("TELEGRAM_CHAT_ID", default=None)

    print(f"\nMODO                = {modo!r}")
    print(f"TELEGRAM_BOT_TOKEN  = {_mascarar(token, 6)}")
    print(f"TELEGRAM_CHAT_ID    = {chat_id!r}")

    # Decidir o que fazer
    print("\n" + "-" * 70)
    if modo != "producao":
        print(f"⚠ MODO={modo!r} → mensagens vao para FICHEIRO, nao para Telegram.")
        outbox = project_root() / "dados" / "telegram_outbox.log"
        print(f"  Ficheiro mock: {outbox}")
        print(f"  Existe: {outbox.exists()}")
        if outbox.exists():
            print(f"  Tamanho: {outbox.stat().st_size} bytes")
            print("\n  Ultimas 3 mensagens no mock:")
            with outbox.open(encoding="utf-8") as f:
                linhas = f.readlines()
            for linha in linhas[-3:]:
                print(f"    {linha.strip()}")
        print("\n  PARA RECEBER NO TELEMOVEL:")
        print("  1. Abre o .env no Bloco de Notas")
        print("  2. Procura linha 'MODO=' e muda para 'MODO=producao'")
        print("  3. Guarda e sai")
        print("  4. Re-corre este diagnostico")
        return

    if not token:
        print("✗ MODO=producao mas TELEGRAM_BOT_TOKEN em falta. Adiciona ao .env:")
        print("  TELEGRAM_BOT_TOKEN=1234567890:ABCdef...")
        return
    if not chat_id:
        print("✗ MODO=producao mas TELEGRAM_CHAT_ID em falta. Adiciona ao .env:")
        print("  TELEGRAM_CHAT_ID=123456789")
        return

    print("✓ MODO=producao + token + chat_id presentes. A tentar enviar...")
    print("-" * 70)

    try:
        from apostas.alertas.telegram_cliente import cliente
        c = cliente(modo="producao")
        c.enviar("[Diagnostico] Scanner Live v2 — Telegram funciona!")
        print("\n✓ ENVIO OK. Verifica o telemovel.")
    except Exception as exc:  # noqa: BLE001
        print(f"\n✗ ERRO ao enviar: {type(exc).__name__}: {exc}")
        import traceback
        print("\nStacktrace completo:")
        traceback.print_exc()
        print("\nCausas comuns:")
        print(" - TOKEN invalido (foste ao BotFather e copiaste bem?)")
        print(" - CHAT_ID errado (abriste a conversa com o bot e enviaste /start?)")
        print(" - python-telegram-bot nao instalado (pip install python-telegram-bot)")


if __name__ == "__main__":
    main()
