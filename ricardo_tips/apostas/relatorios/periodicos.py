"""Relatorios periodicos — M15 (semanal) + M22 (mensal).

Logica pura, sem I/O escondido. Cada funcao recebe os dados em memoria
(linhas de CSV, apostas da BD) e devolve dataclasses prontos a formatar.
Os scripts em `scripts/relatorio_*.py` fazem o I/O (ler CSV, consultar
BD, imprimir ou enviar via Telegram).

M15 — edge × volume por liga/mercado na ultima semana:
  Mostra onde a conta tem valor acumulado (muitos alertas × EV decente)
  e onde o volume aparece sem edge (candidato a desligar).

M22 — crescimento da banca mes a mes:
  Lucro, ROI, win-rate, streak, split live vs pre-match. Compara com o
  mes anterior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable


# ═════════════════════════ M15 — Edge × Volume semanal ════════════════

@dataclass
class LinhaEdgeVolume:
    """Agregado de 1 (liga, mercado) na janela do relatorio."""
    liga: str
    mercado: str
    n_alertas: int
    edge_medio: float          # EV medio em fraccao (0.07 = +7%)
    edge_x_volume: float       # edge_medio × n_alertas (score ranking)
    n_resolvidos: int
    roi: float | None          # % — None se n_resolvidos == 0
    clv_medio: float | None    # % — media CLV dos alertas resolvidos


@dataclass
class RelatorioSemanal:
    data_inicio: datetime
    data_fim: datetime
    linhas_top: list[LinhaEdgeVolume] = field(default_factory=list)
    linhas_bottom: list[LinhaEdgeVolume] = field(default_factory=list)
    total_alertas: int = 0
    total_resolvidos: int = 0
    roi_global: float | None = None  # %
    edge_medio_global: float = 0.0   # fraccao


def _parse_float(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _parse_dt_csv(x: str) -> datetime | None:
    """Aceita formatos comuns do CSV (ts_scan sai como ISO)."""
    if not x:
        return None
    try:
        return datetime.fromisoformat(x.replace("Z", ""))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(x, fmt)
        except ValueError:
            continue
    return None


def agregar_edge_volume(
    rows: Iterable[dict],
    *,
    data_inicio: datetime,
    data_fim: datetime,
    top_n: int = 15,
    bottom_n: int = 5,
) -> RelatorioSemanal:
    """Agrega linhas do CSV live por (liga, mercado) na janela [inicio, fim).

    `rows` sao as linhas tal como vem do csv.DictReader (strings). Linhas
    de sinais ANALISTA (ev vazio) sao ignoradas no edge_medio porque nao
    teem EV comparavel; contam no volume so se tiverem resultado.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for row in rows:
        ts = _parse_dt_csv(row.get("ts_scan") or "")
        if ts is None or ts < data_inicio or ts >= data_fim:
            continue
        liga = (row.get("liga") or "?").strip()
        mercado = (row.get("mercado") or "?").strip().lower()
        chave = (liga, mercado)
        b = buckets.setdefault(chave, {
            "n": 0, "evs": [], "lucros": [], "resolvidos": 0, "clvs": [],
        })
        b["n"] += 1
        ev = _parse_float(row.get("ev"))
        if ev is not None:
            b["evs"].append(ev)
        resultado = (row.get("resultado") or "").strip()
        if resultado in ("W", "L", "push"):
            b["resolvidos"] += 1
            lucro = _parse_float(row.get("lucro"))
            if lucro is not None:
                b["lucros"].append(lucro)
            clv = _parse_float(row.get("clv"))
            if clv is not None:
                b["clvs"].append(clv)

    linhas: list[LinhaEdgeVolume] = []
    for (liga, mercado), b in buckets.items():
        edge_medio = sum(b["evs"]) / len(b["evs"]) if b["evs"] else 0.0
        roi = (sum(b["lucros"]) / len(b["lucros"]) * 100) if b["lucros"] else None
        # CSV guarda CLV como fracao (0.03). Expoe em % para consistencia M16.
        clv_medio = (sum(b["clvs"]) / len(b["clvs"]) * 100) if b["clvs"] else None
        linhas.append(LinhaEdgeVolume(
            liga=liga, mercado=mercado,
            n_alertas=b["n"], edge_medio=edge_medio,
            edge_x_volume=edge_medio * b["n"],
            n_resolvidos=b["resolvidos"], roi=roi, clv_medio=clv_medio,
        ))

    linhas.sort(key=lambda l: l.edge_x_volume, reverse=True)
    rel = RelatorioSemanal(
        data_inicio=data_inicio, data_fim=data_fim,
        linhas_top=linhas[:top_n],
        linhas_bottom=linhas[-bottom_n:][::-1] if len(linhas) > top_n else [],
    )
    rel.total_alertas = sum(l.n_alertas for l in linhas)
    rel.total_resolvidos = sum(l.n_resolvidos for l in linhas)
    todas_evs = [e for l in linhas for e in [l.edge_medio] * l.n_alertas if l.edge_medio]
    if todas_evs:
        rel.edge_medio_global = sum(todas_evs) / len(todas_evs)
    rois = [l.roi for l in linhas if l.roi is not None]
    if rois:
        rel.roi_global = sum(rois) / len(rois)
    return rel


