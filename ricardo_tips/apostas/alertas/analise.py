"""Análise live: identifica jogos nos próximos N horas com EV positivo.

Para cada jogo próximo (dentro de uma janela antes-do-kickoff):
  1. Treina o modelo (GAP por default) com todos os jogos anteriores.
  2. Para cada linha e lado do mercado configurado, calcula P(over/under).
  3. Lê as odds de referência (closing odds disponíveis na BD).
  4. Se EV >= limiar, grava um Sugestao e retorna-o.

Idempotente: não cria sugestões para (jogo, mercado, linha, lado) já existentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import select

from apostas.backtest.walk_forward import _criar_modelo, _historicos_antes_de
from apostas.modelos.value import ev as calcular_ev
from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes, OddsFecho, Sugestao

log = get_logger(__name__)


@dataclass
class SugestaoExpandida:
    """Sugestao + relações carregadas (para enviar via Telegram sem N+1)."""
    sugestao: Sugestao
    jogo: Jogo
    liga: Liga
    casa: Equipa
    fora: Equipa


def identificar_sugestoes(
    referencia: datetime | None = None,
    janela_horas: tuple[float, float] = (0.0, 24.0),
    modelo: str = "gap",
    peso_cantos: float = 0.5,
    rho_dixon_coles: float = 0.1,
) -> list[SugestaoExpandida]:
    """Varre jogos agendados na janela e grava Sugestao para os de EV positivo."""
    cfg = load_config()
    ev_minimo = float(cfg["value_betting"]["ev_minimo"])
    linhas_cfg = cfg["mercados"]["golos"]["linhas"]
    lados_cfg = cfg["mercados"]["golos"]["lados"]
    casa_ref = "Pinnacle_Closing"
    meia_vida = float(cfg["modelo"]["decay_meia_vida_dias"])

    ref = referencia or datetime.utcnow()
    inicio = ref + timedelta(hours=janela_horas[0])
    fim = ref + timedelta(hours=janela_horas[1])

    novas: list[SugestaoExpandida] = []

    with abrir_sessao() as s:
        jogos_proximos = s.scalars(
            select(Jogo).where(
                Jogo.data_utc >= inicio,
                Jogo.data_utc <= fim,
            ).order_by(Jogo.data_utc)
        ).all()

        log.info(
            "A analisar %d jogos entre %s e %s (modelo=%s)",
            len(jogos_proximos), inicio, fim, modelo,
        )

        for jogo in jogos_proximos:
            historicos = _historicos_antes_de(s, jogo.data_utc, jogo.liga_id, modelo)
            if len(historicos) < 10:
                continue

            modelo_obj = _criar_modelo(modelo, meia_vida, peso_cantos, rho_dixon_coles)
            modelo_obj.fit(historicos, referencia=jogo.data_utc)

            try:
                probs_cache: dict[float, tuple[float, float]] = {}
                for linha in linhas_cfg:
                    p_over = modelo_obj.prob_over(jogo.casa_id, jogo.fora_id, linha)
                    probs_cache[linha] = (p_over, 1.0 - p_over)
            except KeyError:
                continue  # equipa desconhecida

            for linha in linhas_cfg:
                p_over, p_under = probs_cache[linha]
                for lado, prob in (("over", p_over), ("under", p_under)):
                    if lado not in lados_cfg:
                        continue
                    odd = _odd_referencia(s, jogo.id, linha, lado, casa_ref)
                    if odd is None:
                        continue
                    ev = calcular_ev(prob, odd)
                    if ev < ev_minimo:
                        continue
                    nova = _gravar_sugestao_se_nova(
                        s, jogo, linha, lado, prob, odd, ev,
                    )
                    if nova is not None:
                        liga = s.get(Liga, jogo.liga_id)
                        casa = s.get(Equipa, jogo.casa_id)
                        fora = s.get(Equipa, jogo.fora_id)
                        novas.append(SugestaoExpandida(
                            sugestao=nova, jogo=jogo, liga=liga, casa=casa, fora=fora,
                        ))

    log.info("Identificadas %d sugestões novas.", len(novas))
    return novas


def sugestoes_na_janela(
    inicio: datetime, fim: datetime
) -> list[SugestaoExpandida]:
    """Devolve sugestões já gravadas na BD cujos jogos caiem na janela."""
    with abrir_sessao() as s:
        rows = s.execute(
            select(Sugestao, Jogo, Liga, Equipa, Equipa)
            .join(Jogo, Sugestao.jogo_id == Jogo.id)
            .join(Liga, Jogo.liga_id == Liga.id)
            .join(Equipa, Equipa.id == Jogo.casa_id)
            .where(Jogo.data_utc >= inicio, Jogo.data_utc <= fim)
            .order_by(Jogo.data_utc)
        ).all()
        # SQLAlchemy devolve o Equipa de casa; precisamos de carregar fora à parte
        out = []
        for sg, j, lg, casa, _ in rows:
            fora = s.get(Equipa, j.fora_id)
            out.append(SugestaoExpandida(
                sugestao=sg, jogo=j, liga=lg, casa=casa, fora=fora,
            ))
        return out


def _odd_referencia(
    s, jogo_id: int, linha: float, lado: str, casa: str
) -> float | None:
    """Prefere odds correntes (The Odds API) às de fecho históricas."""
    corrente = s.scalar(
        select(OddsCorrentes).where(
            OddsCorrentes.jogo_id == jogo_id,
            OddsCorrentes.mercado == "golos",
            OddsCorrentes.linha == linha,
            OddsCorrentes.lado == lado,
            OddsCorrentes.casa_de_apostas == "pinnacle",
        ).order_by(OddsCorrentes.timestamp.desc())
    )
    if corrente is not None:
        return corrente.odd

    o = s.scalar(
        select(OddsFecho).where(
            OddsFecho.jogo_id == jogo_id,
            OddsFecho.mercado == "golos",
            OddsFecho.linha == linha,
            OddsFecho.lado == lado,
            OddsFecho.casa_de_apostas == casa,
        )
    )
    return o.odd if o else None


def _gravar_sugestao_se_nova(
    s, jogo: Jogo, linha: float, lado: str, prob: float, odd: float, ev: float,
) -> Sugestao | None:
    existente = s.scalar(
        select(Sugestao).where(
            Sugestao.jogo_id == jogo.id,
            Sugestao.mercado == "golos",
            Sugestao.linha == linha,
            Sugestao.lado == lado,
        )
    )
    if existente is not None:
        return None
    sg = Sugestao(
        jogo_id=jogo.id, mercado="golos", linha=linha, lado=lado,
        prob_modelo=prob, odd_referencia=odd, ev=ev,
    )
    s.add(sg)
    s.flush()
    return sg
