"""Backtest da estratégia Pinnacle-vs-soft-books em dados históricos.

Diferente do walk-forward com modelo:
  - Não treina nada.
  - Para cada jogo com odds Pinnacle_Closing e odds de uma soft book
    (B365, WH, VC, BW), calcula fair prob via no-vig na Pinnacle.
  - Se EV = fair_prob × odd_soft - 1 > ev_minimo, grava aposta.
  - Resolve com o resultado real do jogo.

Avalia se a estratégia tem edge replicável em 3+ épocas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from apostas.modelos.no_vig import prob_justa_1x2, prob_justa_binaria
from apostas.modelos.value import ev as calcular_ev
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Jogo, OddsFecho

log = get_logger(__name__)


@dataclass
class ApostaSoft:
    data: datetime
    liga_id: int
    jogo_id: int
    casa_soft: str
    mercado: str   # "golos" ou "1x2"
    linha: float | None
    lado: str
    prob_fair: float
    odd_soft: float
    odd_pinnacle: float
    ev: float
    stake: float
    resultado: str  # "ganho" | "perdido"
    lucro: float


@dataclass
class RelatorioSofts:
    epoca_teste: int
    linha: float
    ev_minimo: float
    mercado: str = "golos"
    casa_fair: str = "Pinnacle_Closing"
    casas_soft: list[str] = field(default_factory=list)
    apostas: list[ApostaSoft] = field(default_factory=list)

    @property
    def n_apostas(self) -> int:
        return len(self.apostas)

    @property
    def n_vitorias(self) -> int:
        return sum(1 for a in self.apostas if a.resultado == "ganho")

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
    def hit_rate(self) -> float:
        return self.n_vitorias / self.n_apostas if self.n_apostas else 0.0


def correr(
    epoca_teste: int = 2024,
    linha: float = 2.5,
    ev_minimo: float = 0.03,
    stake: float = 1.0,
    casa_fair: str = "Pinnacle_Closing",
    casas_soft: tuple[str, ...] = ("B365", "WH", "BW", "VC"),
    ligas_nomes: list[str] | None = None,
    mercado: str = "golos",
) -> RelatorioSofts:
    """Backtest da estratégia Pinnacle-fair vs várias soft books.

    `mercado`: 'golos' (over/under `linha`) ou '1x2' (resultado final).
    """
    rel = RelatorioSofts(
        epoca_teste=epoca_teste, linha=linha, ev_minimo=ev_minimo,
        mercado=mercado, casa_fair=casa_fair, casas_soft=list(casas_soft),
    )

    with abrir_sessao() as s:
        q = select(Jogo).where(Jogo.epoca == epoca_teste, Jogo.estado == "terminado")
        if ligas_nomes:
            from apostas.utils.schema import Liga as _Liga
            ids_filtro = s.scalars(
                select(_Liga.id).where(_Liga.nome.in_(ligas_nomes))
            ).all()
            if not ids_filtro:
                raise ValueError(f"Nenhuma liga com nomes: {ligas_nomes}")
            q = q.where(Jogo.liga_id.in_(ids_filtro))
        jogos = s.scalars(q.order_by(Jogo.data_utc)).all()

        log.info(
            "Backtest softs em %d jogos da época %d (mercado=%s, fair=%s, softs=%s)",
            len(jogos), epoca_teste, mercado, casa_fair, casas_soft,
        )

        for jogo in jogos:
            if mercado == "golos":
                _processar_golos(
                    s, jogo, rel, linha, casa_fair, casas_soft,
                    ev_minimo, stake,
                )
            elif mercado == "1x2":
                _processar_1x2(
                    s, jogo, rel, casa_fair, casas_soft, ev_minimo, stake,
                )

    log.info(
        "Backtest softs terminado: %d apostas, ROI=%.2f%%, lucro=%.2f",
        rel.n_apostas, rel.roi * 100, rel.lucro,
    )
    return rel


def _processar_golos(
    s, jogo: Jogo, rel: RelatorioSofts, linha: float, casa_fair: str,
    casas_soft: tuple[str, ...], ev_minimo: float, stake: float,
) -> None:
    odd_pin_over = _odd(s, jogo.id, "golos", linha, "over", casa_fair)
    odd_pin_under = _odd(s, jogo.id, "golos", linha, "under", casa_fair)
    if odd_pin_over is None or odd_pin_under is None:
        return
    try:
        p_over_fair, p_under_fair = prob_justa_binaria(odd_pin_over, odd_pin_under)
    except ValueError:
        return

    golos_total = (jogo.golos_casa or 0) + (jogo.golos_fora or 0)
    foi_over = golos_total > linha

    for casa_soft in casas_soft:
        for lado, p_fair, odd_pin in (
            ("over", p_over_fair, odd_pin_over),
            ("under", p_under_fair, odd_pin_under),
        ):
            odd_soft = _odd(s, jogo.id, "golos", linha, lado, casa_soft)
            if odd_soft is None or odd_soft <= 1.0:
                continue
            ev = calcular_ev(p_fair, odd_soft)
            if ev < ev_minimo:
                continue
            ganhou = (lado == "over" and foi_over) or (
                lado == "under" and not foi_over
            )
            lucro = stake * (odd_soft - 1) if ganhou else -stake
            rel.apostas.append(ApostaSoft(
                data=jogo.data_utc, liga_id=jogo.liga_id,
                jogo_id=jogo.id, casa_soft=casa_soft,
                mercado="golos", linha=linha, lado=lado, prob_fair=p_fair,
                odd_soft=odd_soft, odd_pinnacle=odd_pin,
                ev=ev, stake=stake,
                resultado="ganho" if ganhou else "perdido",
                lucro=lucro,
            ))


def _processar_1x2(
    s, jogo: Jogo, rel: RelatorioSofts, casa_fair: str,
    casas_soft: tuple[str, ...], ev_minimo: float, stake: float,
) -> None:
    odd_pin_c = _odd(s, jogo.id, "1x2", None, "casa", casa_fair)
    odd_pin_x = _odd(s, jogo.id, "1x2", None, "empate", casa_fair)
    odd_pin_f = _odd(s, jogo.id, "1x2", None, "fora", casa_fair)
    if odd_pin_c is None or odd_pin_x is None or odd_pin_f is None:
        return
    try:
        p_c, p_x, p_f = prob_justa_1x2(odd_pin_c, odd_pin_x, odd_pin_f)
    except ValueError:
        return

    # Resultado real
    gc = jogo.golos_casa or 0
    gf = jogo.golos_fora or 0
    if gc > gf:
        resultado_real = "casa"
    elif gc < gf:
        resultado_real = "fora"
    else:
        resultado_real = "empate"

    for casa_soft in casas_soft:
        for lado, p_fair, odd_pin in (
            ("casa", p_c, odd_pin_c),
            ("empate", p_x, odd_pin_x),
            ("fora", p_f, odd_pin_f),
        ):
            odd_soft = _odd(s, jogo.id, "1x2", None, lado, casa_soft)
            if odd_soft is None or odd_soft <= 1.0:
                continue
            ev = calcular_ev(p_fair, odd_soft)
            if ev < ev_minimo:
                continue
            ganhou = (lado == resultado_real)
            lucro = stake * (odd_soft - 1) if ganhou else -stake
            rel.apostas.append(ApostaSoft(
                data=jogo.data_utc, liga_id=jogo.liga_id,
                jogo_id=jogo.id, casa_soft=casa_soft,
                mercado="1x2", linha=None, lado=lado, prob_fair=p_fair,
                odd_soft=odd_soft, odd_pinnacle=odd_pin,
                ev=ev, stake=stake,
                resultado="ganho" if ganhou else "perdido",
                lucro=lucro,
            ))


def _odd(
    s, jogo_id: int, mercado: str, linha: float | None, lado: str, casa: str,
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
    return o.odd if o is not None and o.odd > 1.0 else None
