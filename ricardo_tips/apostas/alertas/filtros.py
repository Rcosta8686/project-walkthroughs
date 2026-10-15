"""Filtro final M6 — 8 pontos que uma projeccao tem de passar para
virar sugestao de aposta.

Doc melhorias-scanner.md M6:
  1. EV >= limiar depois da mistura (3% inicial; mais alto em odds < 1.50)
  2. Fora das zonas fracas do modelo
  3. Dados validados (onze, stats recentes, odds recentes, sanity)
  4. Momento certo (abertura / janela onzes / intervalo)
  5. Limite da casa permite stake que valha a pena
  6. Cabe na carteira do jogo + teste stress do dia
  7. Se ao vivo: regras do M12
  8. Vale gastar "vida" da conta nesta casa
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContextoFiltro:
    """Entrada do filtro M6. Nem todos os campos sao obrigatorios —
    pontos nao preenchidos sao assumidos como OK."""
    # Ponto 1
    ev: float
    odd: float
    # Ponto 2
    mercado: str
    liga: str
    # Ponto 3
    dados_recentes: bool = True
    onze_confirmado: bool | None = None  # None = pre-match sem lineup
    # Ponto 4
    momento: str = "abertura"  # abertura | onzes | intervalo | live | tarde
    # Ponto 5
    limite_casa_chf: float | None = None  # None = desconhecido
    stake_pretendida_chf: float = 5.0
    # Ponto 6
    carteira_pior_perda_pct: float | None = None  # % banca
    stop_loss_hoje_atingido: bool = False
    # Ponto 7 (live)
    is_live: bool = False
    latencia_segundos: int | None = None
    sem_suspensao: bool = True
    # Ponto 8
    casa_limitada: bool = False
    ev_bom_noutra_casa: bool = False


@dataclass
class ResultadoFiltro:
    passou: bool
    pontos_falhados: list[str] = field(default_factory=list)
    pontos_avisos: list[str] = field(default_factory=list)

    def razao(self) -> str:
        if self.passou:
            return "OK"
        return "; ".join(self.pontos_falhados)


# ─────────────────────────── Zonas fracas ──────────────────────────────────
# Lista actualizada por aprender_jogos.py / calibracao historica.
# NOTA 2026-10-15: dados PL/Ligue 1 eram de LIVE scanner (ROI -X% com
# n>=100). Para PRE-MATCH ainda nao temos amostra suficiente — portanto
# NAO bloqueamos nenhuma liga pre-match. Zonas fracas so afectam live.
LIGAS_FRACAS_LIVE = frozenset({
    "brasileirao série a", "brasileirão série a",
    "liga portugal betclic",
})
LIGAS_FRACAS = LIGAS_FRACAS_LIVE  # mantido para compatibilidade com scanner
# 1X2 live tem ROI -27% (n=188), mas 1X2 pre-match ainda nao temos dados
MERCADOS_FRACOS_LIVE = frozenset({"1x2"})
MERCADOS_FRACOS = frozenset()  # pre-match: tudo passa


def _ev_min_para_odd(odd: float) -> float:
    """EV minimo exigido conforme odd. Odds baixas pedem mais margem
    (sao mais arriscadas para a banca)."""
    if odd < 1.50:
        return 0.05  # 5%
    if odd < 2.00:
        return 0.03
    return 0.03


def passa_filtros_m6(ctx: ContextoFiltro) -> ResultadoFiltro:
    """Avalia os 8 pontos. Devolve ResultadoFiltro com motivo."""
    falhados: list[str] = []
    avisos: list[str] = []

    # Ponto 1: EV minimo
    ev_min = _ev_min_para_odd(ctx.odd)
    if ctx.ev < ev_min:
        falhados.append(f"P1: EV {ctx.ev*100:.1f}% < min {ev_min*100:.1f}% (odd {ctx.odd:.2f})")

    # Ponto 2: zona fraca — SO em live, nao bloqueia pre-match
    liga_low = (ctx.liga or "").lower()
    if ctx.is_live:
        if any(liga_fraca in liga_low for liga_fraca in LIGAS_FRACAS_LIVE):
            falhados.append(f"P2: liga fraca live ({ctx.liga})")
        if ctx.mercado in MERCADOS_FRACOS_LIVE:
            falhados.append(f"P2: mercado fraco live ({ctx.mercado})")
    else:
        # Pre-match: zonas fracas live NAO aplicam (dados nao transferem)
        # Mantemos so o bloqueio de "brasileirao" que e consenso geral
        if "brasileirao" in liga_low or "brasileirão" in liga_low:
            falhados.append(f"P2: liga fraca ({ctx.liga})")

    # Ponto 3: dados validados
    if not ctx.dados_recentes:
        falhados.append("P3: dados nao recentes")
    if ctx.onze_confirmado is False:  # explicitamente nao-confirmado
        avisos.append("P3: onze nao confirmado — menor confianca")

    # Ponto 4: momento certo
    if ctx.momento == "tarde":
        falhados.append("P4: momento tardio demais (odds ja mexeram)")

    # Ponto 5: limite da casa
    if ctx.limite_casa_chf is not None and ctx.limite_casa_chf < ctx.stake_pretendida_chf:
        falhados.append(
            f"P5: limite casa {ctx.limite_casa_chf:.0f} CHF < stake {ctx.stake_pretendida_chf:.0f} CHF"
        )

    # Ponto 6: carteira / stop-loss
    if ctx.stop_loss_hoje_atingido:
        falhados.append("P6: stop-loss hoje ja atingido")
    if ctx.carteira_pior_perda_pct is not None and ctx.carteira_pior_perda_pct > 0.04:
        falhados.append(
            f"P6: carteira pior perda {ctx.carteira_pior_perda_pct*100:.1f}% > 4%"
        )

    # Ponto 7: live regras
    if ctx.is_live:
        if ctx.latencia_segundos is not None and ctx.latencia_segundos > 10:
            falhados.append(f"P7: latencia live {ctx.latencia_segundos}s > 10s")
        if not ctx.sem_suspensao:
            falhados.append("P7: odd suspensa no ultimo minuto")

    # Ponto 8: vida da conta
    if ctx.casa_limitada and ctx.ev < 0.08:
        falhados.append(
            f"P8: casa limitada e EV {ctx.ev*100:.1f}% < 8% (nao vale 'vida' da conta)"
        )
    if ctx.ev_bom_noutra_casa and ctx.ev < 0.05:
        avisos.append("P8: ha EV melhor noutra casa — considerar primeiro")

    return ResultadoFiltro(
        passou=len(falhados) == 0,
        pontos_falhados=falhados,
        pontos_avisos=avisos,
    )
