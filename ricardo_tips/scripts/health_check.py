"""Health check — chamado pelo Task Scheduler cada 30 min.

Verifica flaresolverr + CSV live + BD. Se algo falhar, envia 1 mensagem
Telegram. Se tudo OK, fica em silencio (nao enche a atencao).

Uso:
  python scripts/health_check.py                   # imprime + Telegram se problema
  python scripts/health_check.py --sempre          # imprime sempre (debug)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.alertas.health_check import (  # noqa: E402
    correr_health_check, formatar_telegram,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sempre", action="store_true",
                        help="Imprime relatorio mesmo quando tudo OK.")
    args = parser.parse_args()

    r = correr_health_check()
    texto = formatar_telegram(r)
    print(texto)

    if r.tudo_ok and not args.sempre:
        return 0

    if not r.tudo_ok:
        try:
            from apostas.alertas.telegram_cliente import cliente
            cliente().enviar(texto)
            print("\n[OK] Alerta enviado para Telegram.")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERRO] Nao conseguiu enviar Telegram: {exc}")
            return 2
    return 1 if not r.tudo_ok else 0


if __name__ == "__main__":
    sys.exit(main())
