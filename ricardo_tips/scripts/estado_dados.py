"""O historico esta a crescer? Saude dos dados a pedido.

O mesmo resumo que a tarefa da noite manda para o Telegram: o que entrou na BD
por dia nos ultimos 7 dias (jogos terminados, fichas de jogadores, momentos ao
vivo, dicas enviadas), o total guardado, quando correu a ultima noite e a
ultima copia de seguranca. Avisa do que estiver mal.

Uso:
  venv\\Scripts\\python.exe scripts\\estado_dados.py           # mostra e envia ao Telegram
  venv\\Scripts\\python.exe scripts\\estado_dados.py --so-ver  # so mostra
  venv\\Scripts\\python.exe scripts\\estado_dados.py --dias 14
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: emojis sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.relatorios import saude_dados  # noqa: E402
from apostas.utils.db import abrir_sessao, criar_schema  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Saude dos dados (o historico esta a crescer?)")
    parser.add_argument("--dias", type=int, default=saude_dados.DIAS)
    parser.add_argument("--so-ver", action="store_true", help="Nao envia para o Telegram")
    args = parser.parse_args()
    criar_schema()
    with abrir_sessao() as s:
        texto = saude_dados.formatar(saude_dados.calcular(s, dias=args.dias))
    print(texto)
    if not args.so_ver:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)
        print("\n[OK] Enviado para o Telegram.")


if __name__ == "__main__":
    main()
