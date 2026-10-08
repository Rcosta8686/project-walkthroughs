"""Remoção de overround (margem) das odds Pinnacle → probs justas.

Pinnacle opera com margem ~2% em top ligas. A soma das probs implícitas
(1/odd) dá ~1.02, não 1.0 — esses 2% são a margem da casa. Para obter
a "verdade" implícita do mercado, dividimos cada prob implícita pela
soma, normalizando a 1.

Duas abordagens:
  - Normalização aditiva (simples): p_fair = p_implicit / sum(p_implicit)
  - Shin (1993): assume que margem é devida a traders informados; mais
    sofisticado mas requer resolver uma equação não-linear. Para
    diferenças de 2-7%, as duas abordagens diferem em <0.5pp. Usamos
    a aditiva por simplicidade.

Para over/under: duas odds (over e under), sum ~= 1.02 → divide por sum.
Para 1x2: três odds, mesma lógica.
"""

from __future__ import annotations


def prob_justa_binaria(odd_a: float, odd_b: float) -> tuple[float, float]:
    """Para mercado 2-outcomes (over/under, BTTS). Devolve (p_a, p_b)
    com p_a + p_b = 1.0.

    Levanta ValueError se as odds são <= 1 ou devoluçamacompensam uma
    arbitragem impossível (sum < 0.99).
    """
    if odd_a <= 1 or odd_b <= 1:
        raise ValueError(f"Odds devem ser > 1 (recebidas {odd_a}, {odd_b})")
    p_a_imp = 1.0 / odd_a
    p_b_imp = 1.0 / odd_b
    soma = p_a_imp + p_b_imp
    if soma < 0.99:
        raise ValueError(f"Overround suspeito: {soma:.4f} (deveria ser >= 0.99)")
    return p_a_imp / soma, p_b_imp / soma


def prob_justa_1x2(
    odd_casa: float, odd_empate: float, odd_fora: float,
) -> tuple[float, float, float]:
    """Mercado 1X2 (3-outcomes). Normalização aditiva."""
    for odd in (odd_casa, odd_empate, odd_fora):
        if odd <= 1:
            raise ValueError(f"Odd deve ser > 1 (recebida {odd})")
    p_c = 1.0 / odd_casa
    p_x = 1.0 / odd_empate
    p_f = 1.0 / odd_fora
    soma = p_c + p_x + p_f
    if soma < 0.99:
        raise ValueError(f"Overround suspeito: {soma:.4f}")
    return p_c / soma, p_x / soma, p_f / soma


def margem(odds: list[float]) -> float:
    """Devolve a margem (overround) do book para um mercado.

    Ex: Pinnacle Over/Under tipicamente ~1.02; soft books 1.06-1.08.
    """
    return sum(1.0 / o for o in odds)
