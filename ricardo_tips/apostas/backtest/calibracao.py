"""Diagnóstico de calibração do modelo.

Agrupa as apostas sugeridas em bins de probabilidade e compara a
probabilidade declarada pelo modelo com a hit-rate real observada.
Modelo bem calibrado → essas duas curvas sobrepõem-se. Modelo
sobre-confiante → a hit-rate real fica abaixo do que o modelo prevê.

Também calcula log-loss e Brier score, duas métricas padrão.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from apostas.backtest.walk_forward import ApostaSimulada


@dataclass
class BinCalibracao:
    prob_min: float
    prob_max: float
    n_apostas: int
    prob_media_modelo: float
    hit_rate_real: float
    roi: float


def calibrar(
    apostas: list[ApostaSimulada],
    bins: list[tuple[float, float]] | None = None,
) -> list[BinCalibracao]:
    """Agrupa apostas por bins de probabilidade e devolve resumo por bin."""
    if bins is None:
        bins = [(0.40, 0.50), (0.50, 0.55), (0.55, 0.60),
                (0.60, 0.70), (0.70, 0.80), (0.80, 1.01)]

    resultado: list[BinCalibracao] = []
    for lo, hi in bins:
        no_bin = [a for a in apostas if lo <= a.prob_modelo < hi]
        if not no_bin:
            continue
        n = len(no_bin)
        prob_media = sum(a.prob_modelo for a in no_bin) / n
        ganhas = sum(1 for a in no_bin if a.resultado == "ganho")
        hit = ganhas / n
        stake = sum(a.stake for a in no_bin)
        lucro = sum(a.lucro for a in no_bin)
        resultado.append(BinCalibracao(
            prob_min=lo, prob_max=hi,
            n_apostas=n,
            prob_media_modelo=prob_media,
            hit_rate_real=hit,
            roi=lucro / stake if stake else 0.0,
        ))
    return resultado


def brier_score(apostas: list[ApostaSimulada]) -> float:
    """Média de (prob_modelo − resultado)²; menor é melhor. 0 = perfeito."""
    if not apostas:
        return 0.0
    soma = 0.0
    for a in apostas:
        resultado = 1.0 if a.resultado == "ganho" else 0.0
        soma += (a.prob_modelo - resultado) ** 2
    return soma / len(apostas)


def log_loss(apostas: list[ApostaSimulada]) -> float:
    """Log-loss médio; menor é melhor."""
    if not apostas:
        return 0.0
    soma = 0.0
    eps = 1e-9
    for a in apostas:
        p = min(max(a.prob_modelo, eps), 1 - eps)
        soma += -math.log(p) if a.resultado == "ganho" else -math.log(1 - p)
    return soma / len(apostas)
