"""Especialista em cartoes amarelos: projeccao rica para o mercado favorito.

Combina 7 factores em 1 projeccao: baseline historico + arbitro + rivalidade +
marcador + fase do jogo + ritmo observado + vermelhos ja em campo.

Modelo:
  lam_rest = baseline_rest * mult_arbitro * mult_derby * mult_marcador *
             mult_fase * mult_ritmo * mult_vermelho

  cards_proj = cards_actual + Poisson(lam_rest)
  P(over X.5) = P(cards_proj > X.5) via Poisson skellam.

Usado por scanner_live_v2 para o mercado "cards" / "cartoes".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from apostas.modelos import referencias
from apostas.utils.logger import get_logger

log = get_logger(__name__)


# ─── Constantes ──────────────────────────────────────────────────────────

# Fase do jogo: taxa de cards relativa a media horaria.
# Primeiro 30' sao calmos; ultimo 15' concentra ~30% dos cards.
_FASE_RATE = {
    (0, 30): 0.85,
    (30, 45): 1.05,
    (45, 60): 1.00,
    (60, 75): 1.15,
    (75, 90): 1.60,
    (90, 120): 1.80,  # descontos — cada minuto conta mais
}

# Marcador: situacao de jogo multiplica tensao.
# diff absoluto 0: empate — tensao +; 1: apertado +; 2+: resolvido −.
_MARCADOR_MULT = {
    0: 1.10,
    1: 1.05,
    2: 0.95,
    3: 0.85,  # goleada: ninguem quer riscar
}

# Vermelho ja em campo: resto do jogo mais quente.
_FATOR_VERMELHO = 1.35

# Ritmo observado vs baseline: se >=1.5x o esperado, +20% para resto.
_LIMIAR_RITMO_QUENTE = 1.5
_FATOR_RITMO_QUENTE = 1.20

# Faltas por amarelo: se o arbitro desta a cada ~5 ou menos faltas → +10%.
_LIMIAR_FALTAS_AMARELO_SEVERO = 5.0
_FATOR_ARBITRO_SEVERO_OBSERVADO = 1.10


@dataclass
class ProjecaoCartoes:
    """Resultado da projecao especialista para cartoes amarelos."""
    cards_actual: int
    minuto: int
    cards_proj_final: float
    lam_restante: float
    # Factores aplicados (para explicabilidade)
    factor_arbitro: float
    factor_derby: float
    factor_marcador: float
    factor_fase: float
    factor_ritmo: float
    factor_vermelho: float
    # Prob OVER por linha half-point tipica
    prob_over_por_linha: dict[float, float] = field(default_factory=dict)
    # Explicacao textual (lista de razoes principais, ordenadas por peso)
    razoes: list[str] = field(default_factory=list)

    @property
    def confianca(self) -> int:
        """1-5: numero de factores com desvio relevante vs 1.0."""
        factores = [
            self.factor_arbitro, self.factor_derby, self.factor_marcador,
            self.factor_fase, self.factor_ritmo, self.factor_vermelho,
        ]
        n = sum(1 for f in factores if abs(f - 1.0) >= 0.08)
        return max(1, min(5, n + 1))


# ─── Helpers ─────────────────────────────────────────────────────────────

def _rate_fase(minuto: int) -> float:
    for (ini, fim), rate in _FASE_RATE.items():
        if ini <= minuto < fim:
            return rate
    return 1.0


def _poisson_prob_over(linha: float, lam_total: float, obs_actual: int) -> float:
    """P(X_final > linha) onde X_final = obs_actual + Poisson(lam_total)."""
    if lam_total <= 0:
        return 1.0 if obs_actual > linha else 0.0
    resto_necessario = linha - obs_actual
    if resto_necessario < 0:
        return 1.0
    n_minimo = int(math.floor(resto_necessario)) + 1
    prob_abaixo = 0.0
    for k in range(0, n_minimo):
        prob_abaixo += math.exp(-lam_total) * (lam_total ** k) / math.factorial(k)
    return max(0.0, min(1.0, 1.0 - prob_abaixo))


# ─── API principal ───────────────────────────────────────────────────────

def projetar_cartoes(
    *,
    cards_actual_casa: int,
    cards_actual_fora: int,
    minuto: int,
    marcador_casa: int,
    marcador_fora: int,
    baseline_cards_por_90: float,
    nome_arbitro: str | None = None,
    nome_casa: str = "",
    nome_fora: str = "",
    vermelhos_em_campo: int = 0,
    fouls_actual_total: int | None = None,
    linhas_candidatas: tuple[float, ...] = (2.5, 3.5, 4.5, 5.5, 6.5, 7.5),
) -> ProjecaoCartoes:
    """Projecta cards finais + prob OVER por linha.

    baseline_cards_por_90: cards/90min esperados pela ficha das 2 equipas
      combinadas (vem do confronto). Esta e a taxa "natural" do jogo sem
      ajustes de arbitro/contexto.

    nome_arbitro, nome_casa/nome_fora: alimentam _factor_ e _derby_.
    vermelhos_em_campo: 0 ou mais; aumenta ritmo do resto.
    fouls_actual_total: opcional — serve para detectar arbitro severo
      observacionalmente (se faltas/amarelo baixo, severo).
    """
    cards_actual = cards_actual_casa + cards_actual_fora
    minuto_safe = max(1, min(90 + 15, minuto))

    # Baseline do resto do jogo (ja ajustado pelo tempo que falta)
    min_rest = max(1, 90 - minuto)
    baseline_rest = baseline_cards_por_90 * (min_rest / 90.0)

    # ─── Factor arbitro ──────────────────────────────────────────────
    factor_arb = referencias.fator_arbitro(nome_arbitro) if nome_arbitro else 1.0

    # Severidade observada: se faltas/amarelo < 5, arbitro mais severo que
    # o esperado. Incrementa factor.
    if (fouls_actual_total is not None and cards_actual > 0
            and (fouls_actual_total / max(1, cards_actual)) <= _LIMIAR_FALTAS_AMARELO_SEVERO):
        factor_arb *= _FATOR_ARBITRO_SEVERO_OBSERVADO

    # ─── Factor rivalidade ───────────────────────────────────────────
    factor_derby = referencias.fator_rivalidade(nome_casa, nome_fora)

    # ─── Factor marcador (diff absoluto) ─────────────────────────────
    diff = abs(marcador_casa - marcador_fora)
    diff_clamped = min(3, diff)
    factor_marcador = _MARCADOR_MULT.get(diff_clamped, 1.0)

    # ─── Factor fase do jogo (resto do jogo) ─────────────────────────
    # Usamos o rate do minuto a meio do resto para capturar "onde esta a cair"
    mid_rest = minuto + min_rest // 2
    factor_fase = _rate_fase(mid_rest)

    # ─── Factor ritmo observado ──────────────────────────────────────
    esperado_ate_agora = baseline_cards_por_90 * (minuto / 90.0)
    factor_ritmo = 1.0
    if esperado_ate_agora > 0:
        ratio = cards_actual / esperado_ate_agora
        if ratio >= _LIMIAR_RITMO_QUENTE:
            factor_ritmo = _FATOR_RITMO_QUENTE

    # ─── Factor vermelho ─────────────────────────────────────────────
    factor_vermelho = _FATOR_VERMELHO if vermelhos_em_campo > 0 else 1.0

    # ─── Lambda restante ─────────────────────────────────────────────
    lam_restante = (baseline_rest * factor_arb * factor_derby
                    * factor_marcador * factor_fase * factor_ritmo
                    * factor_vermelho)
    cards_proj_final = cards_actual + lam_restante

    # ─── Prob OVER por linha ─────────────────────────────────────────
    prob_over_por_linha: dict[float, float] = {}
    for linha in linhas_candidatas:
        prob_over_por_linha[linha] = _poisson_prob_over(
            linha, lam_restante, cards_actual,
        )

    # ─── Razoes (ordenadas por desvio vs 1.0) ────────────────────────
    razoes_candidatas = [
        (abs(factor_arb - 1.0), _razao_arbitro(nome_arbitro, factor_arb)),
        (abs(factor_derby - 1.0), f"Derby/rivalidade (+{(factor_derby - 1) * 100:.0f}%)"
            if factor_derby > 1.0 else ""),
        (abs(factor_marcador - 1.0), _razao_marcador(diff, factor_marcador)),
        (abs(factor_fase - 1.0), _razao_fase(mid_rest, factor_fase)),
        (abs(factor_ritmo - 1.0), f"Ritmo quente ({cards_actual} cards ao {minuto}', 50%+ acima do esperado)"
            if factor_ritmo > 1.0 else ""),
        (abs(factor_vermelho - 1.0), f"Vermelho em campo (+{(factor_vermelho - 1) * 100:.0f}%)"
            if factor_vermelho > 1.0 else ""),
    ]
    razoes = [r for _, r in sorted(razoes_candidatas, key=lambda x: -x[0]) if r]

    return ProjecaoCartoes(
        cards_actual=cards_actual,
        minuto=minuto,
        cards_proj_final=round(cards_proj_final, 2),
        lam_restante=round(lam_restante, 2),
        factor_arbitro=round(factor_arb, 2),
        factor_derby=round(factor_derby, 2),
        factor_marcador=round(factor_marcador, 2),
        factor_fase=round(factor_fase, 2),
        factor_ritmo=round(factor_ritmo, 2),
        factor_vermelho=round(factor_vermelho, 2),
        prob_over_por_linha={k: round(v, 3) for k, v in prob_over_por_linha.items()},
        razoes=razoes[:3],
    )


def _razao_arbitro(nome: str | None, factor: float) -> str:
    if not nome or abs(factor - 1.0) < 0.08:
        return ""
    sinal = "+" if factor > 1.0 else "−"
    return f"Arbitro {nome.title()} ({sinal}{abs(factor - 1) * 100:.0f}%)"


def _razao_marcador(diff: int, factor: float) -> str:
    if diff == 0:
        return "Empate ao marcador — jogo tenso"
    if diff == 1:
        return "Jogo apertado por 1 golo — ritmo alto"
    if diff >= 3:
        return "Goleada — jogo arrumado, ninguem arrisca"
    return ""


def _razao_fase(minuto: int, factor: float) -> str:
    if minuto >= 75:
        return "Reta final — pico de cartoes (ultimos 15 min)"
    if minuto >= 90:
        return "Descontos — minutos quentes"
    return ""


# ─── Narrativa para Telegram ─────────────────────────────────────────────

def narrativa_para_telegram(
    proj: ProjecaoCartoes,
    *,
    linha_alvo: float | None = None,
    lado: str = "over",
) -> str:
    """Mensagem resumo tipo mega-cerebro especialista em cards.

    linha_alvo: a linha a destacar (ex: 4.5). Se None, escolhe a mais acionavel.
    """
    if not proj.prob_over_por_linha:
        return ""

    # Escolhe a linha mais acionavel (prob mais perto de 0.65 para over,
    # 0.35 para under) se nao foi dada.
    if linha_alvo is None:
        alvo_prob = 0.65 if lado == "over" else 0.35
        linha_alvo = min(proj.prob_over_por_linha.keys(),
                          key=lambda l: abs(proj.prob_over_por_linha[l] - alvo_prob))

    prob_over = proj.prob_over_por_linha.get(linha_alvo, 0.5)
    prob_decisao = prob_over if lado == "over" else 1 - prob_over

    lines = [
        "🧠 *Mega-Especialista Cartoes*",
        f"_Projeccao final: {proj.cards_proj_final:.1f} cards_",
        f"*{lado.upper()} {linha_alvo}*: prob {prob_decisao * 100:.0f}% (fair odd {1 / max(prob_decisao, 0.01):.2f})",
    ]
    if proj.razoes:
        lines.append("*Factores dominantes:*")
        for r in proj.razoes:
            lines.append(f"  • {r}")
    lines.append(f"_Confianca: {proj.confianca}/5_")
    return "\n".join(lines)
