"""Modelo preditivo para in-play.

Dado o estado actual de um jogo (``EstadoLive`` do Sofascore) e opcionais
inputs pré-jogo (xG esperados), estima probabilidades para:

  - P(mais ≥N golos antes do fim)
  - P(BTTS acontecer antes do fim) — útil quando ainda não aconteceu
  - P(mais ≥N cantos antes do fim)
  - P(mais ≥N cartões amarelos antes do fim)
  - P(resultado final = 1 / X / 2) — bayesian update do pré-jogo

A matemática é Poisson time-decayed com ajustes empíricos documentados
no artigo "The Theory of In-play Betting" (Dixon & Robinson 1998, mais
versões modernas tipo Baio-Blangiardo 2010). Os coeficientes aqui são
razoáveis mas não calibrados ao histórico Ricardo Tips — isso virá
depois quando tivermos ≥500 jogos trackeados com stats live.

Nenhum destes outputs é "verdade"; são **probabilidades do modelo**.
Depois comparas com odd da casa (1/odd = prob da casa) e vês se há
edge. Mesmo com modelo imperfeito, se consistentemente apanhas edges
≥5-10%, a filosofia Pinnacle→softs mostra que o volume compensa a
imprecisão (variancia reduz com volume).

Formula central:
  λ_restantes = λ_pre × (90-min)/90 × fator_marcador(diff, min)
  λ_atualizado = w(min) × λ_restantes + (1-w(min)) × λ_from_xG(estado)

onde xG_from_rate = xG_actual / min * minutos_restantes.

P(≥N golos restantes) = 1 - F_Poisson(N-1, λ_atualizado)
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive


# ───────────── Fatores empíricos (ajustar quando houver dados Ricardo) ─────────────

# Multiplicadores da rate de golos pelo marcador. Vem de análises
# agregadas: jogo empatado tardio tende a defender (<1), jogo com 1
# golo de diferença late game tem lose-urgency (>1).
# Chave: (diff_absoluto, "early"/"mid"/"late"), diff = goals_feitos - goals_sofridos
_FATOR_MARCADOR = {
    (0, "early"): 1.00,
    (0, "mid"):   0.90,
    (0, "late"):  0.75,   # 0-0 aos 75+ = defender quase sempre
    (1, "early"): 1.00,
    (1, "mid"):   1.05,
    (1, "late"):  1.20,   # losing team pressiona
    (2, "early"): 0.95,
    (2, "mid"):   0.85,
    (2, "late"):  0.80,
    # 3+: garbage time, intensidade baixa
    (3, "early"): 0.90,
    (3, "mid"):   0.70,
    (3, "late"):  0.60,
}


def _fase_do_jogo(minuto: int) -> str:
    if minuto < 30:
        return "early"
    if minuto < 65:
        return "mid"
    return "late"


def _fator_marcador(marcador_casa: int, marcador_fora: int, minuto: int) -> float:
    diff = min(abs(marcador_casa - marcador_fora), 3)
    fase = _fase_do_jogo(minuto)
    return _FATOR_MARCADOR.get((diff, fase), 1.0)


def _poisson_cdf(k: int, lam: float) -> float:
    """F_Poisson(k, lam) = sum_{i=0..k} lam^i * e^-lam / i!"""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    acc = 0.0
    termo = exp(-lam)
    acc += termo
    for i in range(1, k + 1):
        termo *= lam / i
        acc += termo
    return min(1.0, acc)


# ───────────────────────────── API ─────────────────────────────


@dataclass
class PrevisaoLive:
    minuto: int
    minutos_restantes: int
    lam_golos_restantes: float
    lam_cantos_restantes: float
    lam_cartoes_amarelos_restantes: float
    # P(≥N golos restantes no jogo todo)
    p_mais_1_golo: float
    p_mais_2_golos: float
    # P(ambas marcam no final) — útil se BTTS ainda não aconteceu
    p_btts_final: float
    # P(jogo acaba com Over N golos no total)
    def p_over_total(self, linha_total: float) -> float:
        """P(total golos no final > linha). Usa o marcador actual + λ_restantes."""
        n_actual = self._marcador_casa + self._marcador_fora
        n_para_bater = int(linha_total) - n_actual
        if linha_total % 1 == 0.5:
            # Linhas 2.5 etc: precisamos de >= ceil
            n_para_bater = int(linha_total + 1) - n_actual
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_golos_restantes)

    # Internos (usados por métodos)
    _marcador_casa: int = 0
    _marcador_fora: int = 0

    def p_mais_cantos(self, n: int) -> float:
        return 1.0 - _poisson_cdf(n - 1, self.lam_cantos_restantes)

    def p_mais_cartoes_amarelos(self, n: int) -> float:
        return 1.0 - _poisson_cdf(n - 1, self.lam_cartoes_amarelos_restantes)


def prever(
    estado: "EstadoLive",
    lam_golos_pre: float | None = None,
    lam_cantos_pre: float | None = None,
    lam_cartoes_pre: float | None = None,
) -> PrevisaoLive:
    """Devolve previsão de eventos restantes para um jogo in-play.

    Parameters
    ----------
    estado:
        Snapshot Sofascore (resultado + xG + shots + cards + posse).
    lam_golos_pre:
        Golos totais esperados pre-match. Default: implícito 2.6 (média
        europeia). Se tens a sugestão pré-match Pinnacle, usa 1/odd_over_2_5
        + 1/odd_under_2_5 e deriva lambda que satisfaz Poisson = linha.
    lam_cantos_pre:
        Cantos totais esperados pre-match. Default 10.5.
    lam_cartoes_pre:
        Cartões amarelos totais esperados pre-match. Default 4.0.
    """
    lam_golos_pre = lam_golos_pre if lam_golos_pre is not None else 2.6
    lam_cantos_pre = lam_cantos_pre if lam_cantos_pre is not None else 10.5
    lam_cartoes_pre = lam_cartoes_pre if lam_cartoes_pre is not None else 4.0

    minuto = min(90, max(0, estado.minuto))
    minutos_restantes = max(0, 90 - minuto)
    frac_restante = minutos_restantes / 90.0

    fator_m = _fator_marcador(estado.marcador_casa, estado.marcador_fora, minuto)

    # Rate de golos restantes
    lam_golos_rest_prior = lam_golos_pre * frac_restante * fator_m

    # Update bayesian com xG efectivo observado
    if estado.xg_casa is not None and estado.xg_fora is not None and minuto > 10:
        xg_actual = estado.xg_casa + estado.xg_fora
        xg_rate_actual = xg_actual / minuto  # xG por minuto efectivo
        lam_from_xg = xg_rate_actual * minutos_restantes * fator_m
        # Peso: cedo = confia pre; tarde = confia actual
        w_pre = max(0.2, 1 - minuto / 90)
        lam_golos_rest = w_pre * lam_golos_rest_prior + (1 - w_pre) * lam_from_xg
    else:
        lam_golos_rest = lam_golos_rest_prior

    # Intensidade recente (shots last 10-15 min)
    if estado.xg_recent_casa is not None and estado.xg_recent_fora is not None:
        xg_recent = estado.xg_recent_casa + estado.xg_recent_fora
        xg_recent_rate = xg_recent / 15.0  # por minuto nos últimos 15
        # Escala contra o rate médio pre-match
        rate_medio_pre_minuto = lam_golos_pre / 90.0
        intensidade = xg_recent_rate / rate_medio_pre_minuto if rate_medio_pre_minuto > 0 else 1.0
        intensidade = min(2.0, max(0.5, intensidade))
        lam_golos_rest *= intensidade

    # Rate de cantos restantes (modelo simples: linear + factor marcador)
    cantos_actual = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    if minuto > 10:
        cantos_rate = cantos_actual / minuto
    else:
        cantos_rate = lam_cantos_pre / 90.0
    lam_cantos_rest = cantos_rate * minutos_restantes * fator_m

    # Rate de cartoes amarelos: mais frequentes em 2H + jogos apertados
    cards_actual = estado.yellow_cards_casa + estado.yellow_cards_fora
    if minuto > 10:
        cards_rate = cards_actual / minuto
    else:
        cards_rate = lam_cartoes_pre / 90.0
    # 2H tem ~60% dos cartoes. Fator para proximos minutos:
    fator_fase_cards = 1.3 if minuto >= 45 else 1.0
    # Marcador apertado late = mais frustracao
    diff = abs(estado.marcador_casa - estado.marcador_fora)
    fator_card_marcador = 1.1 if (diff <= 1 and minuto >= 70) else 1.0
    lam_cards_rest = cards_rate * minutos_restantes * fator_fase_cards * fator_card_marcador

    p_mais_1 = 1.0 - _poisson_cdf(0, lam_golos_rest)
    p_mais_2 = 1.0 - _poisson_cdf(1, lam_golos_rest)

    # P(BTTS final) = 1 se ja aconteceu, caso contrario estima
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        p_btts = 1.0
    else:
        # Precisa que a equipa com 0 marque pelo menos 1 vez
        if estado.marcador_casa == 0 and estado.marcador_fora == 0:
            # Ambas precisam de marcar. Aproximacao: cada equipa com metade da rate
            lam_c = lam_golos_rest / 2
            lam_f = lam_golos_rest / 2
            p_casa_marca = 1.0 - exp(-lam_c)
            p_fora_marca = 1.0 - exp(-lam_f)
            p_btts = p_casa_marca * p_fora_marca
        else:
            # So a equipa com 0 precisa de marcar
            # Assume 50/50 distribuicao do lam entre equipas
            lam_lado_zero = lam_golos_rest / 2
            p_btts = 1.0 - exp(-lam_lado_zero)

    return PrevisaoLive(
        minuto=minuto,
        minutos_restantes=minutos_restantes,
        lam_golos_restantes=lam_golos_rest,
        lam_cantos_restantes=lam_cantos_rest,
        lam_cartoes_amarelos_restantes=lam_cards_rest,
        p_mais_1_golo=p_mais_1,
        p_mais_2_golos=p_mais_2,
        p_btts_final=p_btts,
        _marcador_casa=estado.marcador_casa,
        _marcador_fora=estado.marcador_fora,
    )


def ev_contra_odd(prob: float, odd_casa: float) -> float:
    """EV = p × (odd - 1) - (1 - p). >0 = aposta de valor."""
    if odd_casa <= 1.0 or prob <= 0 or prob >= 1:
        return -1.0
    return prob * (odd_casa - 1) - (1 - prob)
