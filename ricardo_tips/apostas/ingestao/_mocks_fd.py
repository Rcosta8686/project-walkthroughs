"""Gerador de CSVs sintéticos no formato football-data.co.uk.

Produz um round-robin determinístico (seed fixa por liga+época) com
golos amostrados de Poisson usando forças de ataque/defesa por equipa.
As odds são derivadas das probabilidades verdadeiras + margem de 5%,
com perturbação aleatória pequena — para o backtest ter algo plausível
a bater (ou falhar a bater, consoante o modelo).

Para a lista real de colunas, ver https://www.football-data.co.uk/notes.txt
"""

from __future__ import annotations

import math
import random
from io import StringIO

# Equipas por código de liga (football-data)
_EQUIPAS_FD = {
    "E0": [
        ("Man United", 1.15, 0.95),
        ("Newcastle", 1.00, 1.00),
        ("Liverpool", 1.30, 0.85),
        ("Arsenal", 1.25, 0.90),
        ("Chelsea", 1.05, 1.00),
        ("Man City", 1.40, 0.80),
    ],
    "SP1": [
        ("Barcelona", 1.35, 0.85),
        ("Ath Madrid", 1.10, 0.90),
        ("Real Madrid", 1.40, 0.80),
        ("Sociedad", 1.00, 1.00),
        ("Betis", 0.95, 1.05),
        ("Valencia", 0.90, 1.10),
    ],
    "I1": [
        ("Milan", 1.20, 0.95),
        ("Juventus", 1.15, 0.85),
        ("Roma", 1.05, 1.00),
        ("Inter", 1.30, 0.85),
        ("Napoli", 1.25, 0.90),
        ("Lazio", 1.00, 1.00),
    ],
    "D1": [
        ("Bayern Munich", 1.45, 0.80),
        ("Dortmund", 1.20, 0.95),
        ("Leverkusen", 1.15, 0.95),
        ("RB Leipzig", 1.15, 0.95),
        ("Ein Frankfurt", 1.05, 1.05),
        ("Freiburg", 0.95, 1.00),
    ],
    "F1": [
        ("Paris SG", 1.45, 0.80),
        ("Monaco", 1.15, 1.00),
        ("Marseille", 1.10, 1.00),
        ("Lille", 1.05, 1.00),
        ("Nice", 0.95, 1.00),
        ("Lyon", 1.00, 1.00),
    ],
}

_COLUNAS = [
    "Div", "Date", "HomeTeam", "AwayTeam",
    "FTHG", "FTAG", "FTR",
    "HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR",
    "B365H", "B365D", "B365A",
    "B365>2.5", "B365<2.5",
    "AvgCH", "AvgCD", "AvgCA",
    "AvgC>2.5", "AvgC<2.5",
]

_MEDIA_HOME_GOLOS = 1.5  # média da liga
_MEDIA_AWAY_GOLOS = 1.1