def formatar_relatorio_semanal_telegram(rel: RelatorioSemanal) -> str:
    """Formato compacto para Telegram (<4096 chars)."""
    linhas = [
        "*📊 Relatório Semanal — Edge × Volume*",
        f"_{rel.data_inicio:%d/%m} → {rel.data_fim:%d/%m}_",
        "",
        f"Total alertas: *{rel.total_alertas}*  ·  resolvidos: {rel.total_resolvidos}",
        f"Edge médio: *{rel.edge_medio_global*100:+.1f}%*",
    ]
    if rel.roi_global is not None:
        sinal = "+" if rel.roi_global >= 0 else ""
        linhas.append(f"ROI médio por linha: *{sinal}{rel.roi_global:.1f}%*")
    linhas.append("")

    if rel.linhas_top:
        linhas.append("*🏆 Top edge × volume*")
        for i, l in enumerate(rel.linhas_top, 1):
            roi_s = ""
            if l.roi is not None:
                sinal = "+" if l.roi >= 0 else ""
                roi_s = f"  ROI {sinal}{l.roi:.0f}%"
            linhas.append(
                f"{i:>2}. {l.liga} · {l.mercado}  →  "
                f"{l.n_alertas} ×  edge {l.edge_medio*100:+.1f}%{roi_s}"
            )

    if rel.linhas_bottom:
        linhas.append("")
        linhas.append("*⚠ Bottom — rever*")
        for l in rel.linhas_bottom:
            roi_s = ""
            if l.roi is not None:
                sinal = "+" if l.roi >= 0 else ""
                roi_s = f"  ROI {sinal}{l.roi:.0f}%"
            linhas.append(
                f"• {l.liga} · {l.mercado}  →  "
                f"{l.n_alertas} ×  edge {l.edge_medio*100:+.1f}%{roi_s}"
            )

    if not rel.linhas_top:
        linhas.append("_Sem alertas registados nesta janela._")

    return "\n".join(linhas)


# ═════════════════════════ M22 — Crescimento mensal da banca ══════════

@dataclass
class ApostaResumo:
    """Vista mínima de uma aposta para o relatório mensal."""
    criado_em: datetime
    stake: float
    odd_executada: float
    resultado: str | None  # pendente | W | L | push | ganho | perdido | reembolso
    lucro: float | None
    fonte: str  # 'pre_match' | 'live'


