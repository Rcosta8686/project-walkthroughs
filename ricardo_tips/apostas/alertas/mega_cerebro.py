"""Mega-cerebro: sintese final a partir dos mini-cerebros + estado live.

Le o que os mini-cerebros das 2 equipas dizem (ficha historica), o
ritmo actual em campo (SoT/cantos/cards vs baseline), e as posturas
dos treinadores, e devolve 1 linha com o mercado que esta a ganhar
peso. Nao e uma sugestao de aposta — e uma leitura orientada do jogo
("a jogar assim, esta equipa vai criar mais, apostar em X").

Devolve None quando nao ha sinal claro — melhor silencio que ruido.

Nome | Como se detecta                              | Direccao
cantos | pressao lateral de uma equipa a chasing +    | over cantos
         remates nao a aumentar na mesma proporcao
remates | ambas as equipas acima do baseline em        | over remates/sot
          intensidade de remates
cards | ritmo de amarelos acima + minuto > 55 +        | over amarelos
          equipa a perder perfil chasing
under cantos | equipa dominante a controlar + outra     | under cantos
          sem perfil chasing + minuto > 65

Roadmap (ver docs/MEGA_CEREBRO.md):
  - Fase A: contexto de competicao (UCL, standings, peso do jogo)
  - Fase B: validacao atitude ficha vs campo por janela 10min
  - Fase C: player power (estrela que pode decidir)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SinteseMega:
    """Resultado do mega-cerebro: 1 linha de leitura global."""
    mercado: str          # "cantos" | "remates" | "cards" | "under_cantos"
    direccao: str         # "over" | "under"
    razao: str            # texto curto para o Telegram
    confianca: int        # 1-3 (quantas heuristicas convergem)

    def to_telegram(self) -> str:
        emoji = {"cantos": "🚩", "remates": "💥", "cards": "🟨",
                 "under_cantos": "🚩"}.get(self.mercado, "📊")
        return f"🧠 Mega: {emoji} {self.direccao} {self.mercado} — {self.razao}"


def _ratio_vs_baseline(valor: int | None, baseline: float | None,
                       minuto: int) -> float | None:
    """Devolve ratio actual/esperado para o minuto. None se faltar dado.

    Ex: baseline=10 por 90', minuto=60, valor_actual=9
    -> esperado=10*(60/90)=6.67; ratio=9/6.67=1.35 → 35% acima do normal.
    """
    if valor is None or baseline is None or minuto <= 0 or baseline <= 0:
        return None
    esperado = baseline * (minuto / 90.0)
    if esperado <= 0:
        return None
    return valor / esperado


def sintetizar(
    *,
    minuto: int,
    score_casa: int,
    score_fora: int,
    # Live stats
    sot_c: int | None = None,
    sot_f: int | None = None,
    cantos_c: int | None = None,
    cantos_f: int | None = None,
    cards_c: int | None = None,
    cards_f: int | None = None,
    # Baselines (de SeccaoBaseline do ficha_equipa)
    baseline_sot_c: float | None = None,
    baseline_sot_f: float | None = None,
    baseline_cantos_c: float | None = None,
    baseline_cantos_f: float | None = None,
    baseline_cards_c: float | None = None,
    baseline_cards_f: float | None = None,
    # Perfis de posse/chasing (de SeccaoBaseline / SeccaoPorResultado)
    posse_c: float | None = None,
    posse_f: float | None = None,
    remates_a_perder_c: float | None = None,
    remates_a_perder_f: float | None = None,
    baseline_remates_c: float | None = None,
    baseline_remates_f: float | None = None,
) -> Optional[SinteseMega]:
    """Combina estado live + perfis das 2 equipas em 1 leitura.

    Devolve None se sinal fraco. Ordem de prioridade:
      1. amarelos a somar (prioritario — risco imediato)
      2. remates ambas acima do baseline (sinal forte)
      3. pressao lateral (cantos sem subida de remates)
      4. equipa dominante a controlar jogo (under cantos)
    """
    diff_c = score_casa - score_fora
    minuto_safe = max(1, minuto)

    perfil_dom_c = posse_c is not None and posse_c >= 56
    perfil_dom_f = posse_f is not None and posse_f >= 56
    perfil_chasing_c = (
        remates_a_perder_c is not None and baseline_remates_c
        and remates_a_perder_c > baseline_remates_c * 1.15
    )
    perfil_chasing_f = (
        remates_a_perder_f is not None and baseline_remates_f
        and remates_a_perder_f > baseline_remates_f * 1.15
    )

    r_sot_c = _ratio_vs_baseline(sot_c, baseline_sot_c, minuto_safe)
    r_sot_f = _ratio_vs_baseline(sot_f, baseline_sot_f, minuto_safe)
    r_can_c = _ratio_vs_baseline(cantos_c, baseline_cantos_c, minuto_safe)
    r_can_f = _ratio_vs_baseline(cantos_f, baseline_cantos_f, minuto_safe)
    r_cards_c = _ratio_vs_baseline(cards_c, baseline_cards_c, minuto_safe)
    r_cards_f = _ratio_vs_baseline(cards_f, baseline_cards_f, minuto_safe)

    # Heuristica 1: amarelos a somar quando jogo quente
    cards_total = (cards_c or 0) + (cards_f or 0)
    if minuto >= 55 and cards_total >= 3:
        n_lados_acima = sum(1 for r in (r_cards_c, r_cards_f)
                            if r is not None and r >= 1.4)
        if n_lados_acima >= 1:
            return SinteseMega(
                mercado="cards", direccao="over",
                razao=f"amarelos acima do normal ({cards_total} ao {minuto}')",
                confianca=min(3, 1 + n_lados_acima),
            )

    # Heuristica 2: ambas as equipas com remates acima do baseline
    if (r_sot_c is not None and r_sot_f is not None
            and r_sot_c >= 1.25 and r_sot_f >= 1.25
            and minuto >= 35):
        return SinteseMega(
            mercado="remates", direccao="over",
            razao=f"ambas acima do ritmo normal — jogo aberto ao {minuto}'",
            confianca=3,
        )

    # Heuristica 3: pressao lateral — cantos a subir, remates nao
    pressao_lateral = False
    razao_pressao = ""
    for lado, r_can, r_sot, nome_lado in (
        (diff_c, r_can_c, r_sot_c, "casa"),
        (-diff_c, r_can_f, r_sot_f, "fora"),
    ):
        # Equipa a perder (lado < 0) com cantos acima e SoT nao proporcional
        if (lado < 0 and r_can is not None and r_can >= 1.4
                and (r_sot is None or r_sot < r_can - 0.3)):
            pressao_lateral = True
            razao_pressao = (
                f"equipa a perder com {r_can:.1f}x cantos "
                f"vs normal — pressao pelas pontas"
            )
            break
    if pressao_lateral:
        return SinteseMega(
            mercado="cantos", direccao="over",
            razao=razao_pressao, confianca=2,
        )

    # Heuristica 4: equipa dominante a controlar — under cantos
    # (equipa a ganhar, dominante, ritmo abaixo; adversario sem perfil chasing)
    if minuto >= 65 and diff_c >= 1 and perfil_dom_c:
        if r_can_c is not None and r_can_c < 0.8 and not perfil_chasing_f:
            return SinteseMega(
                mercado="under_cantos", direccao="under",
                razao="dominante a controlar, baixou ritmo pelas pontas",
                confianca=2,
            )
    if minuto >= 65 and diff_c <= -1 and perfil_dom_f:
        if r_can_f is not None and r_can_f < 0.8 and not perfil_chasing_c:
            return SinteseMega(
                mercado="under_cantos", direccao="under",
                razao="dominante a controlar, baixou ritmo pelas pontas",
                confianca=2,
            )

    return None


def sintetizar_a_partir_confronto(
    *,
    minuto: int,
    score_casa: int,
    score_fora: int,
    sot_c: int | None, sot_f: int | None,
    cantos_c: int | None, cantos_f: int | None,
    cards_c: int | None, cards_f: int | None,
    ficha_casa=None, ficha_fora=None,
) -> Optional[SinteseMega]:
    """Wrapper que extrai os campos relevantes das FichaEquipa."""
    if ficha_casa is None or ficha_fora is None:
        return None
    try:
        bc, bf = ficha_casa.baseline, ficha_fora.baseline
        prc, prf = ficha_casa.por_resultado, ficha_fora.por_resultado
    except AttributeError:
        return None
    # Se as fichas sao fracas (poucos jogos), nao arrisca sintese
    if ficha_casa.jogos_lidos < 5 or ficha_fora.jogos_lidos < 5:
        return None
    return sintetizar(
        minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        sot_c=sot_c, sot_f=sot_f,
        cantos_c=cantos_c, cantos_f=cantos_f,
        cards_c=cards_c, cards_f=cards_f,
        baseline_sot_c=bc.sot, baseline_sot_f=bf.sot,
        baseline_cantos_c=bc.cantos, baseline_cantos_f=bf.cantos,
        baseline_cards_c=bc.cards_amarelos, baseline_cards_f=bf.cards_amarelos,
        posse_c=bc.posse_pct, posse_f=bf.posse_pct,
        remates_a_perder_c=prc.remates_L, remates_a_perder_f=prf.remates_L,
        baseline_remates_c=bc.remates, baseline_remates_f=bf.remates,
    )


def linha_para_telegram(
    *,
    home_id: int,
    away_id: int,
    minuto: int,
    score_casa: int,
    score_fora: int,
    sot_c: int | None = None, sot_f: int | None = None,
    cantos_c: int | None = None, cantos_f: int | None = None,
    cards_c: int | None = None, cards_f: int | None = None,
    confronto_fn=None,
) -> str:
    """Devolve '\\n🧠 Mega: ...' ou '' quando nao ha sinal.

    Pronta a juntar a uma mensagem Telegram.
    """
    if confronto_fn is None:
        try:
            from apostas.alertas.confronto import analisar_confronto
            confronto_fn = analisar_confronto
        except Exception:  # noqa: BLE001
            return ""
    try:
        res = confronto_fn(
            home_id, away_id,
            minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        )
    except Exception:  # noqa: BLE001
        return ""
    if res is None:
        return ""

    sint = sintetizar_a_partir_confronto(
        minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        sot_c=sot_c, sot_f=sot_f,
        cantos_c=cantos_c, cantos_f=cantos_f,
        cards_c=cards_c, cards_f=cards_f,
        ficha_casa=res.ficha_casa, ficha_fora=res.ficha_fora,
    )
    if sint is None:
        return ""
    return "\n" + sint.to_telegram()
