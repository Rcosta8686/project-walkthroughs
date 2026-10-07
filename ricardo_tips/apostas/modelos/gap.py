"""Modelo GAP ratings (Wheatcroft, 2020).

GAP = Generalised Attacking Performance. Em vez de usar golos como sinal
(ruidoso: uma equipa pode ter 20 remates e não marcar), usa-se uma métrica
combinada de **remates + cantos** como proxy do "quanto a equipa pressiona".

Para cada equipa calculamos quatro ratings (ratios à média da liga):
  - atq_casa, atq_fora: a métrica ofensiva média da equipa (quando em casa/fora)
  - def_casa, def_fora: a métrica ofensiva do adversário quando a equipa está
    em casa/fora (quanto mais baixo, melhor defende)

Para prever um jogo:
  E[métrica_ofensiva_casa] = atq_casa_do_casa × def_fora_do_fora × média_liga_casa
  E[métrica_ofensiva_fora] = atq_fora_do_fora × def_casa_do_casa × média_liga_fora

A seguir converte-se para golos esperados através da taxa histórica da liga:
  λ_casa = E[métrica_ofensiva_casa] × taxa_golos_por_metrica (home)
  λ_fora = E[métrica_ofensiva_fora] × taxa_golos_por_metrica (away)

Finalmente, P(over N) via convolução Poisson, como no modelo baseline.

Base: Wheatcroft (2020), "A profitable model for predicting the over/under
market in football", International Journal of Forecasting 36(3): 1121–1137.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class JogoHistoricoGAP:
    data: datetime
    liga_id: int
    casa_id: int
    fora_id: int
    remates_casa: int
    remates_fora: int
    cantos_casa: int
    cantos_fora: int
    golos_casa: int
    golos_fora: int
    xg_casa: float | None = None
    xg_fora: float | None = None


@dataclass
class ParametrosGAP:
    peso_cantos: float
    rho_dixon_coles: float = 0.0
    # Médias por liga
    media_atq_casa_liga: dict[int, float] = field(default_factory=dict)
    media_atq_fora_liga: dict[int, float] = field(default_factory=dict)
    taxa_golos_por_atq_casa: dict[int, float] = field(default_factory=dict)
    taxa_golos_por_atq_fora: dict[int, float] = field(default_factory=dict)
    # Ratings por equipa
    atq_casa: dict[int, float] = field(default_factory=dict)
    atq_fora: dict[int, float] = field(default_factory=dict)
    def_casa: dict[int, float] = field(default_factory=dict)
    def_fora: dict[int, float] = field(default_factory=dict)
    liga_por_equipa: dict[int, int] = field(default_factory=dict)
    # Nº de jogos observados por equipa (usado para shrinkage adaptativo)
    n_jogos_casa: dict[int, float] = field(default_factory=dict)
    n_jogos_fora: dict[int, float] = field(default_factory=dict)


class ModeloGAP:
    """Modelo GAP ratings — ataque/defesa a partir de remates + cantos.

    Opcionalmente aplica correção de Dixon-Coles aos resultados baixos
    (0-0, 1-0, 0-1, 1-1) para corrigir o desvio empírico típico da Poisson
    pura. Também aplica shrinkage mais forte a equipas recém-promovidas
    (poucos jogos na liga).
    """

    def __init__(
        self,
        meia_vida_dias: float = 365.0,
        peso_cantos: float = 0.5,
        prior_jogos: int = 3,
        rho_dixon_coles: float = 0.0,
        shrinkage_threshold: int = 5,
        metrica: str = "shots_corners",  # ou "xg"
    ):
        if metrica not in {"shots_corners", "xg"}:
            raise ValueError(f"Métrica desconhecida: {metrica}")
        self.meia_vida_dias = meia_vida_dias
        self.peso_cantos = peso_cantos
        self.prior_jogos = prior_jogos
        self.rho_dixon_coles = rho_dixon_coles
        self.shrinkage_threshold = shrinkage_threshold
        self.metrica = metrica
        self.parametros = ParametrosGAP(
            peso_cantos=peso_cantos, rho_dixon_coles=rho_dixon_coles,
        )

    def _metrica_casa(self, j: "JogoHistoricoGAP") -> float:
        if self.metrica == "xg":
            if j.xg_casa is None:
                return 0.0
            return float(j.xg_casa)
        return j.remates_casa + self.peso_cantos * j.cantos_casa

    def _metrica_fora(self, j: "JogoHistoricoGAP") -> float:
        if self.metrica == "xg":
            if j.xg_fora is None:
                return 0.0
            return float(j.xg_fora)
        return j.remates_fora + self.peso_cantos * j.cantos_fora

    # ─── Fit ──────────────────────────────────────────────────────────
    def fit(
        self,
        jogos: Iterable[JogoHistoricoGAP],
        referencia: datetime | None = None,
    ) -> None:
        jogos = list(jogos)
        if not jogos:
            raise ValueError("Modelo GAP precisa de pelo menos 1 jogo histórico.")

        referencia = referencia or max(j.data for j in jogos)

        pesos: list[float] = []
        for j in jogos:
            dias = max(0.0, (referencia - j.data).total_seconds() / 86400.0)
            pesos.append(0.5 ** (dias / self.meia_vida_dias))

        # Totais por liga
        total_atq_casa_liga: dict[int, float] = defaultdict(float)
        total_atq_fora_liga: dict[int, float] = defaultdict(float)
        total_golos_casa_liga: dict[int, float] = defaultdict(float)
        total_golos_fora_liga: dict[int, float] = defaultdict(float)
        peso_total_liga: dict[int, float] = defaultdict(float)

        # Se métrica=xg, filtra jogos que não têm xG (evita viés)
        jogos_pesos = [
            (j, w) for j, w in zip(jogos, pesos)
            if self.metrica != "xg" or (j.xg_casa is not None and j.xg_fora is not None)
        ]
        if not jogos_pesos:
            raise ValueError(f"Nenhum jogo com métrica '{self.metrica}' disponível.")

        for j, w in jogos_pesos:
            atq_c = self._metrica_casa(j)
            atq_f = self._metrica_fora(j)
            total_atq_casa_liga[j.liga_id] += w * atq_c
            total_atq_fora_liga[j.liga_id] += w * atq_f
            total_golos_casa_liga[j.liga_id] += w * j.golos_casa
            total_golos_fora_liga[j.liga_id] += w * j.golos_fora
            peso_total_liga[j.liga_id] += w

        media_atq_c = {
            lid: total_atq_casa_liga[lid] / peso_total_liga[lid]
            for lid in peso_total_liga
        }
        media_atq_f = {
            lid: total_atq_fora_liga[lid] / peso_total_liga[lid]
            for lid in peso_total_liga
        }
        # Taxa de conversão: golos por unidade de métrica ofensiva
        taxa_c = {
            lid: (total_golos_casa_liga[lid] / total_atq_casa_liga[lid])
            if total_atq_casa_liga[lid] > 0 else 0.1
            for lid in peso_total_liga
        }
        taxa_f = {
            lid: (total_golos_fora_liga[lid] / total_atq_fora_liga[lid])
            if total_atq_fora_liga[lid] > 0 else 0.1
            for lid in peso_total_liga
        }

        # Agregações por equipa
        atq_c_soma: dict[int, float] = defaultdict(float)
        atq_f_soma: dict[int, float] = defaultdict(float)
        atq_c_sofrido: dict[int, float] = defaultdict(float)  # quando em casa, atq do adversário
        atq_f_sofrido: dict[int, float] = defaultdict(float)
        peso_casa_equipa: dict[int, float] = defaultdict(float)
        peso_fora_equipa: dict[int, float] = defaultdict(float)
        liga_de: dict[int, int] = {}

        for j, w in jogos_pesos:
            atq_c = self._metrica_casa(j)
            atq_f = self._metrica_fora(j)

            liga_de.setdefault(j.casa_id, j.liga_id)
            liga_de.setdefault(j.fora_id, j.liga_id)

            atq_c_soma[j.casa_id] += w * atq_c
            atq_c_sofrido[j.casa_id] += w * atq_f  # defendendo em casa, sofre atq_f
            peso_casa_equipa[j.casa_id] += w

            atq_f_soma[j.fora_id] += w * atq_f
            atq_f_sofrido[j.fora_id] += w * atq_c
            peso_fora_equipa[j.fora_id] += w

        prior_base = float(self.prior_jogos)
        threshold = float(self.shrinkage_threshold)
        atq_casa: dict[int, float] = {}
        def_casa: dict[int, float] = {}
        atq_fora: dict[int, float] = {}
        def_fora: dict[int, float] = {}

        for equipa_id in liga_de:
            lid = liga_de[equipa_id]
            mc = media_atq_c[lid]
            mf = media_atq_f[lid]

            w_c = peso_casa_equipa.get(equipa_id, 0.0)
            w_f = peso_fora_equipa.get(equipa_id, 0.0)

            # Shrinkage adaptativo: equipas com poucos jogos ganham prior maior
            prior_c = _prior_adaptativo(w_c, prior_base, threshold)
            prior_f = _prior_adaptativo(w_f, prior_base, threshold)

            atq_c_eq = (atq_c_soma.get(equipa_id, 0.0) + prior_c * mc) / (w_c + prior_c)
            def_c_eq = (atq_c_sofrido.get(equipa_id, 0.0) + prior_c * mf) / (w_c + prior_c)
            atq_f_eq = (atq_f_soma.get(equipa_id, 0.0) + prior_f * mf) / (w_f + prior_f)
            def_f_eq = (atq_f_sofrido.get(equipa_id, 0.0) + prior_f * mc) / (w_f + prior_f)

            atq_casa[equipa_id] = atq_c_eq / mc if mc > 0 else 1.0
            def_casa[equipa_id] = def_c_eq / mf if mf > 0 else 1.0
            atq_fora[equipa_id] = atq_f_eq / mf if mf > 0 else 1.0
            def_fora[equipa_id] = def_f_eq / mc if mc > 0 else 1.0

        self.parametros = ParametrosGAP(
            peso_cantos=self.peso_cantos,
            rho_dixon_coles=self.rho_dixon_coles,
            media_atq_casa_liga=media_atq_c,
            media_atq_fora_liga=media_atq_f,
            taxa_golos_por_atq_casa=taxa_c,
            taxa_golos_por_atq_fora=taxa_f,
            atq_casa=atq_casa,
            atq_fora=atq_fora,
            def_casa=def_casa,
            def_fora=def_fora,
            liga_por_equipa=liga_de,
            n_jogos_casa=dict(peso_casa_equipa),
            n_jogos_fora=dict(peso_fora_equipa),
        )

    # ─── Predict ──────────────────────────────────────────────────────
    def lambdas(self, casa_id: int, fora_id: int) -> tuple[float, float]:
        p = self.parametros
        liga = p.liga_por_equipa.get(casa_id) or p.liga_por_equipa.get(fora_id)
        if liga is None:
            raise KeyError("Equipa sem liga conhecida; modelo não treinado para ela.")

        mc = p.media_atq_casa_liga[liga]
        mf = p.media_atq_fora_liga[liga]

        atq_c = p.atq_casa.get(casa_id, 1.0) * p.def_fora.get(fora_id, 1.0) * mc
        atq_f = p.atq_fora.get(fora_id, 1.0) * p.def_casa.get(casa_id, 1.0) * mf

        lam_c = max(0.05, atq_c * p.taxa_golos_por_atq_casa[liga])
        lam_f = max(0.05, atq_f * p.taxa_golos_por_atq_fora[liga])
        return lam_c, lam_f

    def prob_total_golos(self, casa_id: int, fora_id: int, max_golos: int = 10) -> list[float]:
        lam_c, lam_f = self.lambdas(casa_id, fora_id)
        pmf_c = [_poisson_pmf(k, lam_c) for k in range(max_golos + 1)]
        pmf_f = [_poisson_pmf(k, lam_f) for k in range(max_golos + 1)]

        rho = self.rho_dixon_coles
        total = [0.0] * (max_golos * 2 + 1)
        soma = 0.0
        for i, pc in enumerate(pmf_c):
            for j, pf in enumerate(pmf_f):
                p = pc * pf
                if rho != 0.0:
                    p *= _tau_dixon_coles(i, j, lam_c, lam_f, rho)
                total[i + j] += p
                soma += p
        # Dixon-Coles quebra a soma=1; renormaliza
        if soma > 0 and abs(soma - 1.0) > 1e-9:
            total = [x / soma for x in total]
        return total

    def prob_over(self, casa_id: int, fora_id: int, linha: float) -> float:
        dist = self.prob_total_golos(casa_id, fora_id)
        limiar = math.floor(linha) + 1
        return sum(dist[limiar:])


def _poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _tau_dixon_coles(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Fator de ajuste de Dixon-Coles para resultados baixos.

    Modifica a independência Poisson pura para refletir que empates 0-0
    e 1-1 são empiricamente mais frequentes do que a Poisson pura prevê.
    """
    if x == 0 and y == 0:
        return max(0.0, 1.0 - lam * mu * rho)
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


def _prior_adaptativo(w_atual: float, prior_base: float, threshold: float) -> float:
    """Prior maior (mais shrinkage) para equipas com poucas observações."""
    if w_atual >= threshold:
        return prior_base
    # Interpolação linear: 1 jogo → prior = 3× base; threshold → base.
    extra = (threshold - w_atual) / threshold
    return prior_base * (1.0 + 2.0 * extra)
