"""Script: analisar_jogos_hoje.py

Para cada jogo das próximas 24h:
  1. Treina o modelo até à data do jogo (walk-forward).
  2. Calcula P(over/under) para cada linha configurada.
  3. Compara com odds de referência (closing odds disponíveis na BD).
  4. Se EV >= limiar, grava Sugestao e envia alerta no Telegram.

Uso:
    python scripts/analisar_jogos_hoje.py
    python scripts/analisar_jogos_hoje.py --referencia 2024-10-05  # time machine
    python scripts/analisar_jogos_hoje.py --horas 2 --horas-fim 4   # janela custom

Correr a cada 15 min. No Windows: Task Scheduler com trigger de 15 em 15 minutos.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas import analise, estado, mensagens  # noqa: E402
from apostas.alertas.telegram_cliente import cliente as telegram  # noqa: E402
from apostas.utils.config import get_env  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--referencia", help="Data de referência (YYYY-MM-DD). Default: agora.")
    parser.add_argument("--horas", type=float, default=0.0,
                        help="Início da janela em horas a partir da referência (default 0).")
    parser.add_argument("--horas-fim", type=float, default=24.0,
                        help="Fim da janela em horas (default 24).")
    parser.add_argument("--modelo", default="gap", choices=("gap", "poisson"))
    args = parser.parse_args()

    ref = datetime.fromisoformat(args.referencia) if args.referencia else datetime.utcnow()

    if estado.esta_pausado():
        log.info("Alertas pausados — a saltar envio.")
        return

    modo = get_env("MODO", "desenvolvimento")
    log.info("Análise live (modo=%s, ref=%s)", modo, ref)

    sugestoes = analise.identificar_sugestoes(
        referencia=ref,
        janela_horas=(args.horas, args.horas_fim),
        modelo=args.modelo,
    )

    if not sugestoes:
        print("Sem sugestões novas.")
        estado.marcar_analise_feita(ref)
        return

    cli = telegram()
    for s in sugestoes:
        texto = mensagens.sugestao_nova(s.sugestao, s.jogo, s.liga, s.casa, s.fora)
        cli.enviar(texto)

    estado.marcar_analise_feita(ref)
    print(f"✓ Enviadas {len(sugestoes)} sugestões.")


if __name__ == "__main__":
    main()
