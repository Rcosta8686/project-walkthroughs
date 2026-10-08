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

from apostas.modelos.cantos import JogoHistoricoCantos, ModeloCantos
from apostas.modelos.gap import JogoHistoricoGAP, ModeloGAP
from apostas.modelos.poisson import JogoHistorico, ModeloPoisson
from apostas.modelos.value import ev as calcular_ev
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import EstatisticasJogo, Jogo, OddsFecho

log = get_logger(__name__)

MODELOS_DISPONIVEIS = ("poisson", "gap", "cantos")
MERCADOS_DISPONIVEIS = ("golos", "cantos")


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
    modelo: str = "poisson"
    mercado: str = "golos"

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
    modelo: str = "gap",
    peso_cantos: float = 0.5,
    rho_dixon_coles: float = 0.0,
    mercado: str = "golos",
    metrica_gap: str = "shots_corners",
) -> RelatorioBacktest:
    if modelo not in MODELOS_DISPONIVEIS:
        raise ValueError(
            f"Modelo '{modelo}' desconhecido. Usa um de: {MODELOS_DISPONIVEIS}"
        )
    if mercado not in MERCADOS_DISPONIVEIS:
        raise ValueError(
            f"Mercado '{mercado}' desconhecido. Usa um de: {MERCADOS_DISPONIVEIS}"
        )
    if mercado == "cantos" and modelo != "cantos":
        modelo = "cantos"  # força o modelo adequado para o mercado

    rel = RelatorioBacktest(
        linha=linha,
        ev_minimo=ev_minimo,
        casa_de_apostas_ref=casa_de_apostas_ref,
        meia_vida_dias=meia_vida_dias,
        epoca_teste=epoca_teste,
        modelo=modelo,
        mercado=mercado,
    )
    bankroll_corrente = 0.0

    with abrir_sessao() as s:
        jogos_teste = s.scalars(
            select(Jogo)
            .where(Jogo.epoca == epoca_teste, Jogo.estado == "terminado")
            .order_by(Jogo.data_utc)
        ).all()

        log.info(
            "Backtest (modelo=%s) sobre %d jogos da época %d.",
            modelo, len(jogos_teste), epoca_teste,
        )

        for jogo in jogos_teste:
            historicos = _historicos_antes_de(s, jogo.data_utc, jogo.liga_id, modelo)
            if len(historicos) < 10:
                continue  # amostra pequena de mais

            modelo_obj = _criar_modelo(
                modelo, meia_vida_dias, peso_cantos, rho_dixon_coles, metrica_gap,
            )
            try:
                modelo_obj.fit(historicos, referencia=jogo.data_utc)
            except ValueError:
                continue  # histórico insuficiente para esta métrica

            try:
                p_over = modelo_obj.prob_over(jogo.casa_id, jogo.fora_id, linha)
            except KeyError:
                continue  # equipa desconhecida pelo modelo
            p_under = 1.0 - p_over

            odd_over = _odd(s, jogo.id, mercado, linha, "over", casa_de_apostas_ref)
            odd_under = _odd(s, jogo.id, mercado, linha, "under", casa_de_apostas_ref)

            if mercado == "golos":
                valor_total = (jogo.golos_casa or 0) + (jogo.golos_fora or 0)
            else:  # cantos — tira da tabela estatisticas_jogo
                valor_total = _total_cantos(s, jogo)
                if valor_total is None:
                    continue
            foi_over = valor_total > linha

            for lado, p_modelo, odd in (("over", p_over, odd_over), ("under", p_under, odd_under)):
                if odd is None or odd <= 1.0:
                    # odd <= 1 significa valor em branco no CSV (gravado como 0)
                    # ou odd impossível; não é apostável.
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


def _criar_modelo(
    modelo: str, meia_vida_dias: float, peso_cantos: float,
    rho_dixon_coles: float = 0.0, metrica_gap: str = "shots_corners",
):
    if modelo == "poisson":
        return ModeloPoisson(meia_vida_dias=meia_vida_dias)
    if modelo == "gap":
        return ModeloGAP(
            meia_vida_dias=meia_vida_dias,
            peso_cantos=peso_cantos,
            rho_dixon_coles=rho_dixon_coles,
            metrica=metrica_gap,
        )
    if modelo == "cantos":
        return ModeloCantos(meia_vida_dias=meia_vida_dias)
    raise ValueError(f"Modelo desconhecido: {modelo}")


def _total_cantos(s, jogo: Jogo) -> int | None:
    stats = s.scalars(
        select(EstatisticasJogo).where(EstatisticasJogo.jogo_id == jogo.id)
    ).all()
    if len(stats) < 2:
        return None
    total = 0
    for st in stats:
        if st.cantos is None:
            return None
        total += st.cantos
    return total


def _historicos_antes_de(s, data: datetime, liga_id: int, modelo: str) -> list:
    """Devolve a lista de jogos históricos no formato esperado pelo modelo."""
    rows = s.scalars(
        select(Jogo).where(
            Jogo.data_utc < data,
            Jogo.liga_id == liga_id,
            Jogo.estado == "terminado",
            Jogo.golos_casa.is_not(None),
            Jogo.golos_fora.is_not(None),
        )
    ).all()

    if modelo == "poisson":
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

    # modelos "gap" e "cantos" → precisam de stats por jogo
    jogo_ids = [j.id for j in rows]
    if not jogo_ids:
        return []
    stats_rows = s.scalars(
        select(EstatisticasJogo).where(EstatisticasJogo.jogo_id.in_(jogo_ids))
    ).all()
    stats_por_jogo: dict[int, dict[int, EstatisticasJogo]] = {}
    for st in stats_rows:
        stats_por_jogo.setdefault(st.jogo_id, {})[st.equipa_id] = st

    if modelo == "cantos":
        out_c: list[JogoHistoricoCantos] = []
        for j in rows:
            por_equipa = stats_por_jogo.get(j.id)
            if not por_equipa:
                continue
            st_c = por_equipa.get(j.casa_id)
            st_f = por_equipa.get(j.fora_id)
            if st_c is None or st_f is None:
                continue
            if st_c.cantos is None or st_f.cantos is None:
                continue
            out_c.append(JogoHistoricoCantos(
                data=j.data_utc, liga_id=j.liga_id,
                casa_id=j.casa_id, fora_id=j.fora_id,
                cantos_casa=st_c.cantos, cantos_fora=st_f.cantos,
            ))
        return out_c

    # modelo == "gap"
    out: list[JogoHistoricoGAP] = []
    for j in rows:
        por_equipa = stats_por_jogo.get(j.id)
        if not por_equipa:
            continue  # jogo sem stats, inutilizável para o modelo GAP
        st_c = por_equipa.get(j.casa_id)
        st_f = por_equipa.get(j.fora_id)
        if st_c is None or st_f is None:
            continue
        if st_c.remates is None or st_f.remates is None:
            continue
        out.append(
            JogoHistoricoGAP(
                data=j.data_utc,
                liga_id=j.liga_id,
                casa_id=j.casa_id,
                fora_id=j.fora_id,
                remates_casa=st_c.remates,
                remates_fora=st_f.remates,
                cantos_casa=st_c.cantos or 0,
                cantos_fora=st_f.cantos or 0,
                golos_casa=j.golos_casa,
                golos_fora=j.golos_fora,
                xg_casa=st_c.xg,
                xg_fora=st_f.xg,
            )
        )
    return out


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
