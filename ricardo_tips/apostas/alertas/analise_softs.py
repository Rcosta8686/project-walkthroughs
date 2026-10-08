"""Análise live: estratégia Pinnacle-vs-soft-books.

Usa a Pinnacle como "verdade" implícita do mercado (após remover a
margem de 2%). Compara contra outras casas (Betano, bet365, etc.).
Quando uma soft book está consistentemente acima da prob justa,
há valor matemático.

Não precisa de modelo preditivo próprio — é consumo direto de odds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from apostas.modelos.no_vig import prob_justa_1x2, prob_justa_binaria
from apostas.modelos.value import ev as calcular_ev
from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes, Sugestao

log = get_logger(__name__)

CASA_FAIR = "pinnacle"  # casa usada como fonte da prob justa


@dataclass
class SugestaoSoft:
    sugestao: Sugestao
    jogo: Jogo
    liga: Liga
    casa: Equipa
    fora: Equipa
    casa_soft: str          # casa onde apostar (ex. "betano")
    prob_fair: float        # prob justa segundo Pinnacle
    odd_pinnacle: float
    odd_soft: float


def identificar_sugestoes_softs(
    referencia: datetime | None = None,
    janela_horas: tuple[float, float] = (0.0, 24.0),
    mercados: tuple[str, ...] = ("golos",),
    casas_soft: tuple[str, ...] = ("betano", "bet365", "williamhill", "unibet"),
    ev_minimo_override: float | None = None,
) -> list[SugestaoSoft]:
    """Varre jogos agendados; marca onde soft books divergem da Pinnacle."""
    cfg = load_config()
    vb_cfg = cfg["value_betting"]
    ev_minimo = ev_minimo_override if ev_minimo_override is not None else float(vb_cfg["ev_minimo"])
    ev_maximo = float(vb_cfg.get("ev_maximo", 0.25))
    odd_maxima = float(vb_cfg.get("odd_maxima", 7.0))
    linhas_cfg = cfg["mercados"]["golos"]["linhas"]
    lados_cfg = cfg["mercados"]["golos"]["lados"]

    ref = referencia or datetime.utcnow()
    inicio = ref + timedelta(hours=janela_horas[0])
    fim = ref + timedelta(hours=janela_horas[1])

    novas: list[SugestaoSoft] = []

    with abrir_sessao() as s:
        jogos = s.scalars(
            select(Jogo).where(Jogo.data_utc >= inicio, Jogo.data_utc <= fim)
            .order_by(Jogo.data_utc)
        ).all()
        log.info(
            "A varrer %d jogos entre %s e %s para divergências soft-vs-Pinnacle",
            len(jogos), inicio, fim,
        )

        for jogo in jogos:
            if "golos" in mercados:
                for linha in linhas_cfg:
                    novas.extend(
                        _analisar_golos(s, jogo, linha, lados_cfg, casas_soft,
                                        ev_minimo, ev_maximo, odd_maxima)
                    )
            if "1x2" in mercados:
                novas.extend(
                    _analisar_1x2(s, jogo, casas_soft,
                                  ev_minimo, ev_maximo, odd_maxima)
                )

    log.info("Identificadas %d divergências soft-vs-Pinnacle.", len(novas))
    return novas


def _analisar_golos(
    s, jogo: Jogo, linha: float, lados_cfg: list[str],
    casas_soft: tuple[str, ...],
    ev_minimo: float, ev_maximo: float, odd_maxima: float,
) -> list[SugestaoSoft]:
    out = []
    odd_pin_over = _odd_de(s, jogo.id, "golos", linha, "over", CASA_FAIR)
    odd_pin_under = _odd_de(s, jogo.id, "golos", linha, "under", CASA_FAIR)
    if odd_pin_over is None or odd_pin_under is None:
        return out
    try:
        p_over_fair, p_under_fair = prob_justa_binaria(odd_pin_over, odd_pin_under)
    except ValueError:
        return out

    for lado, p_fair in (("over", p_over_fair), ("under", p_under_fair)):
        if lado not in lados_cfg:
            continue
        for casa_soft in casas_soft:
            odd_soft = _odd_de(s, jogo.id, "golos", linha, lado, casa_soft)
            if odd_soft is None or odd_soft <= 1.0 or odd_soft > odd_maxima:
                continue
            ev = calcular_ev(p_fair, odd_soft)
            if ev < ev_minimo or ev > ev_maximo:
                continue
            odd_pin = odd_pin_over if lado == "over" else odd_pin_under
            nova = _gravar_se_nova(s, jogo, "golos", linha, lado,
                                   p_fair, odd_soft, ev, casa_soft)
            if nova is None:
                continue
            liga = s.get(Liga, jogo.liga_id)
            casa = s.get(Equipa, jogo.casa_id)
            fora = s.get(Equipa, jogo.fora_id)
            out.append(SugestaoSoft(
                sugestao=nova, jogo=jogo, liga=liga, casa=casa, fora=fora,
                casa_soft=casa_soft, prob_fair=p_fair,
                odd_pinnacle=odd_pin, odd_soft=odd_soft,
            ))
    return out


def _analisar_1x2(
    s, jogo: Jogo, casas_soft: tuple[str, ...],
    ev_minimo: float, ev_maximo: float, odd_maxima: float,
) -> list[SugestaoSoft]:
    out = []
    odd_pin_c = _odd_de(s, jogo.id, "1x2", None, "casa", CASA_FAIR)
    odd_pin_x = _odd_de(s, jogo.id, "1x2", None, "empate", CASA_FAIR)
    odd_pin_f = _odd_de(s, jogo.id, "1x2", None, "fora", CASA_FAIR)
    if odd_pin_c is None or odd_pin_x is None or odd_pin_f is None:
        return out
    try:
        p_c, p_x, p_f = prob_justa_1x2(odd_pin_c, odd_pin_x, odd_pin_f)
    except ValueError:
        return out

    for lado, p_fair, odd_pin in (
        ("casa", p_c, odd_pin_c),
        ("empate", p_x, odd_pin_x),
        ("fora", p_f, odd_pin_f),
    ):
        for casa_soft in casas_soft:
            odd_soft = _odd_de(s, jogo.id, "1x2", None, lado, casa_soft)
            if odd_soft is None or odd_soft <= 1.0 or odd_soft > odd_maxima:
                continue
            ev = calcular_ev(p_fair, odd_soft)
            if ev < ev_minimo or ev > ev_maximo:
                continue
            nova = _gravar_se_nova(s, jogo, "1x2", None, lado,
                                   p_fair, odd_soft, ev, casa_soft)
            if nova is None:
                continue
            liga = s.get(Liga, jogo.liga_id)
            casa = s.get(Equipa, jogo.casa_id)
            fora = s.get(Equipa, jogo.fora_id)
            out.append(SugestaoSoft(
                sugestao=nova, jogo=jogo, liga=liga, casa=casa, fora=fora,
                casa_soft=casa_soft, prob_fair=p_fair,
                odd_pinnacle=odd_pin, odd_soft=odd_soft,
            ))
    return out


def _odd_de(
    s, jogo_id: int, mercado: str, linha: float | None,
    lado: str, casa: str,
) -> float | None:
    o = s.scalar(
        select(OddsCorrentes).where(
            OddsCorrentes.jogo_id == jogo_id,
            OddsCorrentes.mercado == mercado,
            OddsCorrentes.linha == linha,
            OddsCorrentes.lado == lado,
            OddsCorrentes.casa_de_apostas == casa,
        ).order_by(OddsCorrentes.timestamp.desc())
    )
    return o.odd if o is not None and o.odd > 1.0 else None


def _gravar_se_nova(
    s, jogo: Jogo, mercado: str, linha: float | None, lado: str,
    prob: float, odd: float, ev: float, casa_soft: str,
) -> Sugestao | None:
    # Idempotente por (jogo, mercado, linha, lado, casa_soft).
    # Sugestao não tem coluna casa; usamos mercado concatenado.
    mercado_marcado = f"{mercado}@{casa_soft}"
    existente = s.scalar(
        select(Sugestao).where(
            Sugestao.jogo_id == jogo.id,
            Sugestao.mercado == mercado_marcado,
            Sugestao.linha == linha,
            Sugestao.lado == lado,
        )
    )
    if existente is not None:
        return None
    sg = Sugestao(
        jogo_id=jogo.id, mercado=mercado_marcado, linha=linha, lado=lado,
        prob_modelo=prob, odd_referencia=odd, ev=ev,
    )
    s.add(sg)
    s.flush()
    return sg
