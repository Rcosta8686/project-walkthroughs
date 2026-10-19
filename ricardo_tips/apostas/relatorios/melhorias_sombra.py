"""Modo sombra — tracking das combinacoes (liga, mercado) bloqueadas.

O scanner grava todos os alertas no CSV antes de decidir se envia para
Telegram (ver scanner_live_v2.py linha 1010 — `_gravar_novas` corre antes
do bloco `if args.telegram`). O resolver depois marca W/L para TODAS as
linhas. Portanto o historico continua a crescer para mercados bloqueados
pelo filtro de desempenho (M23), sem spam ao user.

Isto permite:
  1. Ver a trajectoria ROI de cada bloqueado nas ultimas N semanas.
  2. Detectar candidatos a reactivar quando o ROI recente >= 0.
  3. Validar mudancas ao modelo em "shadow mode" antes de ligar.

A integracao com o relatorio semanal (`scripts/relatorio_semanal.py`)
adiciona uma seccao "🔬 Em sombra" com os bloqueados e a sua tendencia.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from apostas.relatorios.periodicos import (
    _normalizar_ev, _parse_dt_csv, _parse_float,
)


# Minimo de alertas resolvidos nas 2 ultimas semanas para considerar
# o bloqueado como "candidato a reactivar". <10 e ruido.
MIN_RESOLVIDOS_CANDIDATO = 10


@dataclass
class TrajetoriaShadow:
    """ROI historico de 1 (liga, mercado) partido em janelas semanais."""
    liga: str
    mercado: str
    roi_global: float | None   # % — todo o historico (ja no perfil M23)
    n_global: int
    roi_recentes: list[float | None]  # ROI por semana [sem0, sem1, sem2, ...]
    n_recentes: list[int]              # n_resolvidos por semana
    candidato_reactivar: bool


def _agrupar_por_semana(rows: Iterable[dict], agora: datetime,
                        n_semanas: int) -> list[list[dict]]:
    """Separa linhas em N buckets de semana (semana 0 = mais recente)."""
    semanas: list[list[dict]] = [[] for _ in range(n_semanas)]
    for row in rows:
        ts = _parse_dt_csv(row.get("ts_scan") or "")
        if ts is None:
            continue
        delta_dias = (agora - ts).days
        if delta_dias < 0:
            continue
        idx = delta_dias // 7
        if 0 <= idx < n_semanas:
            semanas[idx].append(row)
    return semanas


def _roi_de_rows(rows: list[dict]) -> tuple[float | None, int]:
    """Para um lote de linhas CSV, devolve (ROI%, n_resolvidos)."""
    lucros: list[float] = []
    for row in rows:
        resultado = (row.get("resultado") or "").strip()
        if resultado not in ("W", "L", "push"):
            continue
        lucro = _parse_float(row.get("lucro"))
        if lucro is not None:
            lucros.append(lucro)
    if not lucros:
        return None, 0
    return sum(lucros) / len(lucros) * 100, len(lucros)


def trajetoria_bloqueado(
    rows: Iterable[dict],
    liga: str,
    mercado: str,
    *,
    agora: datetime,
    n_semanas: int = 4,
) -> TrajetoriaShadow:
    """Computa trajectoria semanal de 1 bloqueado.

    `rows` sao as linhas CSV. Filtra pela (liga, mercado) exacta
    (case-insensitive no mercado). Devolve uma TrajetoriaShadow.
    """
    liga_c = liga.strip()
    mercado_c = mercado.strip().lower()
    minhas = [
        r for r in rows
        if (r.get("liga") or "").strip() == liga_c
        and (r.get("mercado") or "").strip().lower() == mercado_c
    ]
    semanas = _agrupar_por_semana(minhas, agora, n_semanas)
    roi_recentes: list[float | None] = []
    n_recentes: list[int] = []
    for lote in semanas:
        r, n = _roi_de_rows(lote)
        roi_recentes.append(r)
        n_recentes.append(n)

    # Global: todas as resolvidas (sem janela)
    roi_global, n_global = _roi_de_rows(list(minhas))

    # Candidato a reactivar: ROI das 2 ultimas semanas >= 0, com volume
    # minimo (>=10 resolvidos combinados).
    recentes_2sem = [r for r in roi_recentes[:2] if r is not None]
    n_recentes_2sem = sum(n_recentes[:2])
    candidato = (
        n_recentes_2sem >= MIN_RESOLVIDOS_CANDIDATO
        and bool(recentes_2sem)
        and all(r >= 0 for r in recentes_2sem)
    )

    return TrajetoriaShadow(
        liga=liga, mercado=mercado,
        roi_global=roi_global, n_global=n_global,
        roi_recentes=roi_recentes, n_recentes=n_recentes,
        candidato_reactivar=candidato,
    )


def candidatos_a_reactivar(
    rows: Iterable[dict],
    bloqueios: list[tuple[str, str, float, int]],
    *,
    agora: datetime,
    n_semanas: int = 4,
) -> list[TrajetoriaShadow]:
    """Para cada (liga, mercado) actualmente bloqueado, computa trajectoria.

    `bloqueios` vem de `filtro_desempenho.bloqueios_atuais(perfis_mercado)`.
    Devolve lista das trajectorias, ordenada por candidato primeiro depois
    por ROI global crescente (os piores primeiro).
    """
    rows_list = list(rows)
    trajectorias: list[TrajetoriaShadow] = []
    for (liga, mercado, _roi, _n) in bloqueios:
        traj = trajetoria_bloqueado(
            rows_list, liga, mercado, agora=agora, n_semanas=n_semanas,
        )
        trajectorias.append(traj)
    trajectorias.sort(
        key=lambda t: (
            not t.candidato_reactivar,            # candidatos primeiro
            t.roi_global if t.roi_global is not None else 0,  # piores depois
        )
    )
    return trajectorias


def formatar_seccao_sombra(trajectorias: list[TrajetoriaShadow]) -> str:
    """Produz o texto da seccao '🔬 Em sombra' para o relatorio semanal."""
    if not trajectorias:
        return ""
    linhas = ["", "*🔬 Em sombra (bloqueados mas monitorizados)*"]
    for t in trajectorias[:10]:
        # Resumo recente: ultimas 2 semanas combinadas
        n_2sem = sum(t.n_recentes[:2])
        rois_2sem = [r for r in t.roi_recentes[:2] if r is not None]
        if rois_2sem and n_2sem > 0:
            # Media simples (ok para 2 pontos)
            roi_rec = sum(rois_2sem) / len(rois_2sem)
            sinal_rec = "+" if roi_rec >= 0 else ""
            rec_s = f"últ 2 sem {sinal_rec}{roi_rec:.0f}% (n={n_2sem})"
        else:
            rec_s = f"últ 2 sem sem dados"
        global_s = ""
        if t.roi_global is not None:
            sinal_g = "+" if t.roi_global >= 0 else ""
            global_s = f"hist {sinal_g}{t.roi_global:.0f}%"
        flag = " ⬆ *candidato*" if t.candidato_reactivar else ""
        linhas.append(
            f"• {t.liga} · {t.mercado}  →  {rec_s}  ·  {global_s}{flag}"
        )
    return "\n".join(linhas)
