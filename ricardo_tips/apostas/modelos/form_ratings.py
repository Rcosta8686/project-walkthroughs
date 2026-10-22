"""Ratings de forma por equipa a partir de dados historicos.

Calcula, para cada equipa, nas ultimas N jornadas (default 20, com
decay exponencial):

  - xg_por_90       : expected goals marcados por 90min (ataque)
  - xga_por_90      : expected goals sofridos por 90min (defesa)
  - golos_por_90    : golos reais marcados por 90min
  - golos_sofridos_por_90
  - btts_rate       : fraccao de jogos em que ambas marcaram
  - over_2_5_rate   : fraccao de jogos com > 2.5 golos
  - sot_por_90      : remates a baliza por 90min
  - sot_contra_por_90
  - cantos_por_90
  - cards_por_90
  - home_adv        : diferenca golos por 90min em casa vs fora

Separa stats em casa vs fora, porque o home advantage é real
(~0.25-0.35 golos/jogo na europa).

Uso tipico:
    r = RatingsForma(epocas_treino=2)
    rating_casa = r.rating(equipa_id_casa, local="casa")
    rating_fora = r.rating(equipa_id_fora, local="fora")
    lam_esperado = combinar(rating_casa, rating_fora, media_liga)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp

from sqlalchemy import and_, or_, select

from apostas.utils.db import abrir_sessao
from apostas.utils.schema import EstatisticasJogo, Jogo
from apostas.utils.tempo import agora_utc


@dataclass
class RatingEquipa:
    equipa_id: int
    n_jogos: int               # jogos usados no computo
    local: str                 # "casa" | "fora" | "todos"
    xg_por_90: float
    xga_por_90: float
    golos_por_90: float
    golos_sofridos_por_90: float
    btts_rate: float           # 0.0-1.0
    over_2_5_rate: float
    sot_por_90: float
    sot_contra_por_90: float
    cantos_por_90: float
    cards_por_90: float

    def tem_dados_sufis(self, min_jogos: int = 5) -> bool:
        return self.n_jogos >= min_jogos


# Medias europeias long-run (fallback quando equipa tem <5 jogos):
MEDIAS_LIGA_DEFAULT = {
    "xg_por_90":       1.35,   # ~2.7 total por jogo /2
    "xga_por_90":      1.35,
    "golos_por_90":    1.35,
    "golos_sofridos_por_90": 1.35,
    "btts_rate":       0.52,
    "over_2_5_rate":   0.53,
    "sot_por_90":      4.5,
    "sot_contra_por_90": 4.5,
    "cantos_por_90":   5.3,
    "cards_por_90":    2.3,
}


class RatingsForma:
    """Computa ratings de equipa com decay temporal.

    Parameters
    ----------
    max_jogos:
        Numero maximo de jogos a usar (default 20). Mais = mais estavel,
        menos = reage mais rapido a mudanca de forma.
    meia_vida_dias:
        Em dias. Jogos mais antigos pesam menos. 180 = jogo de ha 6 meses
        vale metade de hoje.
    """

    def __init__(
        self,
        max_jogos: int = 20,
        meia_vida_dias: float = 180.0,
        ref_data: datetime | None = None,
    ):
        self.max_jogos = max_jogos
        self.meia_vida_dias = meia_vida_dias
        self.ref_data = ref_data or agora_utc()

    def _peso(self, data_jogo: datetime) -> float:
        """Decay exponencial com meia-vida."""
        dias = (self.ref_data - data_jogo).total_seconds() / 86400.0
        return 0.5 ** (dias / self.meia_vida_dias)

    def rating(self, equipa_id: int, local: str = "todos") -> RatingEquipa:
        """Devolve rating da equipa. local = 'casa' | 'fora' | 'todos'."""
        with abrir_sessao() as s:
            cond_local = []
            if local == "casa":
                cond_local = [Jogo.casa_id == equipa_id]
            elif local == "fora":
                cond_local = [Jogo.fora_id == equipa_id]
            else:
                cond_local = [or_(Jogo.casa_id == equipa_id, Jogo.fora_id == equipa_id)]

            # Puxa jogos jogados (nao agendados) ordenados pelos mais recentes
            jogos = s.scalars(
                select(Jogo).where(
                    and_(
                        Jogo.estado.in_(("terminado", "encerrado", "finished", "ended")),
                        Jogo.golos_casa.isnot(None),
                        Jogo.golos_fora.isnot(None),
                        Jogo.data_utc <= self.ref_data,
                        *cond_local,
                    )
                ).order_by(Jogo.data_utc.desc()).limit(self.max_jogos)
            ).all()

            if not jogos:
                return _rating_default(equipa_id, local, 0)

            soma_peso = 0.0
            xg_pond = 0.0
            xga_pond = 0.0
            golos_pond = 0.0
            golos_sofridos_pond = 0.0
            btts_pond = 0.0
            over_pond = 0.0
            sot_pond = 0.0
            sot_contra_pond = 0.0
            cantos_pond = 0.0
            cards_pond = 0.0

            for j in jogos:
                peso = self._peso(j.data_utc)
                is_casa = (j.casa_id == equipa_id)
                golos_feitos = j.golos_casa if is_casa else j.golos_fora
                golos_sofridos = j.golos_fora if is_casa else j.golos_casa

                # xG/stats vem da tabela EstatisticasJogo
                st_eu = s.scalar(select(EstatisticasJogo).where(
                    EstatisticasJogo.jogo_id == j.id,
                    EstatisticasJogo.equipa_id == equipa_id,
                ))
                st_outro = s.scalar(select(EstatisticasJogo).where(
                    EstatisticasJogo.jogo_id == j.id,
                    EstatisticasJogo.equipa_id != equipa_id,
                ))

                xg_eu = (st_eu.xg if st_eu and st_eu.xg is not None else golos_feitos)
                xg_outro = (st_outro.xg if st_outro and st_outro.xg is not None else golos_sofridos)
                sot_eu = st_eu.remates_a_baliza if st_eu and st_eu.remates_a_baliza is not None else 4.5
                sot_outro = st_outro.remates_a_baliza if st_outro and st_outro.remates_a_baliza is not None else 4.5
                cantos_eu = st_eu.cantos if st_eu and st_eu.cantos is not None else 5.3
                cards_eu_amar = st_eu.cartoes_amarelos if st_eu and st_eu.cartoes_amarelos is not None else 2.3

                btts = 1.0 if golos_feitos > 0 and golos_sofridos > 0 else 0.0
                over_2_5 = 1.0 if (golos_feitos + golos_sofridos) > 2.5 else 0.0

                soma_peso += peso
                xg_pond += peso * xg_eu
                xga_pond += peso * xg_outro
                golos_pond += peso * golos_feitos
                golos_sofridos_pond += peso * golos_sofridos
                btts_pond += peso * btts
                over_pond += peso * over_2_5
                sot_pond += peso * sot_eu
                sot_contra_pond += peso * sot_outro
                cantos_pond += peso * cantos_eu
                cards_pond += peso * cards_eu_amar

            if soma_peso == 0:
                return _rating_default(equipa_id, local, 0)

            return RatingEquipa(
                equipa_id=equipa_id,
                n_jogos=len(jogos),
                local=local,
                xg_por_90=xg_pond / soma_peso,
                xga_por_90=xga_pond / soma_peso,
                golos_por_90=golos_pond / soma_peso,
                golos_sofridos_por_90=golos_sofridos_pond / soma_peso,
                btts_rate=btts_pond / soma_peso,
                over_2_5_rate=over_pond / soma_peso,
                sot_por_90=sot_pond / soma_peso,
                sot_contra_por_90=sot_contra_pond / soma_peso,
                cantos_por_90=cantos_pond / soma_peso,
                cards_por_90=cards_pond / soma_peso,
            )


def _rating_default(equipa_id: int, local: str, n: int) -> RatingEquipa:
    return RatingEquipa(
        equipa_id=equipa_id,
        n_jogos=n,
        local=local,
        **MEDIAS_LIGA_DEFAULT,
    )


def shrinkage(
    rating: RatingEquipa, peso_media: float = 0.3,
    medias: dict[str, float] | None = None,
) -> RatingEquipa:
    """Mistura o rating da equipa com a media de liga.

    Reduz ruido: equipas com poucos jogos ficam mais perto da media.
    peso_media 0 = usa so rating equipa; peso_media 1 = usa so media.
    """
    m = medias or MEDIAS_LIGA_DEFAULT
    # Ajuste adaptativo: menos jogos → mais shrinkage
    peso_eff = max(peso_media, 1 - (rating.n_jogos / 20.0)) if rating.n_jogos < 20 else peso_media

    def mix(valor: float, media_valor: float) -> float:
        return (1 - peso_eff) * valor + peso_eff * media_valor

    return RatingEquipa(
        equipa_id=rating.equipa_id,
        n_jogos=rating.n_jogos,
        local=rating.local,
        xg_por_90=mix(rating.xg_por_90, m["xg_por_90"]),
        xga_por_90=mix(rating.xga_por_90, m["xga_por_90"]),
        golos_por_90=mix(rating.golos_por_90, m["golos_por_90"]),
        golos_sofridos_por_90=mix(rating.golos_sofridos_por_90, m["golos_sofridos_por_90"]),
        btts_rate=mix(rating.btts_rate, m["btts_rate"]),
        over_2_5_rate=mix(rating.over_2_5_rate, m["over_2_5_rate"]),
        sot_por_90=mix(rating.sot_por_90, m["sot_por_90"]),
        sot_contra_por_90=mix(rating.sot_contra_por_90, m["sot_contra_por_90"]),
        cantos_por_90=mix(rating.cantos_por_90, m["cantos_por_90"]),
        cards_por_90=mix(rating.cards_por_90, m["cards_por_90"]),
    )


@dataclass
class ExpectativaJogo:
    """Expectativa pre-match calculada a partir dos ratings das 2 equipas."""
    lam_golos_total: float
    lam_golos_casa: float
    lam_golos_fora: float
    lam_cantos_total: float
    lam_cards_total: float
    p_btts_pre: float
    confianca: str  # "alta" | "media" | "baixa" conforme n_jogos


def expectativa(casa_rating: RatingEquipa, fora_rating: RatingEquipa,
                home_advantage: float = 0.25) -> ExpectativaJogo:
    """Combina ratings de 2 equipas num expected lambda para o jogo.

    Formula:
      lam_casa = (ataque_casa + defesa_fora) / 2 + home_advantage
      lam_fora = (ataque_fora + defesa_casa) / 2
    """
    # Media de ataque de uma e defesa (xGA) da outra
    lam_casa = (casa_rating.xg_por_90 + fora_rating.xga_por_90) / 2 + home_advantage
    lam_fora = (fora_rating.xg_por_90 + casa_rating.xga_por_90) / 2
    lam_casa = max(0.15, lam_casa)
    lam_fora = max(0.10, lam_fora)
    lam_total = lam_casa + lam_fora

    # BTTS pre: usa p(casa marca pelo menos 1) * p(fora marca pelo menos 1)
    p_casa_marca = 1 - exp(-lam_casa)
    p_fora_marca = 1 - exp(-lam_fora)
    p_btts = p_casa_marca * p_fora_marca
    # Mistura com observacao historica
    p_btts = 0.6 * p_btts + 0.4 * ((casa_rating.btts_rate + fora_rating.btts_rate) / 2)

    lam_cantos = (casa_rating.cantos_por_90 + fora_rating.cantos_por_90)
    lam_cards = (casa_rating.cards_por_90 + fora_rating.cards_por_90)

    confianca = (
        "alta" if min(casa_rating.n_jogos, fora_rating.n_jogos) >= 15
        else "media" if min(casa_rating.n_jogos, fora_rating.n_jogos) >= 8
        else "baixa"
    )
    return ExpectativaJogo(
        lam_golos_total=lam_total,
        lam_golos_casa=lam_casa,
        lam_golos_fora=lam_fora,
        lam_cantos_total=lam_cantos,
        lam_cards_total=lam_cards,
        p_btts_pre=p_btts,
        confianca=confianca,
    )
