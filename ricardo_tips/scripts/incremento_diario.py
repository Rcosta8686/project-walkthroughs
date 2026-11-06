"""Incremento diario (tarefa das 00:15): nenhum jogo terminado fica de fora.

1. Para cada liga que o scanner segue, grava estatisticas de equipa e de
   jogadores dos jogos terminados nos ultimos --dias (default 3, para apanhar
   jogos que acabaram depois da meia-noite ou falharam na noite anterior).
   O que ja esta na BD e saltado.
2. Corre o resolver para os alertas desses dias (resultado, lucro e
   estatisticas dos jogos com projeccao).

Uso:
  venv\\Scripts\\python.exe scripts\\incremento_diario.py
  venv\\Scripts\\python.exe scripts\\incremento_diario.py --dias 7 --telegram
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS.parent))
sys.path.insert(0, str(_SCRIPTS))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: emojis sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.modelos import incremento_diario  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremento diario dos mini-cerebros.")
    parser.add_argument("--dias", type=int, default=3)
    parser.add_argument("--sem-resolver", action="store_true")
    parser.add_argument("--telegram", action="store_true", help="Envia o resumo")
    args = parser.parse_args()

    from apostas.utils.db import criar_schema
    criar_schema()

    agora = agora_utc()
    print(f"Incremento diario: jogos terminados desde {agora - timedelta(days=args.dias):%d/%m %H:%M} UTC\n")
    try:
        resumo = incremento_diario.correr(dias=args.dias, agora=agora)
    except FlaresolverrIndisponivel as exc:
        print(f"⚠ Flaresolverr indisponivel ({exc}). Volta a tentar amanha;"
              " para ja: docker start flaresolverr")
        return
    texto = resumo.texto()
    print("\n" + texto)
    log.info("Incremento diario: %s", texto.replace("\n", " | "))

    if not args.sem_resolver:
        import resolver_sugestoes_live
        inicio = datetime(agora.year, agora.month, agora.day) - timedelta(days=args.dias)
        print(f"\nA resolver alertas desde {inicio:%d/%m}...\n")
        resolver_sugestoes_live.resolver(desde=inicio)

    if args.telegram:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)


if __name__ == "__main__":
    main()
