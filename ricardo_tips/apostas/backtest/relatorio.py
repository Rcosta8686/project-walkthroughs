"""Formatação do relatório de backtest em texto para o terminal."""

from __future__ import annotations

from collections import defaultdict

from apostas.backtest.walk_forward import RelatorioBacktest


def formatar(rel: RelatorioBacktest) -> str:
    """Devolve o relatório pronto a imprimir."""
    linhas = []

    linhas.append("═" * 70)
    linhas.append("  RELATÓRIO DE BACKTEST — Ricardo Tips")
    linhas.append("═" * 70)
    linhas.append("")
    linhas.append(f"  Mercado:              Over/Under {rel.linha} golos")
    linhas.append(f"  Época de teste:       {rel.epoca_teste}")
    linhas.append(f"  Casa de apostas:      {rel.casa_de_apostas_ref}")
    linhas.append(f"  EV mínimo:            {rel.ev_minimo * 100:.1f}%")
    linhas.append(f"  Meia-vida do decaimento: {rel.meia_vida_dias:.0f} dias")
    linhas.append("")

    if rel.n_apostas == 0:
        linhas.append("  ⚠ Zero apostas sugeridas — nenhum jogo passou o limiar de EV.")
        linhas.append("    Tenta descer o EV mínimo (ex.: 0.01) ou verificar a ingestão.")
        linhas.append("")
        linhas.append("═" * 70)
        return "\n".join(linhas)

    linhas.append("  ── Resumo ─────────────────────────────────────────────────────")
    linhas.append(f"  Apostas:              {rel.n_apostas}")
    linhas.append(f"  Ganhas:               {rel.n_vitorias} ({rel.hit_rate * 100:.1f}%)")
    linhas.append(f"  Stake total:          {rel.stake_total:.2f} unidades")
    linhas.append(f"  Lucro:                {rel.lucro:+.2f} unidades")
    linhas.append(f"  ROI:                  {rel.roi * 100:+.2f}%")
    linhas.append(f"  Max drawdown:         {rel.max_drawdown:.2f} unidades")
    linhas.append("")

    # Por lado
    por_lado: dict[str, list] = defaultdict(list)
    for a in rel.apostas:
        por_lado[a.lado].append(a)
    linhas.append("  ── Por lado ──────────────────────────────────────────────────")
    for lado in ("over", "under"):
        apostas = por_lado.get(lado, [])
        if not apostas:
            continue
        stake = sum(a.stake for a in apostas)
        lucro = sum(a.lucro for a in apostas)
        ganhas = sum(1 for a in apostas if a.resultado == "ganho")
        roi = lucro / stake if stake else 0
        linhas.append(
            f"  {lado.capitalize():6s} | {len(apostas):4d} apostas | "
            f"{ganhas / len(apostas) * 100:5.1f}% ganhas | ROI {roi * 100:+6.2f}%"
        )
    linhas.append("")

    # Por liga
    por_liga: dict[int, list] = defaultdict(list)
    for a in rel.apostas:
        por_liga[a.liga_id].append(a)
    linhas.append("  ── Por liga ──────────────────────────────────────────────────")
    for lid in sorted(por_liga):
        apostas = por_liga[lid]
        stake = sum(a.stake for a in apostas)
        lucro = sum(a.lucro for a in apostas)
        roi = lucro / stake if stake else 0
        linhas.append(
            f"  Liga {lid:2d} | {len(apostas):3d} apostas | ROI {roi * 100:+6.2f}%"
        )
    linhas.append("")

    linhas.append("  ── Nota ──────────────────────────────────────────────────────")
    linhas.append("  Este é o modelo BASELINE (golos apenas). ROI próximo de -5%")
    linhas.append("  ou de zero é esperado — a vantagem real vem com GAP ratings")
    linhas.append("  de remates e cantos, implementadas quando a Sportmonks")
    linhas.append("  estiver ligada.")
    linhas.append("═" * 70)
    return "\n".join(linhas)