def gerar_csv(codigo_liga: str, epoca: int) -> str:
    """Devolve um CSV completo (string) para uma liga + época."""
    if codigo_liga not in _EQUIPAS_FD:
        raise ValueError(f"Código de liga desconhecido: {codigo_liga}")

    rng = random.Random(hash((codigo_liga, epoca)) & 0xFFFFFFFF)
    equipas = _EQUIPAS_FD[codigo_liga]

    linhas = [",".join(_COLUNAS)]
    data_base = 1  # dia 1 de agosto
    for jornada, (casa, fora) in enumerate(_round_robin(equipas), start=1):
        casa_nome, atk_c, def_c = casa
        fora_nome, atk_f, def_f = fora

        lambda_casa = _MEDIA_HOME_GOLOS * atk_c * def_f
        lambda_fora = _MEDIA_AWAY_GOLOS * atk_f * def_c

        golos_casa = _poisson(lambda_casa, rng)
        golos_fora = _poisson(lambda_fora, rng)
        resultado = "H" if golos_casa > golos_fora else ("A" if golos_casa < golos_fora else "D")

        # Estatísticas plausíveis (não entram no modelo deste primeiro backtest)
        remates_casa = _poisson(lambda_casa * 7, rng)
        remates_fora = _poisson(lambda_fora * 7, rng)
        rab_casa = max(0, remates_casa // 3)
        rab_fora = max(0, remates_fora // 3)
        cantos_casa = _poisson(5, rng)
        cantos_fora = _poisson(4, rng)
        ca_casa = rng.randint(0, 4)
        ca_fora = rng.randint(0, 4)
        cv_casa = 1 if rng.random() < 0.07 else 0
        cv_fora = 1 if rng.random() < 0.07 else 0

        # Odds 1X2 (probabilidade "verdadeira" + margem 5%)
        probs = _probs_1x2(lambda_casa, lambda_fora)
        margem = 1.05
        o_h = round(margem / probs[0], 2)
        o_d = round(margem / probs[1], 2)
        o_a = round(margem / probs[2], 2)
        # AvgC = fechamento: ligeiramente mais tight (margem 1.04)
        margem_c = 1.04
        ac_h = round(margem_c / probs[0], 2)
        ac_d = round(margem_c / probs[1], 2)
        ac_a = round(margem_c / probs[2], 2)

        # Odds Over/Under 2.5
        p_over = _prob_over_25(lambda_casa, lambda_fora)
        p_under = 1 - p_over
        ou_margem = 1.045
        o_o = round(ou_margem / p_over, 2)
        o_u = round(ou_margem / p_under, 2)
        ao_c = round(1.03 / p_over, 2)
        au_c = round(1.03 / p_under, 2)

        # Datas espaçadas por jornada
        data_dia = min(28, data_base + (jornada - 1) * 2)
        data = f"{data_dia:02d}/{((jornada - 1) // 14) + 8:02d}/{(epoca + ((jornada - 1) // 14) // 5) % 100:02d}"

        linhas.append(",".join(str(x) for x in [
            codigo_liga, data, casa_nome, fora_nome,
            golos_casa, golos_fora, resultado,
            remates_casa, remates_fora, rab_casa, rab_fora,
            cantos_casa, cantos_fora, ca_casa, ca_fora, cv_casa, cv_fora,
            o_h, o_d, o_a,
            o_o, o_u,
            ac_h, ac_d, ac_a,
            ao_c, au_c,
        ]))

    buf = StringIO()
    buf.write("\n".join(linhas))
    return buf.getvalue()


def _round_robin(equipas: list) -> list[tuple]:
    """Gera todos os jogos de um round-robin de ida e volta."""
    jogos: list[tuple] = []
    n = len(equipas)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            jogos.append((equipas[i], equipas[j]))
    return jogos


def _poisson(lam: float, rng: random.Random) -> int:
    """Amostragem Poisson simples (método de Knuth)."""
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _prob_over_25(l_casa: float, l_fora: float) -> float:
    """P(X + Y >= 3) com X ~ Poisson(l_casa), Y ~ Poisson(l_fora)."""
    acc = 0.0
    for i in range(3):
        for j in range(3 - i):
            acc += _poisson_pmf(i, l_casa) * _poisson_pmf(j, l_fora)
    return 1 - acc


def _probs_1x2(l_casa: float, l_fora: float) -> tuple[float, float, float]:
    """P(casa), P(empate), P(fora) convolvendo duas Poisson até 10 golos cada."""
    p_h = p_d = p_a = 0.0
    for i in range(11):
        for j in range(11):
            p = _poisson_pmf(i, l_casa) * _poisson_pmf(j, l_fora)
            if i > j:
                p_h += p
            elif i < j:
                p_a += p
            else:
                p_d += p
    total = p_h + p_d + p_a
    return p_h / total, p_d / total, p_a / total


def _poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)
