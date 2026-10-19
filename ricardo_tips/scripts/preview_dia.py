"""Preview dos jogos de HOJE (ou --data YYYY-MM-DD).

Para automatizar: Windows Task Scheduler as 17:00 todos os dias com:
  Programa: venv\\Scripts\\python.exe
  Argumentos: scripts\\preview_dia.py --telegram
  Pasta: C:\\...\\ricardo_tips

Usa Sofascore + mini-cerebros (confronto) para gerar texto com cenarios
do dia nas ligas prioritarias.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.dados_referencia.ligas_prioritarias import (  # noqa: E402
    sofascore_ids_prioritarias,
)
from apostas.relatorios.preview import (  # noqa: E402
    formatar_preview_telegram, gerar_preview,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None,
                        help="Data YYYY-MM-DD. Default: hoje.")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--todas-ligas", action="store_true",
                        help="Nao filtra pelas ligas prioritarias.")
    args = parser.parse_args()

    if args.data:
        data = datetime.strptime(args.data, "%Y-%m-%d")
    else:
        data = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    fim = data + timedelta(days=1)

    ids_ligas = None if args.todas_ligas else tuple(sofascore_ids_prioritarias())
    preview = gerar_preview(data, fim, ids_ligas=ids_ligas)
    texto = formatar_preview_telegram(preview, titulo=f"📅 Preview {data:%d/%m}")
    print(texto)

    if args.telegram:
        try:
            from apostas.alertas.telegram_cliente import cliente
            cliente().enviar(texto)
            print("\n[OK] Enviado para Telegram.")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERRO] Nao conseguiu enviar Telegram: {exc}")


if __name__ == "__main__":
    main()
