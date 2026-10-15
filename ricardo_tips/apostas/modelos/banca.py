"""Gestao de banca simples (M13 adaptado para user: 50 CHF, 5 CHF flat).

Fluxo:
  1. BancaRegisto grava snapshot diario (via /registar ou automatico)
  2. perda_dia(data) soma lucro das sugestoes do dia
  3. passa_stop_loss() decide se podemos emitir mais sinais hoje
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from apostas.modelos.melhorias import passa_stop_loss as _pode_apostar
from apostas.utils.db import abrir_sessao
from apostas.utils.schema import ApostaReal, BancaRegisto, Sugestao


BANCA_INICIAL_CHF_DEFAULT = 50.0
STAKE_FLAT_CHF_DEFAULT = 5.0
STOP_LOSS_DIA_PCT = 0.05   # 5% da banca
STOP_LOSS_SEMANA_PCT = 0.10  # 10% da banca


@dataclass
class EstadoBanca:
    saldo_atual: float
    perda_dia: float
    perda_semana: float
    apostas_abertas_num: int
    apostas_abertas_stake: float
    pode_apostar: bool
    motivo: str


def _data_truncada(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day)


def saldo_actual() -> float:
    """Ultimo saldo registado em BancaRegisto; se vazio, devolve default."""
    with abrir_sessao() as s:
        ultimo = s.scalars(
            select(BancaRegisto).order_by(BancaRegisto.data.desc()).limit(1)
        ).first()
        if ultimo:
            return ultimo.saldo_chf
    return BANCA_INICIAL_CHF_DEFAULT


def _soma_lucro_desde(desde: datetime) -> tuple[float, int, float]:
    """Soma lucro realizado + conta apostas abertas desde `desde`.

    Devolve (lucro_realizado, num_abertas, stake_abertas).
    """
    with abrir_sessao() as s:
        lucro = s.scalar(
            select(func.coalesce(func.sum(ApostaReal.lucro), 0.0))
            .where(ApostaReal.criado_em >= desde)
            .where(ApostaReal.lucro.isnot(None))
        ) or 0.0
        abertas = s.scalars(
            select(ApostaReal)
            .where(ApostaReal.criado_em >= desde)
            .where(ApostaReal.resultado == "pendente")
        ).all()
        num = len(abertas)
        stake = sum(a.stake for a in abertas)
    return float(lucro), num, float(stake)


def estado_banca_actual() -> EstadoBanca:
    """Snapshot do estado da banca para decisao pre-sinal."""
    saldo = saldo_actual()
    hoje = _data_truncada(datetime.utcnow())
    semana_atras = hoje - timedelta(days=7)

    lucro_dia, abertas_num, abertas_stake = _soma_lucro_desde(hoje)
    lucro_semana, _, _ = _soma_lucro_desde(semana_atras)

    perda_dia = max(0.0, -lucro_dia)
    perda_semana = max(0.0, -lucro_semana)

    pode, motivo = _pode_apostar(
        perda_dia=perda_dia,
        perda_semana=perda_semana,
        banca_atual=saldo,
        limite_dia_pct=STOP_LOSS_DIA_PCT,
        limite_semana_pct=STOP_LOSS_SEMANA_PCT,
    )

    return EstadoBanca(
        saldo_atual=saldo,
        perda_dia=perda_dia,
        perda_semana=perda_semana,
        apostas_abertas_num=abertas_num,
        apostas_abertas_stake=abertas_stake,
        pode_apostar=pode,
        motivo=motivo,
    )


def gravar_snapshot_hoje() -> int:
    """Grava snapshot diario em BancaRegisto. Chamado no fim do dia / inicio."""
    estado = estado_banca_actual()
    hoje = _data_truncada(datetime.utcnow())
    with abrir_sessao() as s:
        ja_existe = s.scalars(
            select(BancaRegisto).where(BancaRegisto.data == hoje)
        ).first()
        if ja_existe:
            ja_existe.saldo_chf = estado.saldo_atual
            ja_existe.apostas_abertas_num = estado.apostas_abertas_num
            ja_existe.apostas_abertas_stake = estado.apostas_abertas_stake
            ja_existe.lucro_dia = -estado.perda_dia if estado.perda_dia else 0.0
            ja_existe.stop_loss_atingido = 0 if estado.pode_apostar else 1
            s.commit()
            return ja_existe.id
        novo = BancaRegisto(
            data=hoje,
            saldo_chf=estado.saldo_atual,
            apostas_abertas_num=estado.apostas_abertas_num,
            apostas_abertas_stake=estado.apostas_abertas_stake,
            lucro_dia=-estado.perda_dia if estado.perda_dia else 0.0,
            stop_loss_atingido=0 if estado.pode_apostar else 1,
        )
        s.add(novo)
        s.commit()
        return novo.id
