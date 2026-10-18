"""Projecao live aos minutos ~40', ~55', ~70': total + por equipa + odds justas.

Em vez de enviar palpites "faceis" (over/under remates com uma linha fixa),
este modulo projecta o valor FINAL de remates / SOT / cantos de cada equipa
e do jogo todo, e devolve a odd JUSTA para cada linha a volta da projeccao.
A decisao final e do utilizador: so aposta se a casa pagar MAIS do que a
odd justa.

Entra:
    - PrevisaoLive do live_predictor v2 (ja tem lam_rest por equipa)
    - EstadoLive (minuto, score)
    - Perfil equipa opcional (fadiga: dias_desde_ultimo_jogo)

Sai:
    - ProjecaoResumo com projeccao final + odds justas por linha

Ajustes aplicados sobre o output do predictor:
    - Estado do jogo por equipa (ganhar/perder/empatar) de 1 em 1 minuto,
      nao apenas nos ultimos 25 min. Baseado nos jogos observados:
        * a ganhar por 1+ gol: -15% em SOT e cantos
        * a perder por 1 gol: +25% em SOT, +15% em cantos
        * a perder por 2+ gols: +45% em SOT, +25% em cantos
    - Fadiga: se jogou ha <=3 dias, 2a parte -8% para essa equipa
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from apostas.utils.logger import get_logger

log = get_logger(__name__)


# ─────────── Constantes de ajuste (medidas nos jogos observados) ──────────

# Multiplicadores quando a equipa esta a GANHAR (sobre a taxa restante)
_MULT_GANHA_SOT = 0.85
_MULT_GANHA_CANTOS = 0.85
_MULT_GANHA_REMATES = 0.90

# Multiplicadores quando a equipa esta a PERDER por 1
_MULT_PERDE1_SOT = 1.25
_MULT_PERDE1_CANTOS = 1.15
_MULT_PERDE1_REMATES = 1.20

# Multiplicadores quando a equipa esta a PERDER por 2+
_MULT_PERDE2_SOT = 1.45
_MULT_PERDE2_CANTOS = 1.25
_MULT_PERDE2_REMATES = 1.35

# Fadiga: 2a parte -8% se jogou ha <=3 dias (meio Champions)
_FATOR_FADIGA_2T = 0.92
_LIMIAR_FADIGA_DIAS = 3


# ─────────── Modelo de resultado ──────────────────────────────────────────

@dataclass
class LinhaOdds:
    """Odd justa over/under para uma linha."""
    linha: float
    over_justa: float
    under_justa: float
    prob_over: float


@dataclass
class ProjecaoResumo:
    """Resumo da projeccao para 1 mercado (sot/remates/cantos)."""
    mercado: str  # "sot", "remates", "cantos"
    minuto: int
    valor_atual_total: float
    projecao_final_total: float
    valor_atual_casa: float
    valor_atual_fora: float
    projecao_final_casa: float
    projecao_final_fora: float
    linhas_total: list[LinhaOdds] = field(default_factory=list)
    linha_casa: Optional[LinhaOdds] = None
    linha_fora: Optional[LinhaOdds] = None
    aviso_fadiga: Optional[str] = None  # "AS Roma jogou ha 2 dias (fadiga)"


# ─────────── Helpers Poisson ──────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_prob_over(linha: float, lam_total: float, obs_actual: float) -> float:
    """Probabilidade de total_final > linha, dado obs_actual + lam_restante.

    Como lam e Poisson do que falta, somamos obs_actual + Poisson(lam).
    P(total > linha) = P(X > linha - obs_actual) = P(X >= ceil(linha - obs + 1))
    Para linha .5 (ex 8.5), P(X > 8.5 - obs) = P(X >= floor(8.5 - obs) + 1).
    """
    resto_necessario = linha - obs_actual
    if resto_necessario < 0:
        return 1.0
    # linha .5 -> precisamos de pelo menos floor(resto)+1 novos
    n_minimo = int(math.floor(resto_necessario)) + 1
    prob_abaixo = 0.0
    for k in range(0, n_minimo):
        prob_abaixo += _poisson_pmf(k, lam_total)
    return max(0.0, min(1.0, 1.0 - prob_abaixo))


def _odd_justa(prob: float) -> float:
    """Odd fair = 1 / prob (minimo 1.01, maximo 100)."""
    if prob <= 0.01:
        return 100.0
    if prob >= 0.99:
        return 1.01
    return round(1.0 / prob, 2)


# ─────────── Ajuste contexto ──────────────────────────────────────────────

def _multiplicador_contexto(
    diff_gols: int,  # da perspectiva da equipa (positivo = a ganhar)
    mercado: str,
) -> float:
    """Multiplicador a aplicar sobre lam_restante de uma equipa."""
    if diff_gols >= 1:
        # a ganhar
        if mercado == "sot":
            return _MULT_GANHA_SOT
        if mercado == "cantos":
            return _MULT_GANHA_CANTOS
        return _MULT_GANHA_REMATES
    if diff_gols == -1:
        if mercado == "sot":
            return _MULT_PERDE1_SOT
        if mercado == "cantos":
            return _MULT_PERDE1_CANTOS
        return _MULT_PERDE1_REMATES
    if diff_gols <= -2:
        if mercado == "sot":
            return _MULT_PERDE2_SOT
        if mercado == "cantos":
            return _MULT_PERDE2_CANTOS
        return _MULT_PERDE2_REMATES
    # empate = 1.0
    return 1.0


# ─────────── API principal ────────────────────────────────────────────────

def projetar(
    mercado: str,
    minuto: int,
    score_casa: int,
    score_fora: int,
    valor_atual_casa: float,
    valor_atual_fora: float,
    lam_rest_casa: float,
    lam_rest_fora: float,
    *,
    dias_descanso_casa: int | None = None,
    dias_descanso_fora: int | None = None,
    nome_casa: str = "Casa",
    nome_fora: str = "Fora",
) -> ProjecaoResumo:
    """Projecta valor final + odds justas por linha.

    mercado: "sot" | "remates" | "cantos"
    valor_atual_X: X observado ate agora (X = casa, fora)
    lam_rest_X: taxa Poisson do que falta (sai do live_predictor)

    Aplica em cima do lam_rest:
      - multiplicador contexto (a ganhar / perder / empatar) por equipa
      - fadiga se jogou ha <= 3 dias (so na 2a parte, >= min 46)
    """
    mercado = mercado.lower()
    if mercado not in {"sot", "remates", "cantos"}:
        raise ValueError(f"mercado invalido: {mercado}")

    # Diff do ponto de vista de cada equipa
    diff_casa = score_casa - score_fora
    diff_fora = -diff_casa

    mult_casa = _multiplicador_contexto(diff_casa, mercado)
    mult_fora = _multiplicador_contexto(diff_fora, mercado)

    # Fadiga: so afecta 2a parte (minuto >= 46)
    aviso_fadiga = None
    if minuto >= 46:
        if dias_descanso_casa is not None and dias_descanso_casa <= _LIMIAR_FADIGA_DIAS:
            mult_casa *= _FATOR_FADIGA_2T
            aviso_fadiga = f"{nome_casa} jogou ha {dias_descanso_casa} dias (fadiga)"
        if dias_descanso_fora is not None and dias_descanso_fora <= _LIMIAR_FADIGA_DIAS:
            mult_fora *= _FATOR_FADIGA_2T
            msg_f = f"{nome_fora} jogou ha {dias_descanso_fora} dias (fadiga)"
            aviso_fadiga = (
                aviso_fadiga + "; " + msg_f if aviso_fadiga else msg_f
            )

    lam_casa_adj = max(0.0, lam_rest_casa * mult_casa)
    lam_fora_adj = max(0.0, lam_rest_fora * mult_fora)

    proj_casa = valor_atual_casa + lam_casa_adj
    proj_fora = valor_atual_fora + lam_fora_adj
    proj_total = proj_casa + proj_fora

    valor_atual_total = valor_atual_casa + valor_atual_fora
    lam_total = lam_casa_adj + lam_fora_adj

    # Linhas a volta da projeccao total (3 linhas: -1, projeccao arredondada, +1)
    centro = math.floor(proj_total) + 0.5
    linhas_centrais = [centro - 1.0, centro, centro + 1.0]
    linhas_total: list[LinhaOdds] = []
    for linha in linhas_centrais:
        if linha <= valor_atual_total:
            # ja ultrapassou -> over e certeza
            prob = 1.0
        else:
            prob = _poisson_prob_over(linha, lam_total, valor_atual_total)
        linhas_total.append(LinhaOdds(
            linha=linha,
            over_justa=_odd_justa(prob),
            under_justa=_odd_justa(1 - prob),
            prob_over=round(prob, 3),
        ))

    # Linha por equipa (apenas a central)
    linha_c = math.floor(proj_casa) + 0.5
    prob_c = _poisson_prob_over(linha_c, lam_casa_adj, valor_atual_casa)
    linha_casa = LinhaOdds(
        linha=linha_c,
        over_justa=_odd_justa(prob_c),
        under_justa=_odd_justa(1 - prob_c),
        prob_over=round(prob_c, 3),
    )
    linha_f = math.floor(proj_fora) + 0.5
    prob_f = _poisson_prob_over(linha_f, lam_fora_adj, valor_atual_fora)
    linha_fora = LinhaOdds(
        linha=linha_f,
        over_justa=_odd_justa(prob_f),
        under_justa=_odd_justa(1 - prob_f),
        prob_over=round(prob_f, 3),
    )

    return ProjecaoResumo(
        mercado=mercado,
        minuto=minuto,
        valor_atual_total=valor_atual_total,
        projecao_final_total=round(proj_total, 1),
        valor_atual_casa=valor_atual_casa,
        valor_atual_fora=valor_atual_fora,
        projecao_final_casa=round(proj_casa, 1),
        projecao_final_fora=round(proj_fora, 1),
        linhas_total=linhas_total,
        linha_casa=linha_casa,
        linha_fora=linha_fora,
        aviso_fadiga=aviso_fadiga,
    )


# ─────────── Formatador Telegram ──────────────────────────────────────────

_NOMES_MERCADO = {
    "sot": "Remates a baliza",
    "remates": "Remates (total)",
    "cantos": "Cantos",
}


def formatar_telegram(
    proj: ProjecaoResumo,
    nome_casa: str,
    nome_fora: str,
    score_casa: int,
    score_fora: int,
) -> str:
    """Mensagem Telegram compacta estilo:

    📊 PROJECAO 56' | AS Roma 1-0 Genoa
    Remates a baliza — TOTAL DO JOGO
      agora 4 → projecao final 6.7
       5.5: over justo @2.00 | under justo @2.00
       6.5: over justo @2.08 | under justo @1.92
       7.5: over justo @3.33 | under justo @1.43
      Por equipa: AS Roma 4->6.1 (linha 5.5: over @1.87 / under @2.15)
                  Genoa 0->0.7 (linha 0.5: over @2.00 / under @2.00)
    So ha valor se a casa pagar MAIS do que a odd justa na mesma linha.
    """
    nome_merc = _NOMES_MERCADO.get(proj.mercado, proj.mercado)
    linhas = [
        f"📊 *PROJECAO {proj.minuto}'* | {nome_casa} {score_casa}-{score_fora} {nome_fora}",
        f"*{nome_merc}* — TOTAL DO JOGO (as duas equipas)",
        f"  agora {proj.valor_atual_total:.0f} → projeccao final {proj.projecao_final_total}",
    ]
    for lo in proj.linhas_total:
        linhas.append(
            f"   {lo.linha}: over justo @{lo.over_justa:.2f} | "
            f"under justo @{lo.under_justa:.2f}"
        )
    # Por equipa
    if proj.linha_casa and proj.linha_fora:
        linhas.append(
            f"  Por equipa: {nome_casa} {proj.valor_atual_casa:.0f}→"
            f"{proj.projecao_final_casa:.1f} "
            f"(linha {proj.linha_casa.linha}: over @{proj.linha_casa.over_justa:.2f} / "
            f"under @{proj.linha_casa.under_justa:.2f})"
        )
        linhas.append(
            f"              {nome_fora} {proj.valor_atual_fora:.0f}→"
            f"{proj.projecao_final_fora:.1f} "
            f"(linha {proj.linha_fora.linha}: over @{proj.linha_fora.over_justa:.2f} / "
            f"under @{proj.linha_fora.under_justa:.2f})"
        )
    if proj.aviso_fadiga:
        linhas.append(f"  ⚠ {proj.aviso_fadiga}")
    linhas.append(
        "_So ha valor se a casa pagar MAIS do que a odd justa na mesma linha._"
    )
    return "\n".join(linhas)


# ─────────── Minutos de projeccao ──────────────────────────────────────────

# Enviar projeccoes aos 40, 55, 70 (max 3 por jogo).
# Tolerancia: minuto >= target e nao foi enviado antes.
MINUTOS_PROJECAO = (40, 55, 70)


def deve_projetar(minuto: int, ja_enviados: set[int]) -> int | None:
    """Devolve o target (40/55/70) a disparar neste scan, ou None.

    Dispara UMA vez por janela, assim que minuto >= target. Guarda
    os targets ja disparados em `ja_enviados`.
    """
    for target in MINUTOS_PROJECAO:
        if minuto >= target and target not in ja_enviados:
            return target
    return None
