"""Preview do fim-de-semana (sex 17h → seg 00h).

Para automatizar: Windows Task Scheduler SEXTA 17:00 com:
  Programa: venv\\Scripts\\python.exe
  Argumentos: scripts\\preview_fim_de_semana_v2.py --telegram

Difere do antigo `prever_fim_de_semana.py` (que usa EV pre-match com
Odds API). Este foca-se em CENARIOS tacticos via mini-cerebros:
confronto cruzado, padroes de 2ª parte, fadiga.
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


def _janela_fim_de_semana(agora: datetime) -> tuple[datetime, datetime]:
    """Janela do PROXIMO (ou actual) fim-de-semana.

    weekday(): seg=0, ter=1, qua=2, qui=3, sex=4, sab=5, dom=6.

    Regra:
      - Sex, sab, dom → janela comeca agora e vai ate seg 00:00.
      - Seg a qui → janela vai de proxima sex 00:00 ate proxima seg 00:00.
    """
    wd = agora.weekday()
    if wd >= 4:  # sex, sab, dom
        inicio = agora.replace(minute=0, second=0, microsecond=0)
        # dias ate segunda-feira seguinte (nao incluindo hoje se for sex)
        dias_ate_seg = (7 - wd) % 7 if wd != 0 else 7
        # Correcao: para sex (4), queremos 3 dias; para sab (5), 2; para dom (6), 1
        dias_ate_seg = 7 - wd
        fim = (agora + timedelta(days=dias_ate_seg)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    else:  # seg (0), ter (1), qua (2), qui (3)
        dias_ate_sex = 4 - wd
        inicio = (agora + timedelta(days=dias_ate_sex)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        fim = inicio + timedelta(days=3)  # sex → seg
    return inicio, fim


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inicio", default=None, help="YYYY-MM-DD")
    parser.add_argument("--fim", default=None, help="YYYY-MM-DD (exclusivo)")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--todas-ligas", action="store_true")
    args = parser.parse_args()

    agora = datetime.utcnow()
    if args.inicio:
        inicio = datetime.strptime(args.inicio, "%Y-%m-%d")
    else:
        inicio, _ = _janela_fim_de_semana(agora)
    if args.fim:
        fim = datetime.strptime(args.fim, "%Y-%m-%d")
    else:
        _, fim = _janela_fim_de_semana(agora)

    ids_ligas = None if args.todas_ligas else tuple(sofascore_ids_prioritarias())
    preview = gerar_preview(inicio, fim, ids_ligas=ids_ligas)
    titulo = f"🏁 Preview Fim-de-Semana {inicio:%d/%m} → {fim:%d/%m}"
    texto = formatar_preview_telegram(preview, titulo=titulo)
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
