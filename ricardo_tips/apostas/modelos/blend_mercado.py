"""Blend p_modelo × p_mercado — Secção 19 do doc de estrategias.

Tese Rob Pizzola / Caan Berry: o mercado (pool de todos os
apostadores profissionais) ja contem muita informacao. Se o modelo
diz EXACTAMENTE o mesmo que o mercado, nao ha vantagem — ha so um
modelo. Se diz algo muito diferente, muitas vezes e o modelo que
esta errado.

Protocolo:
  p_final = w × p_modelo + (1 − w) × p_mercado
  EV = p_final × odd − 1

Default w = 0.3 (o mercado pesa mais). So aumentar w se walk-forward
mostrar que a mistura tem melhor Brier que o mercado sozinho.

Discrepancias GRANDES (>10pp absoluto ou EV>15%) sinalizam-se para
"verificar" (onze errado? lesao nao registada? erro de dados?). Nao
emitir sem verificar.
"""
from __future__ import annotations

W_DEFAULT = 0.30               # peso do modelo (mercado = 1 − w)
DISCREPANCIA_LIMIAR = 0.10     # 10pp — acima disto, marcar p/ verificar
EV_LIMIAR_VERIFICAR = 0.15     # EV > 15% tambem marca p/ verificar


def p_mercado_from_odd(odd: float) -> float:
    """Probabilidade implicita de uma odd unica (1/odd).

    Nao remove a margem do book — para 1-outcome markets, nao podemos
    remover (precisamos do par). Para markets com par (over/under,
    btts), ha versao com no_vig disponivel.
    """
    if odd <= 1.0:
        raise ValueError(f"Odd deve ser > 1 (recebida {odd})")
    return 1.0 / odd


def p_mercado_binaria_fair(odd_a: float, odd_b: float) -> tuple[float, float]:
    """Probs justas (sem margem) de um mercado binario — usar quando
    temos as duas odds (over E under, btts sim E nao, etc).
    """
    from apostas.modelos.no_vig import prob_justa_binaria
    return prob_justa_binaria(odd_a, odd_b)


def blend(p_modelo: float, p_mercado: float, w: float = W_DEFAULT) -> float:
    """Mistura ponderada. w=0.3 → mercado pesa 70%.

    Resultado sempre em [0.01, 0.99].
    """
    p_modelo = max(0.0, min(1.0, p_modelo))
    p_mercado = max(0.0, min(1.0, p_mercado))
    w = max(0.0, min(1.0, w))
    p = w * p_modelo + (1 - w) * p_mercado
    return max(0.01, min(0.99, p))


def discrepancia_grande(p_modelo: float, p_mercado: float,
                         limiar: float = DISCREPANCIA_LIMIAR) -> bool:
    """True se |p_modelo − p_mercado| >= limiar (default 10pp)."""
    return abs(p_modelo - p_mercado) >= limiar


def precisa_verificar(p_modelo: float, p_mercado: float,
                       odd: float, ev_limiar: float = EV_LIMIAR_VERIFICAR,
                       pp_limiar: float = DISCREPANCIA_LIMIAR) -> tuple[bool, str]:
    """Verifica se sinal e demasiado 'bom demais para ser verdade'.

    Devolve (precisa_verificar, razao). Razao vazia quando ok.
    """
    if abs(p_modelo - p_mercado) >= pp_limiar:
        return True, (f"gap p_modelo {p_modelo*100:.1f}% vs "
                      f"p_mercado {p_mercado*100:.1f}% (>{pp_limiar*100:.0f}pp)")
    ev_puro = p_modelo * odd - 1.0
    if ev_puro >= ev_limiar:
        return True, f"EV {ev_puro*100:.1f}% acima limiar {ev_limiar*100:.0f}%"
    return False, ""


def aplicar_blend(p_modelo: float, odd: float,
                   w: float = W_DEFAULT) -> dict:
    """One-shot: calcula p_mercado, blend, flag verificar, EV.

    Devolve dict com: p_mercado, p_final, verificar, verificar_razao, ev.
    """
    p_mercado = p_mercado_from_odd(odd)
    verificar, razao = precisa_verificar(p_modelo, p_mercado, odd)
    p_final = blend(p_modelo, p_mercado, w)
    ev = p_final * odd - 1.0
    return {
        "p_mercado": round(p_mercado, 4),
        "p_final": round(p_final, 4),
        "verificar": verificar,
        "verificar_razao": razao,
        "ev": round(ev, 4),
    }
