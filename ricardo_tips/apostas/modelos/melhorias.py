"""Funcoes das melhorias M1-M14 do scanner (ver docs/MELHORIAS_SCANNER.md).

Cada funcao tem teste em tests/test_melhorias.py com os valores de referencia
do documento melhorias-scanner.md.
"""
from __future__ import annotations

import math


# ─────────────────────────── M1 — remover margem ────────────────────────────

def tirar_margem(odds: list[float]) -> tuple[list[float], float]:
    """Divide 1/odd pela soma. Devolve (probs_justas, margem).

    Testes de referencia:
      [1.95, 1.95]        -> [0.5, 0.5],       margem ≈ 0.02564
      [2.10, 3.40, 3.60]  -> [0.4543, 0.2806, 0.2650], margem ≈ 0.0481
    """
    if not odds or any(o <= 1 for o in odds):
        raise ValueError("odds devem ser > 1")
    inversos = [1.0 / o for o in odds]
    soma = sum(inversos)
    probs = [i / soma for i in inversos]
    margem = soma - 1.0
    return probs, margem


# ─────────────────────────── EV e Kelly ─────────────────────────────────────

def ev(prob: float, odd: float) -> float:
    """Expected value de 1 unidade.

    Referencia: p=0.55, odd=2.00 -> 0.10
    """
    return prob * odd - 1.0


def kelly_quarto(prob: float, odd: float, max_frac: float = 0.02) -> float:
    """Kelly a 1/4, com cap em max_frac da banca. Nunca negativo.

    f_kelly = (p*b - q) / b    com b = odd - 1, q = 1 - p
    stake   = max(0, min(max_frac, f_kelly / 4))

    Referencias:
      p=0.55, odd=2.00, cap 2% -> 0.02 (0.025 limitado)
      p=0.45, odd=2.00         -> 0
    """
    if odd <= 1.0:
        return 0.0
    b = odd - 1.0
    q = 1.0 - prob
    f = (prob * b - q) / b
    if f <= 0:
        return 0.0
    return min(max_frac, f / 4.0)


# ─────────────────────────── Odd americana ─────────────────────────────────

def odd_americana_para_decimal(am: float) -> float:
    """Converte +140 / -175 para decimal.

    Referencias:
      -175 -> 1.5714
      +140 -> 2.40
    """
    if am >= 100:
        return 1.0 + am / 100.0
    if am <= -100:
        return 1.0 + 100.0 / abs(am)
    raise ValueError("odd americana deve ser >= +100 ou <= -100")


# ─────────────────────────── M2 — CLV ──────────────────────────────────────

def clv(odd_apostada: float, odd_fecho_justa: float) -> float:
    """Closing line value.

    CLV = odd_apostada / odd_fecho_justa - 1

    Positivo = apostaste a preco melhor do que o fecho justo (justo = sem margem).
    E o indicador mais rapido para saber se o scanner tem vantagem real:
    centenas de apostas para lucro ser significativo, ~100-200 para CLV estabilizar.

    Referencia: apostada 2.10, fecho justo 1.95 -> ≈ 0.0769
    """
    if odd_fecho_justa <= 1.0 or odd_apostada <= 1.0:
        raise ValueError("odds devem ser > 1")
    return odd_apostada / odd_fecho_justa - 1.0


# ─────────────────────────── Poisson ────────────────────────────────────────

