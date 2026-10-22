"""Relatorio de performance do mega-cerebro.

Agrega SinalMegaCerebro por heuristica para dizer qual acerta e qual
falha. So entra no relatorio semanal/mensal.

Devolve texto Telegram-ready + lista de dicts para debugging.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from apostas.utils.tempo import agora_utc


@dataclass
class LinhaRelatorioMega:
    heuristica: str
    n_total: int
    n_acerto: int
    n_falha: int
    taxa_acerto: float   # 0.0-1.0


def agregar_por_heuristica(
    sinais: Iterable, agora: datetime | None = None, dias: int = 7,
) -> list[LinhaRelatorioMega]:
    """Dado um iteravel de SinalMegaCerebro, agrupa por heuristica e
    devolve lista ordenada por n_total desc."""
    agora = agora or agora_utc()
    inicio = agora - timedelta(days=dias)
    buckets: dict[str, dict] = {}
    for s in sinais:
        if s.criado_em < inicio:
            continue
        if s.resolvido not in ("acerto", "falha"):
            continue
        b = buckets.setdefault(s.heuristica, {"total": 0, "acerto": 0,
                                                "falha": 0})
        b["total"] += 1
        if s.resolvido == "acerto":
            b["acerto"] += 1
        else:
            b["falha"] += 1
    linhas = []
    for h, b in buckets.items():
        taxa = b["acerto"] / b["total"] if b["total"] else 0.0
        linhas.append(LinhaRelatorioMega(
            heuristica=h, n_total=b["total"],
            n_acerto=b["acerto"], n_falha=b["falha"],
            taxa_acerto=taxa,
        ))
    linhas.sort(key=lambda l: -l.n_total)
    return linhas


def formatar_telegram(linhas: list[LinhaRelatorioMega], dias: int = 7) -> str:
    """Mensagem Telegram-ready. Vazio se nao ha sinais nos ultimos N dias."""
    if not linhas:
        return ""
    out = [f"🧠 *Mega-cerebro* (ultimos {dias}d)"]
    for l in linhas:
        pct = f"{l.taxa_acerto * 100:.0f}%"
        bar = "✅" if l.taxa_acerto >= 0.6 else ("⚠" if l.taxa_acerto >= 0.4 else "❌")
        out.append(
            f"  {bar} {l.heuristica}: {l.n_acerto}/{l.n_total} ({pct})"
        )
    return "\n".join(out)


def obter_relatorio_semanal(dias: int = 7) -> str:
    """Puxa SinalMegaCerebro da BD e devolve texto formatado."""
    try:
        from sqlalchemy import select
        from apostas.utils.db import abrir_sessao
        from apostas.utils.schema import SinalMegaCerebro
    except Exception:  # noqa: BLE001
        return ""
    try:
        with abrir_sessao() as sess:
            inicio = agora_utc() - timedelta(days=dias)
            rows = sess.execute(
                select(SinalMegaCerebro).where(
                    SinalMegaCerebro.criado_em >= inicio
                )
            ).scalars().all()
            linhas = agregar_por_heuristica(rows, dias=dias)
            return formatar_telegram(linhas, dias=dias)
    except Exception:  # noqa: BLE001
        return ""
