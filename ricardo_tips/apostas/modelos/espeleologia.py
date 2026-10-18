"""M20 — Espeleologia: post-mortem de alertas L (perdidos).

Fluxo:
  1. `ingerir_falhas_do_csv(csv_path)` le sugestoes_live_v2.csv, apanha
     linhas com resultado='L', e grava uma linha em `FalhaAlerta`.
     Idempotente — alert_id unico.
  2. `agregar_padroes()` conta falhas por (mercado, lado, faixa_minuto)
     e devolve padroes dominantes.
  3. `classificar_falha(row, categoria, nota)` permite o user marcar
     a razao (via CLI ou proxima iteracao do bot).

Nao substitui o resolver. Corre depois dele: resolver fecha W/L no CSV,
e isto trata so dos L para extrair licoes.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import FalhaAlerta

log = get_logger(__name__)


# ─────────── Faixas de minuto para agrupamento ──────────────────────

def _faixa_minuto(m: int | None) -> str:
    """Agrupa minutos em faixas analiticas (nao decimais arbitrarios)."""
    if m is None:
        return "?"
    if m < 15:
        return "0-15"
    if m < 30:
        return "15-30"
    if m < 45:
        return "30-45"
    if m < 60:
        return "45-60"
    if m < 75:
        return "60-75"
    if m < 85:
        return "75-85"
    return "85+"


# ─────────── Ingestao do CSV live ──────────────────────────────────

def _int_or_none(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _float_or_none(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ingerir_falhas_do_csv(csv_path: Path) -> int:
    """Le csv_path e grava FalhaAlerta para cada linha com resultado='L'.

    Idempotente — se o alert_id ja existe em FalhaAlerta, salta.
    Devolve n_adicionadas.
    """
    if not csv_path.exists():
        return 0

    with csv_path.open(encoding="utf-8") as fp:
        linhas = list(csv.DictReader(fp))

    ls = [l for l in linhas if (l.get("resultado") or "") == "L"]
    if not ls:
        return 0

    adicionadas = 0
    with abrir_sessao() as s:
        ja_existentes = set(s.scalars(select(FalhaAlerta.alert_id)).all())
        for row in ls:
            aid = _int_or_none(row.get("alert_id"))
            if aid is None or aid in ja_existentes:
                continue
            s.add(FalhaAlerta(
                alert_id=aid,
                event_id=_int_or_none(row.get("event_id")) or 0,
                liga=(row.get("liga") or "")[:80],
                mercado=(row.get("mercado") or "")[:30],
                lado=(row.get("lado") or "")[:20],
                linha=(row.get("linha") or None),
                minuto=_int_or_none(row.get("minuto")),
                prob_modelo=_float_or_none(row.get("prob_modelo")),
                ev=_float_or_none(row.get("ev")),
                marcador_no_alerta=(row.get("marcador") or None),
            ))
            adicionadas += 1
    return adicionadas


# ─────────── Agregacao de padroes ───────────────────────────────────

@dataclass
class PadraoFalha:
    """Padrao agregado de falha."""
    mercado: str
    lado: str
    faixa_minuto: str
    n: int
    prob_modelo_media: float | None
    ev_medio: float | None


def agregar_padroes(min_ocorrencias: int = 3) -> list[PadraoFalha]:
    """Agrupa FalhaAlerta por (mercado, lado, faixa_minuto).

    Devolve padroes com >= min_ocorrencias, ordenados por volume desc.
    """
    buckets: dict[tuple[str, str, str], dict] = {}
    with abrir_sessao() as s:
        rows = s.scalars(select(FalhaAlerta)).all()
        for r in rows:
            chave = (r.mercado, r.lado, _faixa_minuto(r.minuto))
            b = buckets.setdefault(chave, {"n": 0, "probs": [], "evs": []})
            b["n"] += 1
            if r.prob_modelo is not None:
                b["probs"].append(r.prob_modelo)
            if r.ev is not None:
                b["evs"].append(r.ev)

    padroes: list[PadraoFalha] = []
    for (mercado, lado, faixa), b in buckets.items():
        if b["n"] < min_ocorrencias:
            continue
        pm = sum(b["probs"]) / len(b["probs"]) if b["probs"] else None
        ev = sum(b["evs"]) / len(b["evs"]) if b["evs"] else None
        padroes.append(PadraoFalha(
            mercado=mercado, lado=lado, faixa_minuto=faixa,
            n=b["n"], prob_modelo_media=pm, ev_medio=ev,
        ))
    padroes.sort(key=lambda p: p.n, reverse=True)
    return padroes


def classificar_falha(alert_id: int, categoria: str, nota: str = "") -> bool:
    """Marca uma falha com a sua categoria + nota livre.

    Devolve True se encontrou e actualizou, False caso contrario.
    """
    with abrir_sessao() as s:
        row = s.scalar(select(FalhaAlerta).where(FalhaAlerta.alert_id == alert_id))
        if row is None:
            return False
        row.categoria = (categoria or "")[:50]
        if nota:
            row.nota = nota
    return True


def formatar_relatorio_padroes(padroes: list[PadraoFalha]) -> str:
    """Produz texto compact para terminal/Telegram."""
    if not padroes:
        return "_Sem padrões de falha com volume suficiente ainda._"
    linhas = ["*🔦 Espeleologia — padrões de falha*", ""]
    for p in padroes[:15]:
        extras = []
        if p.prob_modelo_media is not None:
            extras.append(f"p̄ {p.prob_modelo_media*100:.0f}%")
        if p.ev_medio is not None:
            extras.append(f"ev̄ {p.ev_medio*100:+.1f}%")
        extras_str = f"  ·  {' · '.join(extras)}" if extras else ""
        linhas.append(
            f"• {p.mercado} {p.lado} [{p.faixa_minuto}]  →  "
            f"*{p.n}× falha*{extras_str}"
        )
    return "\n".join(linhas)
