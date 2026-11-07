"""Relatorio de calibracao: o modelo acerta tanto quanto promete?

Le dados/tracking/sugestoes_live_v2.csv (alertas ja resolvidos pelo
resolver_sugestoes_live.py) e compara probabilidade prevista vs taxa real.

Uso:
  venv\\Scripts\\python.exe scripts\\relatorio_calibracao.py
  venv\\Scripts\\python.exe scripts\\relatorio_calibracao.py --desde 2026-10-01 --telegram
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.relatorios.calibracao_alertas import (  # noqa: E402
    carregar_resolvidos, formatar_relatorio,
)
from apostas.utils.config import project_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=None,
                        help="CSV de alertas (default: dados/tracking/sugestoes_live_v2.csv)")
    parser.add_argument("--desde", default=None, help="So alertas desde YYYY-MM-DD")
    parser.add_argument("--min-n", type=int, default=10,
                        help="Minimo de alertas por mercado para entrar na tabela")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    csv_path = (Path(args.csv) if args.csv
                else project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv")
    desde = datetime.strptime(args.desde, "%Y-%m-%d") if args.desde else None

    texto = formatar_relatorio(carregar_resolvidos(csv_path, desde=desde),
                               min_n_mercado=args.min_n)
    try:
        from apostas.modelos.correcao_projecoes import (
            formatar_resumo, pares_projecao_real, treinar_correcoes,
        )
        from apostas.utils.db import abrir_sessao
        with abrir_sessao() as sess:
            texto += "\n\n" + formatar_resumo(treinar_correcoes(pares_projecao_real(sess)))
    except Exception as exc:  # noqa: BLE001
        texto += f"\n\n(projeccoes: nao consegui ler a BD — {exc})"
    from apostas.modelos import correcao_ritmo
    ritmo = correcao_ritmo.ler()
    texto += "\n\n" + (correcao_ritmo.formatar(ritmo) if ritmo.avaliacao else
                         "*Ritmo do resto do jogo*: ainda nao calculado "
                         "(corre scripts\\correcao_ritmo.py ou espera pelo incremento das 00:15)")
    print(texto)

    if args.telegram:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)
        print("\n[OK] Enviado para Telegram.")


if __name__ == "__main__":
    main()
