"""Modelo Poisson para o mercado de cantos (over/under).

Mesma mecânica do modelo baseline de golos, mas treinado em contagens
históricas de cantos em vez de golos. Produz P(total cantos ≥ linha).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class JogoHistoricoCantos:
    data: datetime
    liga_id: int
    casa_id: int
    fora_id: int
    cantos_casa: int
    cantos_fora: int


@dataclass
class ParametrosCantos:
    media_casa_por_liga: dict[int, float] = field(default_factory=dict)
    media_fora_por_liga: dict[int, float] = field(default_factory=dict)
    atq_casa: dict[int, float] = field(default_factory=dict)
    atq_fora: dict[int, float] = field(default_factory=dict)
    def_casa: dict[int, float] = field(default_factory=dict)
    def_fora: dict[int, float] = field(default_factory=dict)
    liga_por_equipa: dict[int, int] = field(default_factory=dict)


class ModeloCantos:
    def __init__(self, meia_vida_dias: float = 365.0, prior_jogos: int = 3):
        self.meia_vida_dias = meia_vida_dias
        self.prior_jogos = prior_jogos
        self.parametros = ParametrosCantos()

    def fit(self, jogos: Iterable[JogoHistoricoCantos], referencia: datetime | None = None) -> None:
        jogos = list(jogos)
        if not jogos:
            raise ValueError("Modelo Cantos precisa de pelo menos 1 jogo histórico.")

        referencia = referencia or max(j.data for j in jogos)
        pesos = [0.5 ** (max(0.0, (referencia - j.data).total_seconds() / 86400.0) / self.meia_vida_dias) for j in jogos]

        soma_c = defaultdict(float)
        soma_f = defaultdict(float)
        peso_liga = defaultdict(float)
        for j, w in zip(jogos, pesos):
            soma_c[j.liga_id] += w * j.cantos_casa
            soma_f[j.liga_id] += w * j.cantos_fora
            peso_liga[j.liga_id] += w

        media_c = {lid: soma_c[lid] / peso_liga[lid] for lid in peso_liga}
        media_f = {lid: soma_f[lid] / peso_liga[lid] for lid in peso_liga}

        gm_casa = defaultdict(float)
        gs_casa = defaultdict(float)
        gm_fora = defaultdict(float)
        gs_fora = defaultdict(float)
        peso_casa = defaultdict(float)
        peso_fora = defaultdict(float)
        liga_de: dict[int, int] = {}

        for j, w in zip(jogos, pesos):
            liga_de.setdefault(j.casa_id, j.liga_id)
            liga_de.setdefault(j.fora_id, j.liga_id)
            gm_casa[j.casa_id] += w * j.cantos_casa
            gs_casa[j.casa_id] += w * j.cantos_fora
            peso_casa[j.casa_id] += w
            gm_fora[j.fora_id] += w * j.cantos_fora
            gs_fora[j.fora_id] += w * j.cantos_casa
            peso_fora[j.fora_id] += w

        prior = float(self.prior_jogos)
        atq_casa = {}
        def_casa = {}
        atq_fora = {}
        def_fora = {}
        for equipa_id, lid in liga_de.items():
            mc, mf = media_c[lid], media_f[lid]
            w_c, w_f = peso_casa.get(equipa_id, 0.0), peso_fora.get(equipa_id, 0.0)

            atq_c_eq = (gm_casa.get(equipa_id, 0.0) + prior * mc) / (w_c + prior)
            def_c_eq = (gs_casa.get(equipa_id, 0.0) + prior * mf) / (w_c + prior)
            atq_f_eq = (gm_fora.get(equipa_id, 0.0) + prior * mf) / (w_f + prior)
            def_f_eq = (gs_fora.get(equipa_id, 0.0) + prior * mc) / (w_f + prior)

            atq_casa[equipa_id] = atq_c_eq / mc if mc > 0 else 1.0
            def_casa[equipa_id] = def_c_eq / mf if mf > 0 else 1.0
            atq_fora[equipa_id] = atq_f_eq / mf if mf > 0 else 1.0
            def_fora[equipa_id] = def_f_eq / mc if mc > 0 else 1.0

        self.parametros = ParametrosCantos(
            media_casa_por_liga=media_c,
            media_fora_por_liga=media_f,
            atq_casa=atq_casa, atq_fora=atq_fora,
            def_casa=def_casa, def_fora=def_fora,
            liga_por_equipa=liga_de,
        )

    def lambdas(self, casa_id: int, fora_id: int) -> tuple[float, float]:
        p = self.parametros
        liga = p.liga_por_equipa.get(casa_id) or p.liga_por_equipa.get(fora_id)
        if liga is None:
            raise KeyError("Equipa sem liga conhecida.")
        mc, mf = p.media_casa_por_liga[liga], p.media_fora_por_liga[liga]
        lam_c = max(0.1, p.atq_casa.get(casa_id, 1.0) * p.def_fora.get(fora_id, 1.0) * mc)
        lam_f = max(0.1, p.atq_fora.get(fora_id, 1.0) * p.def_casa.get(casa_id, 1.0) * mf)
        return lam_c, lam_f

    def prob_total(self, casa_id: int, fora_id: int, max_cantos: int = 25) -> list[float]:
        lam_c, lam_f = self.lambdas(casa_id, fora_id)
        pmf_c = [_poisson_pmf(k, lam_c) for k in range(max_cantos + 1)]
        pmf_f = [_poisson_pmf(k, lam_f) for k in range(max_cantos + 1)]
        total = [0.0] * (max_cantos * 2 + 1)
        for i, pc in enumerate(pmf_c):
            for j, pf in enumerate(pmf_f):
                total[i + j] += pc * pf
        return total

    def prob_over(self, casa_id: int, fora_id: int, linha: float) -> float:
        dist = self.prob_total(casa_id, fora_id)
        limiar = math.floor(linha) + 1
        return sum(dist[limiar:])


def _poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)
