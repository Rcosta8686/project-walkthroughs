"""Backtest walk-forward sobre a tabela ``jogos`` + ``odds_fecho``.

Para cada jogo da época de teste, ordenado por data:
  1. Treina o modelo com todos os jogos terminados estritamente anteriores.
  2. Prevê P(over linha) e P(under linha).
  3. Para cada lado, se EV >= ``ev_minimo`` usando as odds de fecho da
     ``casa_de_apostas_ref`` (default ``Avg_Closing``), regista a aposta.
  4. Após o jogo, resolve-a (ganho/perdido) e atualiza o bankroll.

Resultado: ``RelatorioBacktest`` com métricas globais e lista de apostas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from apostas.modelos.poisson import JogoHistorico, ModeloPoisson
from apostas.modelos.value import ev as calcular_ev
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Jogo, OddsFecho

log = get_logger(__name__)


@dataclass
class ApostaSimulada:
    data: datetime
    liga_id: int
    jogo_id: int
    casa_id: int
    fora_id: int
    lado: str                   # "over" | "under"
    linha: float
    prob_modelo: float
    odd: float
    ev: float
    stake: float
    resultado: str              # "ganho" | "perdido"
    lucro: float                # +stake*(odd-1) ou -stake


@dataclass
class RelatorioBacktest:
    linha: float
    ev_minimo: float
    casa_de_apostas_ref: str
    meia_vida_dias: float
    epoca_teste: int

    apostas: list[ApostaSimulada] = field(default_factory=list)
    bankroll: list[tuple[datetime, float]] = field(default_factory=list)

    @property
    def n_apostas(self) -> int:
        return len(self.apostas)

    @property
    def n_vitorias(self) -> int:
        return sum(1 for a in self.apostas if a.resultado == "ganho")

    @property
    def hit_rate(self) -> float:
        return self.n_vitorias / self.n_apostas if self.n_apostas else 0.0

    @property
    def stake_total(self) -> float:
        return sum(a.stake for a in self.apostas)

    @property
    def lucro(self) -> float:
        return sum(a.lucro for a in self.apostas)

    @property
    def roi(self) -> float:
        return self.lucro / self.stake_total if self.stake_total else 0.0

    @property
    def max_drawdown(self) -> float:
        if not self.bankroll:
            return 0.0
        pico = 0.0
        dd = 0.0
        for _, b in self.bankroll:
            pico = max(pico, b)
            dd = min(dd, b - pico)
        return abs(dd)


def correr(
    epoca_teste: int = 2024,
    linha: float = 2.5,
    ev_minimo: float = 0.03,
    casa_de_apostas_ref: str = "Avg_Closing",
    meia_vida_dias: float = 365.0,
    stake: float = 1.0,
) -> RelatorioBacktest:
    rel = RelatorioBacktest(
        linha=linha,
        ev_minimo=ev_minimo,
        casa_de_apostas_ref=casa_de_apostas_ref,
        meia_vida_dias=meia_vida_dias,
        epoca_teste=epoca_teste,
    )
    bankroll_corrente = 0.0

    with abrir_sessao() as s:
        jogos_teste = s.scalars(
            select(Jogo)
            .where(Jogo.epoca == epoca_teste, Jogo.estado == "terminado")
            .order_by(Jogo.data_utc)
        ).all()

        log.info("Backtest sobre %d jogos da época %d.", len(jogos_teste), epoca_teste)

        for jogo in jogos_teste:
            historicos = _jogos_antes_de(s, jogo.data_utc, jogo.liga_id)
            if len(historicos) < 10:
                continue  # amostra pequena de mais

            modelo = ModeloPoisson(meia_vida_dias=meia_vida_dias)
            modelo.fit(historicos, referencia=jogo.data_utc)

            try:
                p_over = modelo.prob_over(jogo.casa_id, jogo.fora_id, linha)
            except KeyError:
                continue  # equipa desconhecida pelo modelo
            p_under = 1.0 - p_over

            odd_over = _odd(s, jogo.id, "golos", linha, "over", casa_de_apostas_ref)
            odd_under = _odd(s, jogo.id, "golos", linha, "under", casa_de_apostas_ref)

            golos_total = (jogo.golos_casa or 0) + (jogo.golos_fora or 0)
            foi_over = golos_total > linha

            for lado, p_modelo, odd in (("over", p_over, odd_over), ("under", p_under, odd_under)):
                if odd is None:
                    continue
                ev_val = calcular_ev(p_modelo, odd)
                if ev_val < ev_minimo:
                    continue
                ganhou = (lado == "over" and foi_over) or (lado == "under" and not foi_over)
                lucro = stake * (odd - 1) if ganhou else -stake
                bankroll_corrente += lucro
                rel.bankroll.append((jogo.data_utc, bankroll_corrente))
                rel.apostas.append(
                    ApostaSimulada(
                        data=jogo.data_utc,
                        liga_id=jogo.liga_id,
                        jogo_id=jogo.id,
                        casa_id=jogo.casa_id,
                        fora_id=jogo.fora_id,
                        lado=lado,
                        linha=linha,
                        prob_modelo=p_modelo,
                        odd=odd,
                        ev=ev_val,
                        stake=stake,
                        resultado="ganho" if ganhou else "perdido",
                        lucro=lucro,
                    )
                )

    log.info(
        "Backtest terminado: %d apostas, ROI=%.2f%%, lucro=%.2f unidades",
        rel.n_apostas, rel.roi * 100, rel.lucro,
    )
    return rel


def _jogos_antes_de(s, data: datetime, liga_id: int) -> list[JogoHistorico]:
    rows = s.scalars(
        select(Jogo).where(
            Jogo.data_utc < data,
            Jogo.liga_id == liga_id,
            Jogo.estado == "terminado",
            Jogo.golos_casa.is_not(None),
            Jogo.golos_fora.is_not(None),
        )
    ).all()
    return [
        JogoHistorico(
            data=j.data_utc,
            liga_id=j.liga_id,
            casa_id=j.casa_id,
            fora_id=j.fora_id,
            golos_casa=j.golos_casa,
            golos_fora=j.golos_fora,
        )
        for j in rows
    ]


def _odd(
    s, jogo_id: int, mercado: str, linha: float, lado: str, casa: str
) -> float | None:
    o = s.scalar(
        select(OddsFecho).where(
            OddsFecho.jogo_id == jogo_id,
            OddsFecho.mercado == mercado,
            OddsFecho.linha == linha,
            OddsFecho.lado == lado,
            OddsFecho.casa_de_apostas == casa,
        )
    )
    return o.odd if o else None
