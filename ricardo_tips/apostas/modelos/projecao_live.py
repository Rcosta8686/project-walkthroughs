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
    aplicar_contexto: bool = False,
) -> ProjecaoResumo:
    """Projecta valor final + odds justas por linha.

    mercado: "sot" | "remates" | "cantos"
    valor_atual_X: X observado ate agora (X = casa, fora)
    lam_rest_X: taxa Poisson do que falta (sai do live_predictor)
    aplicar_contexto: False (default) assume que lam_rest ja tem ajustes
      de score/chasing (vem do live_predictor que ja os aplica).
      Pass True SO quando lam_rest e "neutro" (ex: taxa historica directa).

    Fadiga (minuto >= 46 + dias_descanso <= 3) sempre aplicada quando dada,
    porque o live_predictor nao a conhece.
    """
    mercado = mercado.lower()
    if mercado not in {"sot", "remates", "cantos"}:
        raise ValueError(f"mercado invalido: {mercado}")

    # Diff do ponto de vista de cada equipa
    diff_casa = score_casa - score_fora
    diff_fora = -diff_casa

    if aplicar_contexto:
        mult_casa = _multiplicador_contexto(diff_casa, mercado)
        mult_fora = _multiplicador_contexto(diff_fora, mercado)
    else:
        mult_casa = 1.0
        mult_fora = 1.0

    # Fadiga: so afecta 2a parte (minuto >= 46), SEMPRE aplicada (predictor
    # nao sabe de dias de descanso).
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


_EMOJI_MERCADO = {
    "sot": "🎯",
    "cantos": "🚩",
    "remates": "💥",
}


def _estrelas_projecao(proj: ProjecaoResumo) -> int:
    """Numero de estrelas (1-3) baseado em quantas linhas sao accionaveis.

    Uma linha e "accionavel" se a probabilidade over cai dentro de uma
    zona onde a casa provavelmente estica a odd em algum sentido
    (0.25 - 0.75). 3 linhas accionaveis = 3 estrelas. 1 linha = 1 estrela.
    Minimo 1 (a projeccao em si ja passou o filtro tem_valor_actionavel).
    """
    if not proj.linhas_total:
        return 1
    n = sum(1 for lo in proj.linhas_total if 0.25 <= lo.prob_over <= 0.75)
    return max(1, min(3, n))


def formatar_telegram(
    proj: ProjecaoResumo,
    nome_casa: str,
    nome_fora: str,
    score_casa: int,
    score_fora: int,
    *,
    extras: str = "",
) -> str:
    """Mensagem Telegram compacta.

    `extras`: texto opcional a inserir ANTES da nota final (ex: linhas de
    voz dos treinadores). Pode comecar com '\\n'.
    """
    nome_merc = _NOMES_MERCADO.get(proj.mercado, proj.mercado)
    emoji = _EMOJI_MERCADO.get(proj.mercado, "📊")
    estrelas_str = "⭐" * _estrelas_projecao(proj)

    linhas = [
        f"{estrelas_str} {emoji} *PROJECAO {proj.minuto}'* "
        f"| {nome_casa} {score_casa}-{score_fora} {nome_fora}",
        f"*{nome_merc}:* {proj.valor_atual_total:.0f} → "
        f"*{proj.projecao_final_total:.1f}* (projeccao)",
    ]
    # Split por equipa quando as projecoes nao sao insignificantes
    if (proj.linha_casa and proj.linha_fora
            and (proj.projecao_final_casa >= 0.5
                 or proj.projecao_final_fora >= 0.5)):
        linhas.append(
            f"  {nome_casa}: {proj.valor_atual_casa:.0f}→"
            f"{proj.projecao_final_casa:.1f}  ·  "
            f"{nome_fora}: {proj.valor_atual_fora:.0f}→"
            f"{proj.projecao_final_fora:.1f}"
        )
    if proj.aviso_fadiga:
        linhas.append(f"⚠ {proj.aviso_fadiga}")
    msg = "\n".join(linhas)
    if extras:
        # Garante exactamente 1 \n entre o corpo e os extras
        msg = msg + (extras if extras.startswith("\n") else "\n" + extras)
    msg += "\n_So aposta se a casa paga MAIS do que a odd justa._"
    msg += f"\nAnalista Projecao · {emoji}"
    return msg


# ─────────── Filtro: projeccao e informativa? ─────────────────────────────

def tem_valor_actionavel(proj: ProjecaoResumo) -> bool:
    """True se pelo menos 1 linha tem prob_over entre 20% e 80%.

    Se as 3 linhas estao todas em "quase certo" (prob >= 0.85 ou <= 0.15),
    nao ha linha interessante para apostar — a casa vai ter odds a condizer
    e nao ha edge possivel. Nao enviar.
    """
    if not proj.linhas_total:
        return False
    for lo in proj.linhas_total:
        if 0.20 <= lo.prob_over <= 0.80:
            return True
    return False


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
