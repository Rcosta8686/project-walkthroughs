"""Motor de value betting: EV e remoção da margem da casa (overround).

``ev(prob, odd)`` é o retorno esperado por unidade apostada:

    EV = prob × odd − 1

Positivo = aposta vale a pena em média. Negativo = má aposta em média.

O ``overround`` é a margem embutida nas odds pela casa. Para um mercado
binário (over/under), a casa quota, por exemplo, 1,91 em cada lado;
``1/1,91 + 1/1,91 = 1,047`` → 4,7% de margem. ``remover_overround`` devolve
as probabilidades implícitas "justas" assumindo distribuição proporcional
da margem.
"""

from __future__ import annotations


def ev(prob: float, odd: float) -> float:
    """Valor esperado por unidade apostada. Pode ser negativo."""
    if odd <= 1.0:
        raise ValueError(f"Odd deve ser > 1, recebida {odd}.")
    if not 0.0 <= prob <= 1.0:
        raise ValueError(f"Prob deve estar em [0, 1], recebida {prob}.")
    return prob * odd - 1.0


def probs_implicitas(odds: list[float]) -> list[float]:
    """1/odd normalizado para somar 1 — remove o overround."""
    inversos = [1.0 / o for o in odds]
    total = sum(inversos)
    if total <= 0:
        raise ValueError("Soma dos inversos das odds é não-positiva.")
    return [i / total for i in inversos]


def overround(odds: list[float]) -> float:
    """Margem da casa. 0.05 = 5% de margem (típico de bookmaker soft)."""
    return sum(1.0 / o for o in odds) - 1.0


def ha_valor(prob_modelo: float, odd: float, ev_minimo: float = 0.03) -> bool:
    """True se EV supera o limiar — gatilho para sugerir aposta."""
    return ev(prob_modelo, odd) >= ev_minimo
