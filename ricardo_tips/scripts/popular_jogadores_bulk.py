"""Popula EstatisticaJogadorJogo em bulk a partir dos jogos ja em
EstatisticaEquipaJogo, FILTRANDO POR EPOCA CORRENTE (default: ultimos 90 dias).

Fluxo:
  1. Le event_ids distintos de EstatisticaEquipaJogo com data_jogo nos
     ultimos --dias dias.
  2. Para cada event_id, chama historico_jogadores.gravar_jogadores_terminado().
  3. Idempotente — UniqueConstraint impede duplicados.

Uso:
    # Default: ultimos 90 dias (epoca corrente)
    python scripts/popular_jogadores_bulk.py

    # Ultimos 180 dias (mais historico)
    python scripts/popular_jogadores_bulk.py --dias 180

    # So uma liga
    python scripts/popular_jogadores_bulk.py --liga "Serie A"

Dependencia: flaresolverr. Demora ~45-90 min para epoca corrente das 11 ligas.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select  # noqa: E402

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.modelos import historico_jogadores  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo, EstatisticaJogadorJogo  # noqa: E402

log = get_logger(__name__)


def _event_ids_recentes(dias: int, liga: str | None) -> list[tuple[int, str, datetime]]:
    """Devolve (event_id, liga, data_jogo) distintos nos ultimos N dias."""
    limiar = datetime.utcnow() - timedelta(days=dias)
    with abrir_sessao() as s:
        q = select(
            EstatisticaEquipaJogo.sofascore_event_id,
            EstatisticaEquipaJogo.liga,
            EstatisticaEquipaJogo.data_jogo,
        ).where(
            EstatisticaEquipaJogo.data_jogo >= limiar
        ).distinct().order_by(EstatisticaEquipaJogo.data_jogo.desc())
        if liga:
            q = q.where(EstatisticaEquipaJogo.liga.like(f"%{liga}%"))
        rows = s.execute(q).all()
    # dedupe por event_id (podem haver 2 linhas por jogo)
    vistos: set[int] = set()
    out: list[tuple[int, str, datetime]] = []
    for eid, lig, data in rows:
        if eid in vistos:
            continue
        vistos.add(eid)
        out.append((eid, lig, data))
    return out


def _event_ids_ja_processados() -> set[int]:
    """Devolve set de event_ids que ja tem pelo menos 1 jogador gravado."""
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaJogadorJogo.sofascore_event_id).distinct()
        ).all()
    return set(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=90,
                        help="Ingerir jogadores de jogos nos ultimos N dias "
                             "(default 90 = epoca corrente).")
    parser.add_argument("--liga", default=None,
                        help="Filtrar por liga (match parcial no nome).")
    parser.add_argument("--max-jogos", type=int, default=None,
                        help="Limite de jogos a processar (debug).")
    parser.add_argument("--retomar", action="store_true",
                        help="Salta eventos que ja tem jogadores em "
                             "EstatisticaJogadorJogo. Util para re-correr "
                             "depois de uma falha a meio.")
    args = parser.parse_args()

    eventos = _event_ids_recentes(args.dias, args.liga)
    if args.retomar:
        ja_feitos = _event_ids_ja_processados()
        antes = len(eventos)
        eventos = [(eid, lig, data) for (eid, lig, data) in eventos
                   if eid not in ja_feitos]
        print(f"  [retomar] {antes - len(eventos)} eventos ja feitos → saltados")
    if args.max_jogos:
        eventos = eventos[: args.max_jogos]

    print(f"\n╔ Popular jogadores — {len(eventos)} jogos nos ultimos "
          f"{args.dias} dias ╗\n")

    total_linhas = 0
    total_processados = 0
    for i, (eid, liga, data) in enumerate(eventos, 1):
        try:
            n_add = historico_jogadores.gravar_jogadores_terminado(eid)
            total_linhas += n_add
            total_processados += 1
            if i % 10 == 0:
                print(f"  [{i}/{len(eventos)}] +{total_linhas} jogadores novos "
                      f"({total_processados} eventos processados)")
        except FlaresolverrIndisponivel:
            print("\n⚠ Flaresolverr OFF. docker start flaresolverr e re-corre.")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("event %s falhou: %s", eid, exc)
        time.sleep(0.4)  # 400ms entre eventos (3 chamadas por evento → ~1.5s/jogo)

    print(f"\n╔ TOTAL: {total_processados} eventos, +{total_linhas} jogadores novos ╗\n")


if __name__ == "__main__":
    main()
