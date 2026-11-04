"""Resultados do dia: o que as dicas enviadas ao Telegram deram, uma a uma.

1. Resolve os alertas do dia (resultado final, W/L, lucro) e grava as
   estatisticas finais dos jogos na BD — alimenta as fichas das equipas e a
   correccao das projeccoes (o mesmo que o resolver agendado faz, mas so
   para o dia, por isso e rapido).
2. Mostra o resumo e a lista das dicas enviadas nesse dia.

Uso:
  venv\\Scripts\\python.exe scripts\\relatorio_dia.py                 # hoje
  venv\\Scripts\\python.exe scripts\\relatorio_dia.py --dia 2026-11-04
  venv\\Scripts\\python.exe scripts\\relatorio_dia.py --sem-resolver   # so mostra
  venv\\Scripts\\python.exe scripts\\relatorio_dia.py --telegram
Dependencias: flaresolverr a correr (para resolver).
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

from apostas.relatorios import alertas_enviados  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402


def _nomes_jogos(sess, ids: set[int]) -> dict[int, tuple[str, str]]:
    from sqlalchemy import select

    from apostas.utils.schema import ProjecaoLive
    if not ids:
        return {}
    rows = sess.execute(select(ProjecaoLive.sofascore_event_id, ProjecaoLive.nome_casa,
                               ProjecaoLive.nome_fora)
                        .where(ProjecaoLive.sofascore_event_id.in_(ids))).all()
    return {int(e): (h, a) for e, h, a in rows}


def texto_do_dia(inicio: datetime) -> str:
    fim = inicio + timedelta(days=1)
    csv_sug = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    chaves = alertas_enviados.projeccoes_no_periodo(inicio, fim)
    totais: dict = {}
    nomes: dict = {}
    if chaves:
        try:
            from apostas.utils.db import abrir_sessao
            with abrir_sessao() as s:
                totais = alertas_enviados.totais_finais_bd(s, chaves)
                nomes = _nomes_jogos(s, {e for e, _ in chaves})
        except Exception as exc:  # noqa: BLE001
            print(f"(BD indisponivel: {exc})")
    dicas = alertas_enviados.listar(inicio, fim, csv_sug, totais_proj=totais)
    resumo = alertas_enviados.resumir(inicio, fim, csv_sug, totais_proj=totais)
    partes = [f"*📆 Resultados de {inicio:%d/%m/%Y}* (horas em UTC)", "",
              alertas_enviados.formatar(resumo)]
    if dicas:
        partes += ["", alertas_enviados.formatar_lista(dicas, nomes)]
    return "\n".join(partes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resultados das dicas de um dia.")
    parser.add_argument("--dia", default=None, help="YYYY-MM-DD (default: hoje)")
    parser.add_argument("--sem-resolver", action="store_true",
                        help="Nao vai buscar resultados novos, so mostra")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    if args.dia:
        inicio = datetime.strptime(args.dia, "%Y-%m-%d")
    else:
        agora = agora_utc()
        inicio = datetime(agora.year, agora.month, agora.day)

    if not args.sem_resolver:
        import resolver_sugestoes_live
        print(f"A resolver os alertas desde {inicio:%d/%m} e a gravar estatisticas...\n")
        resolver_sugestoes_live.resolver(desde=inicio)
        print()

    texto = texto_do_dia(inicio)
    print(texto)
    if args.telegram:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)
        print("\n[OK] Enviado para Telegram.")


if __name__ == "__main__":
    main()
