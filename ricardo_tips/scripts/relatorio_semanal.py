"""Relatorio semanal — M15 edge × volume.

Agrupa `sugestoes_live_v2.csv` da ultima semana por (liga, mercado):
mostra top 15 por edge × volume e bottom 5 (candidatos a cortar).

Uso:
    python scripts/relatorio_semanal.py                 # imprime texto
    python scripts/relatorio_semanal.py --telegram      # envia via bot
    python scripts/relatorio_semanal.py --dias 14       # altera janela
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas import filtro_desempenho  # noqa: E402
from apostas.alertas.historico_alertas import (  # noqa: E402
    agregar_por_mercado, carregar_perfis,
)
from apostas.relatorios.melhorias_sombra import (  # noqa: E402
    candidatos_a_reactivar, formatar_seccao_sombra,
)
from apostas.relatorios.periodicos import (  # noqa: E402
    agregar_edge_volume,
    formatar_relatorio_semanal_telegram,
)
from apostas.utils.config import project_root  # noqa: E402


def _csv_path() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _ler_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def main() -> None:
    parser = argparse.ArgumentParser(description="Relatorio semanal M15 edge × volume.")
    parser.add_argument("--dias", type=int, default=7,
                        help="Janela em dias (default 7).")
    parser.add_argument("--top", type=int, default=15,
                        help="N linhas top (default 15).")
    parser.add_argument("--bottom", type=int, default=5,
                        help="N linhas bottom (default 5).")
    parser.add_argument("--telegram", action="store_true",
                        help="Envia o relatorio via bot Telegram.")
    args = parser.parse_args()

    fim = datetime.utcnow()
    inicio = fim - timedelta(days=args.dias)
    rows = _ler_csv(_csv_path())
    rel = agregar_edge_volume(
        rows, data_inicio=inicio, data_fim=fim,
        top_n=args.top, bottom_n=args.bottom,
    )
    texto = formatar_relatorio_semanal_telegram(rel)

    # Seccao sombra: para cada (liga, mercado) actualmente bloqueado por
    # M23 (filtro de desempenho), mostra ROI das ultimas 2 semanas +
    # historico, e flag "candidato a reactivar" quando ROI recente >= 0.
    try:
        perfis = carregar_perfis(_csv_path())
        perfis_mercado = agregar_por_mercado(perfis) if perfis else {}
        bloqueios = filtro_desempenho.bloqueios_atuais(perfis_mercado)
        if bloqueios:
            trajectorias = candidatos_a_reactivar(rows, bloqueios, agora=fim)
            texto += "\n" + formatar_seccao_sombra(trajectorias)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] seccao sombra falhou: {exc}")

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
