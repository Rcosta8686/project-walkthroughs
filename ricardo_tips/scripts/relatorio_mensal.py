"""Relatorio mensal — M22 crescimento da banca.

Agrega `ApostaReal` (pre-match) + `ApostaRealLive` (live) do mes e
mostra lucro, ROI, win-rate, streak, dias verdes/vermelhos, crescimento
vs mes anterior. Banca (BancaRegisto) e opcional — mostra crescimento %
se o user tiver vindo a gravar o saldo diario.

Uso:
    python scripts/relatorio_mensal.py                     # mes atual
    python scripts/relatorio_mensal.py --mes 10 --ano 2026 # mes especifico
    python scripts/relatorio_mensal.py --telegram          # envia
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from apostas.relatorios.periodicos import (  # noqa: E402
    ApostaResumo,
    formatar_relatorio_mensal_telegram,
    gerar_relatorio_mensal,
)
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import (  # noqa: E402
    ApostaReal, ApostaRealLive, BancaRegisto,
)


def _carregar_apostas(data_inicio: datetime, data_fim: datetime) -> list[ApostaResumo]:
    """Le ApostaReal + ApostaRealLive entre [data_inicio, data_fim)."""
    apostas: list[ApostaResumo] = []
    with abrir_sessao() as s:
        pre = s.scalars(
            select(ApostaReal).where(
                ApostaReal.criado_em >= data_inicio,
                ApostaReal.criado_em < data_fim,
            )
        ).all()
        for a in pre:
            apostas.append(ApostaResumo(
                criado_em=a.criado_em,
                stake=a.stake,
                odd_executada=a.odd_executada,
                resultado=a.resultado,
                lucro=a.lucro,
                fonte="pre_match",
            ))
        live = s.scalars(
            select(ApostaRealLive).where(
                ApostaRealLive.criado_em >= data_inicio,
                ApostaRealLive.criado_em < data_fim,
            )
        ).all()
        for a in live:
            apostas.append(ApostaResumo(
                criado_em=a.criado_em,
                stake=a.stake,
                odd_executada=a.odd_executada,
                resultado=a.resultado,
                lucro=a.lucro,
                fonte="live",
            ))
    return apostas


def _saldo_na_data(data: datetime) -> float | None:
    """Devolve o saldo mais proximo (<=) data, ou None."""
    with abrir_sessao() as s:
        reg = s.scalars(
            select(BancaRegisto)
            .where(BancaRegisto.data <= data)
            .order_by(BancaRegisto.data.desc())
            .limit(1)
        ).first()
        return reg.saldo_chf if reg else None


def main() -> None:
    agora = datetime.utcnow()
    parser = argparse.ArgumentParser(description="Relatorio mensal M22.")
    parser.add_argument("--mes", type=int, default=agora.month)
    parser.add_argument("--ano", type=int, default=agora.year)
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    inicio = datetime(args.ano, args.mes, 1)
    fim = datetime(args.ano + 1, 1, 1) if args.mes == 12 else datetime(args.ano, args.mes + 1, 1)
    # Mes anterior
    if args.mes == 1:
        ant_mes, ant_ano = 12, args.ano - 1
    else:
        ant_mes, ant_ano = args.mes - 1, args.ano
    inicio_ant = datetime(ant_ano, ant_mes, 1)

    apostas_mes = _carregar_apostas(inicio, fim)
    apostas_ant = _carregar_apostas(inicio_ant, inicio)
    saldo_inicio = _saldo_na_data(inicio)
    saldo_fim = _saldo_na_data(min(fim, agora))

    rel = gerar_relatorio_mensal(
        ano=args.ano, mes=args.mes,
        apostas_mes=apostas_mes,
        apostas_mes_anterior=apostas_ant,
        saldo_inicio_mes=saldo_inicio,
        saldo_fim_mes=saldo_fim,
    )
    texto = formatar_relatorio_mensal_telegram(rel)
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