def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) para Poisson(lam)."""
    if lam < 0 or k < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) para Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    acc = math.exp(-lam)
    termo = acc
    for i in range(1, k + 1):
        termo *= lam / i
        acc += termo
    return min(1.0, acc)


def prob_over(linha: float, lam: float) -> float:
    """P(total > linha). Suporta linhas .5 (sem push) e inteiras.

    Para linha .5: threshold = floor(linha) + 1 (ex: 2.5 -> 3+)
    Referencia: linha 2.5, lam 2.6 -> ≈ 0.4816
    """
    if abs(linha - round(linha)) < 1e-9:
        # Linha inteira (ex: 2.0): over = X > 2 = X >= 3. Push se X == 2.
        threshold = int(round(linha)) + 1
    else:
        threshold = int(math.floor(linha)) + 1
    return 1.0 - poisson_cdf(threshold - 1, lam)


# ─────────────────────────── M4 — Grelha Dixon-Coles ───────────────────────

def grelha_dixon_coles(
    lam_c: float, lam_f: float, rho: float = 0.0, max_golos: int = 10
) -> list[list[float]]:
    """Grelha de probabilidades P(golos_casa=i, golos_fora=j).

    Dixon-Coles corrige o Poisson simples que da empates e 0-0/1-1 a menos:
      tau(0,0) = 1 - lam_c*lam_f*rho
      tau(0,1) = 1 + lam_c*rho
      tau(1,0) = 1 + lam_f*rho
      tau(1,1) = 1 - rho
      resto = 1
    rho tipicamente negativo (−0.05 a −0.15 na europa).

    Devolve grelha (max_golos+1) × (max_golos+1), normalizada para somar 1.

    Referencias:
      lam_c=1.5, lam_f=1.1, rho=0:
        casa ≈ 0.4642, empate ≈ 0.2577, fora ≈ 0.2781, Over 2.5 ≈ 0.4816
      lam_c=1.5, lam_f=1.1, rho=-0.1:
        casa ≈ 0.4520, empate ≈ 0.2822, fora ≈ 0.2658
    """
    tam = max_golos + 1
    g = [[0.0] * tam for _ in range(tam)]
    soma = 0.0
    for i in range(tam):
        pi = poisson_pmf(i, lam_c)
        for j in range(tam):
            pj = poisson_pmf(j, lam_f)
            tau = 1.0
            if i == 0 and j == 0:
                tau = 1.0 - lam_c * lam_f * rho
            elif i == 0 and j == 1:
                tau = 1.0 + lam_c * rho
            elif i == 1 and j == 0:
                tau = 1.0 + lam_f * rho
            elif i == 1 and j == 1:
                tau = 1.0 - rho
            v = max(0.0, pi * pj * tau)
            g[i][j] = v
            soma += v
    if soma > 0:
        for i in range(tam):
            for j in range(tam):
                g[i][j] /= soma
    return g


def probs_1x2_da_grelha(grelha: list[list[float]]) -> tuple[float, float, float]:
    """(prob_casa, prob_empate, prob_fora) a partir da grelha."""
    tam = len(grelha)
    casa = empate = fora = 0.0
    for i in range(tam):
        for j in range(tam):
            if i > j:
                casa += grelha[i][j]
            elif i == j:
                empate += grelha[i][j]
            else:
                fora += grelha[i][j]
    return casa, empate, fora


def prob_over_da_grelha(grelha: list[list[float]], linha: float) -> float:
    """P(total > linha) usando a grelha."""
    tam = len(grelha)
    # Para linha .5 queremos i+j >= floor(linha)+1; inteira: i+j >= linha+1
    if abs(linha - round(linha)) < 1e-9:
        thr = int(round(linha)) + 1
    else:
        thr = int(math.floor(linha)) + 1
    p = 0.0
    for i in range(tam):
        for j in range(tam):
            if i + j >= thr:
                p += grelha[i][j]
    return p


def prob_btts_da_grelha(grelha: list[list[float]]) -> float:
    """P(ambas marcam >= 1)."""
    tam = len(grelha)
    p = 0.0
    for i in range(1, tam):
        for j in range(1, tam):
            p += grelha[i][j]
    return p


# ─────────────────────────── M4 — Handicap asiatico ────────────────────────

def _liquida_ah_metade(linha: float, diff: float) -> float:
    """Liquida 1 metade da aposta: devolve 1 (ganha), 0 (push) ou -1 (perde).

    linha positiva = equipa recebe golo(s); linha negativa = da golo(s).
    diff = golos_equipa - golos_adversario (olhando-a para a equipa apostada).
    """
    resultado = diff + linha
    if resultado > 0.0001:
        return 1.0
    if resultado < -0.0001:
        return -1.0
    return 0.0  # push (so para linhas inteiras)


def ev_ah(grelha: list[list[float]], favorito: str, linha: float, odd: float) -> float:
    """EV de aposta no handicap asiatico. favorito = "casa" ou "fora".

    - Linhas .5 (ex -0.5, +1.5): sem push, so ganha/perde.
    - Linhas inteiras (ex 0, -1, +2): push possivel (reembolso).
    - Linhas .25/.75: metade em L-0.25, metade em L+0.25.

    Testes de referencia (lam_c=1.5, lam_f=1.1, rho=0):
      casa -0.5 @ 1.90  -> EV ≈ -0.1179
      casa -0.25 @ 1.75 -> EV ≈ -0.0587
      casa  0.0  @ 1.60 -> EV ≈ +0.0005
    """
    if favorito not in ("casa", "fora"):
        raise ValueError("favorito deve ser 'casa' ou 'fora'")

    def _diff(i: int, j: int) -> float:
        return (i - j) if favorito == "casa" else (j - i)

    # Linhas .25 / .75 → media de 2 metades
    frac = linha - math.floor(linha)
    if abs(frac - 0.25) < 1e-9 or abs(frac - 0.75) < 1e-9:
        ev_a = ev_ah(grelha, favorito, linha - 0.25, odd)
        ev_b = ev_ah(grelha, favorito, linha + 0.25, odd)
        return 0.5 * (ev_a + ev_b)

    tam = len(grelha)
    ev_total = 0.0
    for i in range(tam):
        for j in range(tam):
            p = grelha[i][j]
            if p == 0:
                continue
            r = _liquida_ah_metade(linha, _diff(i, j))
            if r > 0:
                ev_total += p * (odd - 1.0)
            elif r < 0:
                ev_total += p * (-1.0)
            # push: nao soma nada (devolve stake)
    return ev_total


# ─────────────────────────── M12 — Inverter linha total ────────────────────

def inverter_linha_total(
    p_over: float, golos_restantes: int, max_lam: float = 10.0, tol: float = 1e-6
) -> float:
    """A partir da p_over da referencia e golos ja marcados, obter lam_restante.

    Procura binaria: encontra lam tal que P(X >= golos_restantes) = p_over.
    Util no live para derivar lam_restante e depois gerar mercados secundarios.

    Referencias:
      p_over 0.55, faltam 3 golos -> lam ≈ 2.8826
      p_over 0.40, faltam 2 golos -> lam ≈ 1.3764
    """
    if golos_restantes < 1 or not (0 < p_over < 1):
        raise ValueError("golos_restantes >= 1 e 0 < p_over < 1")
    lo, hi = 0.0, max_lam
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        p = 1.0 - poisson_cdf(golos_restantes - 1, mid)
        if abs(p - p_over) < tol:
            return mid
        if p < p_over:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ─────────────────────────── M13 — Banca simples ───────────────────────────

def stake_flat(stake_fixa: float = 5.0) -> float:
    """Stake plano em CHF. Ignora Kelly — util quando banca pequena (50 CHF).

    Decisao do utilizador: comecar com 50 CHF, 5 CHF por sugestao.
    """
    return float(stake_fixa)


def stake_kelly_cap(
    prob: float, odd: float, banca: float,
    frac_kelly: float = 0.25, max_frac: float = 0.02,
    round_to: float = 5.0,
) -> float:
    """Stake Kelly-1/4 em CHF, cap em max_frac da banca, arredondado a round_to.

    Para quando a banca crescer. Por agora usa-se stake_flat.
    """
    f = kelly_quarto(prob, odd, max_frac=max_frac)
    stake = f * banca
    if round_to > 0:
        stake = round_to * round(stake / round_to)
    return max(0.0, stake)


def lucro_esperado_chf(prob: float, odd: float, stake: float) -> float:
    """Lucro esperado em CHF = EV × stake."""
    return ev(prob, odd) * stake


def passa_stop_loss(
    perda_dia: float, perda_semana: float,
    banca_atual: float,
    limite_dia_pct: float = 0.05, limite_semana_pct: float = 0.10,
) -> tuple[bool, str]:
    """True se podemos continuar a apostar. Doc M13.

    perda_dia e perda_semana sao positivas (unidades CHF perdidas).
    Devolve (pode_apostar, motivo).
    """
    if banca_atual <= 0:
        return False, "banca esgotada"
    if perda_dia >= limite_dia_pct * banca_atual:
        return False, f"stop-loss dia (-{limite_dia_pct*100:.0f}%)"
    if perda_semana >= limite_semana_pct * banca_atual:
        return False, f"stop-loss semana (-{limite_semana_pct*100:.0f}%)"
    return True, "ok"