@dataclass
class RelatorioMensal:
    ano: int
    mes: int
    data_inicio: datetime
    data_fim: datetime

    # Apostas no mes
    n_apostas: int = 0
    n_live: int = 0
    n_pre_match: int = 0
    n_pendentes: int = 0
    n_vitorias: int = 0
    n_derrotas: int = 0
    n_push: int = 0

    stake_total: float = 0.0
    lucro_total: float = 0.0
    roi: float | None = None       # %
    win_rate: float | None = None  # %

    # Banca
    saldo_inicio_mes: float | None = None
    saldo_fim_mes: float | None = None
    crescimento_pct: float | None = None  # vs saldo_inicio_mes

    # Comparacao com mes anterior
    lucro_mes_anterior: float | None = None
    delta_vs_anterior: float | None = None  # lucro - lucro_mes_anterior

    # Streak atual
    streak_tipo: str = ""   # 'W' | 'L' | ''
    streak_tamanho: int = 0

    # Dias
    dias_vencedores: int = 0
    dias_perdedores: int = 0


def _normalizar_resultado(r: str | None) -> str:
    """Mapeia 'ganho'/'perdido'/'reembolso' (ApostaReal) e 'W'/'L'/'push'
    (ApostaRealLive) para um alfabeto comum: W | L | push | pendente."""
    if not r:
        return "pendente"
    r = r.lower()
    if r in ("w", "ganho"):
        return "W"
    if r in ("l", "perdido"):
        return "L"
    if r in ("push", "reembolso"):
        return "push"
    return "pendente"


def _lucro_dia(apostas: list[ApostaResumo]) -> dict[datetime, float]:
    """Soma lucros por dia (apenas apostas resolvidas)."""
    por_dia: dict[datetime, float] = {}
    for a in apostas:
        if a.lucro is None or a.resultado in (None, "pendente"):
            continue
        dia = datetime(a.criado_em.year, a.criado_em.month, a.criado_em.day)
        por_dia[dia] = por_dia.get(dia, 0.0) + a.lucro
    return por_dia


def _streak_de(apostas: list[ApostaResumo]) -> tuple[str, int]:
    """Streak atual (mais recente para tras). Ignora push e pendente."""
    resolvidas = [a for a in apostas if _normalizar_resultado(a.resultado) in ("W", "L")]
    resolvidas.sort(key=lambda a: a.criado_em, reverse=True)
    if not resolvidas:
        return "", 0
    tipo = _normalizar_resultado(resolvidas[0].resultado)
    tamanho = 0
    for a in resolvidas:
        if _normalizar_resultado(a.resultado) == tipo:
            tamanho += 1
        else:
            break
    return tipo, tamanho


