"""Modelo Poisson simples baseado em golos.

**Baseline, não modelo final.** Serve para validar o pipeline (ingestão →
modelo → EV → backtest). O modelo "real" (GAP ratings de remates e cantos)
só pode ser construído quando a ingestão da API-Football estiver ativa.

Mecânica:

- Para cada equipa, calculam-se duas taxas (ataque e defesa) separadas
  para casa e fora, com peso exponencial decrescente conforme a idade do
  jogo (meia-vida configurável).
- Para prever um jogo: ``lambda_casa = atq_casa × def_fora × media_casa_liga``;
  ``lambda_fora = atq_fora × def_casa × media_fora_liga``.
- A probabilidade de over/under X.5 obtém-se somando o produto convolucional
  de duas Poisson até ao limiar.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class JogoHistorico:
    """DTO leve para alimentar o fit sem acoplar ao SQLAlchemy."""
    data: datetime
    liga_id: int
    casa_id: int
    fora_id: int
    golos_casa: int
    golos_fora: int


@dataclass
class ParametrosPoisson:
    """Resultado do fit; usa-se para predict."""
    media_casa_por_liga: dict[int, float] = field(default_factory=dict)
    media_fora_por_liga: dict[int, float] = field(default_factory=dict)
    atq_casa: dict[int, float] = field(default_factory=dict)
    atq_fora: dict[int, float] = field(default_factory=dict)
    def_casa: dict[int, float] = field(default_factory=dict)
    def_fora: dict[int, float] = field(default_factory=dict)
    liga_por_equipa: dict[int, int] = field(default_factory=dict)


class ModeloPoisson:
    """Fit + predict de probabilidades de over/under golos."""

    def __init__(self, meia_vida_dias: float = 365.0, prior_jogos: int = 3):
        self.meia_vida_dias = meia_vida_dias
        self.prior_jogos = prior_jogos  # shrinkage para equipas com poucos jogos
        self.parametros = ParametrosPoisson()

    # ─── Fit ──────────────────────────────────────────────────────────
    def fit(self, jogos: Iterable[JogoHistorico], referencia: datetime | None = None) -> None:
        jogos = list(jogos)
        if not jogos:
            raise ValueError("Modelo Poisson precisa de pelo menos 1 jogo histórico.")

        referencia = referencia or max(j.data for j in jogos)

        # Pesos por idade (exponencial com meia-vida)
        pesos: list[float] = []
        for j in jogos:
            dias = max(0.0, (referencia - j.data).total_seconds() / 86400.0)
            pesos.append(0.5 ** (dias / self.meia_vida_dias))

        # Médias por liga (home vs away)
        soma_c_por_liga: dict[int, float] = defaultdict(float)
        soma_f_por_liga: dict[int, float] = defaultdict(float)
        peso_por_liga: dict[int, float] = defaultdict(float)

        for j, w in zip(jogos, pesos):
            soma_c_por_liga[j.liga_id] += w * j.golos_casa
            soma_f_por_liga[j.liga_id] += w * j.golos_fora
            peso_por_liga[j.liga_id] += w

        media_c = {
            lid: soma_c_por_liga[lid] / peso_por_liga[lid] for lid in peso_por_liga
        }
        media_f = {
            lid: soma_f_por_liga[lid] / peso_por_liga[lid] for lid in peso_por_liga
        }

        # Ratings por equipa
        golos_marcados_casa: dict[int, float] = defaultdict(float)
        golos_marcados_fora: dict[int, float] = defaultdict(float)
        golos_sofridos_casa: dict[int, float] = defaultdict(float)
        golos_sofridos_fora: dict[int, float] = defaultdict(float)
        peso_casa: dict[int, float] = defaultdict(float)
        peso_fora: dict[int, float] = defaultdict(float)
        liga_de: dict[int, int] = {}

        for j, w in zip(jogos, pesos):
            liga_de.setdefault(j.casa_id, j.liga_id)
            liga_de.setdefault(j.fora_id, j.liga_id)

            golos_marcados_casa[j.casa_id] += w * j.golos_casa
            golos_sofridos_casa[j.casa_id] += w * j.golos_fora
            peso_casa[j.casa_id] += w

            golos_marcados_fora[j.fora_id] += w * j.golos_fora
            golos_sofridos_fora[j.fora_id] += w * j.golos_casa
            peso_fora[j.fora_id] += w

        atq_casa: dict[int, float] = {}
        def_casa: dict[int, float] = {}
        atq_fora: dict[int, float] = {}
        def_fora: dict[int, float] = {}

        prior = float(self.prior_jogos)

        for equipa_id in liga_de:
            lid = liga_de[equipa_id]
            mc = media_c[lid]
            mf = media_f[lid]

            w_c = peso_casa.get(equipa_id, 0.0)
            w_f = peso_fora.get(equipa_id, 0.0)

            # Com shrinkage: equipa com poucos jogos tende a 1.0 (média da liga)
            gm_c = (golos_marcados_casa.get(equipa_id, 0.0) + prior * mc) / (w_c + prior)
            gs_c = (golos_sofridos_casa.get(equipa_id, 0.0) + prior * mf) / (w_c + prior)
            gm_f = (golos_marcados_fora.get(equipa_id, 0.0) + prior * mf) / (w_f + prior)
            gs_f = (golos_sofridos_fora.get(equipa_id, 0.0) + prior * mc) / (w_f + prior)

            atq_casa[equipa_id] = gm_c / mc if mc > 0 else 1.0
            def_casa[equipa_id] = gs_c / mf if mf > 0 else 1.0
            atq_fora[equipa_id] = gm_f / mf if mf > 0 else 1.0
            def_fora[equipa_id] = gs_f / mc if mc > 0 else 1.0

        self.parametros = ParametrosPoisson(
            media_casa_por_liga=media_c,
            media_fora_por_liga=media_f,
            atq_casa=atq_casa,
            atq_fora=atq_fora,
            def_casa=def_casa,
            def_fora=def_fora,
            liga_por_equipa=liga_de,
        )

    # ─── Predict ──────────────────────────────────────────────────────
    def lambdas(self, casa_id: int, fora_id: int) -> tuple[float, float]:
        p = self.parametros
        liga = p.liga_por_equipa.get(casa_id) or p.liga_por_equipa.get(fora_id)
        if liga is None:
            raise KeyError("Equipa sem liga conhecida; modelo não treinado para ela.")
        mc = p.media_casa_por_liga[liga]
        mf = p.media_fora_por_liga[liga]
        atq_c = p.atq_casa.get(casa_id, 1.0)
        def_c = p.def_casa.get(casa_id, 1.0)
        atq_f = p.atq_fora.get(fora_id, 1.0)
        def_f = p.def_fora.get(fora_id, 1.0)
        lam_c = max(0.05, atq_c * def_f * mc)
        lam_f = max(0.05, atq_f * def_c * mf)
        return lam_c, lam_f

    def prob_total_golos(self, casa_id: int, fora_id: int, max_golos: int = 10) -> list[float]:
        """Distribuição P(X+Y = k) para k em 0..max_golos."""
        lam_c, lam_f = self.lambdas(casa_id, fora_id)
        pmf_c = [_poisson_pmf(k, lam_c) for k in range(max_golos + 1)]
        pmf_f = [_poisson_pmf(k, lam_f) for k in range(max_golos + 1)]
        total = [0.0] * (max_golos * 2 + 1)
        for i, pc in enumerate(pmf_c):
            for j, pf in enumerate(pmf_f):
                total[i + j] += pc * pf
        return total

    def prob_over(self, casa_id: int, fora_id: int, linha: float) -> float:
        """P(golos_totais > linha). Para linha X.5, equivale a P(>= ceil(X.5))."""
        dist = self.prob_total_golos(casa_id, fora_id)
        limiar = math.floor(linha) + 1  # ex.: linha=2.5 → precisa de >= 3
        return sum(dist[limiar:])

    def prob_1x2(
        self, casa_id: int, fora_id: int, max_golos: int = 10,
    ) -> tuple[float, float, float]:
        """Devolve (P(casa), P(empate), P(fora)) usando a matriz de resultados."""
        lam_c, lam_f = self.lambdas(casa_id, fora_id)
        pmf_c = [_poisson_pmf(k, lam_c) for k in range(max_golos + 1)]
        pmf_f = [_poisson_pmf(k, lam_f) for k in range(max_golos + 1)]
        p_casa = p_empate = p_fora = 0.0
        for i, pc in enumerate(pmf_c):
            for j, pf in enumerate(pmf_f):
                p = pc * pf
                if i > j:
                    p_casa += p
                elif i == j:
                    p_empate += p
                else:
                    p_fora += p
        return p_casa, p_empate, p_fora


def _poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)