def gerar_relatorio_mensal(
    *,
    ano: int,
    mes: int,
    apostas_mes: list[ApostaResumo],
    apostas_mes_anterior: list[ApostaResumo] | None = None,
    saldo_inicio_mes: float | None = None,
    saldo_fim_mes: float | None = None,
) -> RelatorioMensal:
    """Produz o relatorio mensal a partir das apostas do mes.

    Os `apostas_*` vem ja filtrados pelo chamador (que faz a query a BD
    ou le o CSV). O relatorio nao faz I/O.
    """
    data_inicio = datetime(ano, mes, 1)
    if mes == 12:
        data_fim = datetime(ano + 1, 1, 1)
    else:
        data_fim = datetime(ano, mes + 1, 1)

    rel = RelatorioMensal(
        ano=ano, mes=mes,
        data_inicio=data_inicio, data_fim=data_fim,
        saldo_inicio_mes=saldo_inicio_mes,
        saldo_fim_mes=saldo_fim_mes,
    )

    rel.n_apostas = len(apostas_mes)
    rel.n_live = sum(1 for a in apostas_mes if a.fonte == "live")
    rel.n_pre_match = sum(1 for a in apostas_mes if a.fonte == "pre_match")

    for a in apostas_mes:
        res = _normalizar_resultado(a.resultado)
        if res == "W":
            rel.n_vitorias += 1
        elif res == "L":
            rel.n_derrotas += 1
        elif res == "push":
            rel.n_push += 1
        else:
            rel.n_pendentes += 1

        rel.stake_total += a.stake
        if a.lucro is not None and res != "pendente":
            rel.lucro_total += a.lucro

    stake_resolvido = sum(
        a.stake for a in apostas_mes
        if _normalizar_resultado(a.resultado) in ("W", "L", "push")
    )
    if stake_resolvido > 0:
        rel.roi = rel.lucro_total / stake_resolvido * 100
    n_decididas = rel.n_vitorias + rel.n_derrotas  # ignora push
    if n_decididas > 0:
        rel.win_rate = rel.n_vitorias / n_decididas * 100

    por_dia = _lucro_dia(apostas_mes)
    rel.dias_vencedores = sum(1 for v in por_dia.values() if v > 0)
    rel.dias_perdedores = sum(1 for v in por_dia.values() if v < 0)

    rel.streak_tipo, rel.streak_tamanho = _streak_de(apostas_mes)

    if saldo_inicio_mes is not None and saldo_fim_mes is not None and saldo_inicio_mes > 0:
        rel.crescimento_pct = (saldo_fim_mes / saldo_inicio_mes - 1) * 100

    if apostas_mes_anterior:
        lucro_ant = sum(
            a.lucro for a in apostas_mes_anterior
            if a.lucro is not None and _normalizar_resultado(a.resultado) != "pendente"
        )
        rel.lucro_mes_anterior = lucro_ant
        rel.delta_vs_anterior = rel.lucro_total - lucro_ant

    return rel


_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def formatar_relatorio_mensal_telegram(rel: RelatorioMensal) -> str:
    mes_nome = _MESES_PT.get(rel.mes, str(rel.mes))
    linhas = [
        f"*📅 Relatório Mensal — {mes_nome} {rel.ano}*",
        "",
    ]
    if rel.saldo_inicio_mes is not None and rel.saldo_fim_mes is not None:
        sinal = "+" if (rel.crescimento_pct or 0) >= 0 else ""
        linhas.append(
            f"💰 Banca: {rel.saldo_inicio_mes:.2f} → *{rel.saldo_fim_mes:.2f}* CHF  "
            f"({sinal}{rel.crescimento_pct:.1f}%)"
        )
    sinal_lucro = "+" if rel.lucro_total >= 0 else ""
    linhas.append(f"📈 Lucro mês: *{sinal_lucro}{rel.lucro_total:.2f}* un")
    if rel.roi is not None:
        sinal_roi = "+" if rel.roi >= 0 else ""
        linhas.append(f"🎯 ROI: *{sinal_roi}{rel.roi:.1f}%*  (stake {rel.stake_total:.1f} un)")
    if rel.win_rate is not None:
        linhas.append(
            f"✅ Win rate: *{rel.win_rate:.1f}%*  "
            f"({rel.n_vitorias}W / {rel.n_derrotas}L / {rel.n_push}P)"
        )
    linhas.append("")
    linhas.append(
        f"🧾 Apostas: {rel.n_apostas}  "
        f"(live {rel.n_live} · pre {rel.n_pre_match} · pendentes {rel.n_pendentes})"
    )
    linhas.append(
        f"📆 Dias: {rel.dias_vencedores}🟢 / {rel.dias_perdedores}🔴"
    )

    if rel.streak_tamanho >= 2:
        emoji = "🔥" if rel.streak_tipo == "W" else "❄"
        linhas.append(f"{emoji} Streak atual: {rel.streak_tamanho}× {rel.streak_tipo}")

    if rel.lucro_mes_anterior is not None:
        delta = rel.delta_vs_anterior or 0.0
        sinal = "+" if delta >= 0 else ""
        linhas.append(
            f"\n_vs mês anterior: {rel.lucro_mes_anterior:+.2f} → "
            f"{rel.lucro_total:+.2f}  ({sinal}{delta:.2f})_"
        )

    return "\n".join(linhas)
