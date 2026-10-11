"""Analista independente: emite sinais baseados no modelo + estado,
sem depender de odds live da Odds API.

Filosofia: o verdadeiro analista olha para o jogo (xG, momentum, posse,
arbitro, derby, cartoes, etc.) e DECIDE em que mercado ha valor. Depois
procura a odd no bookmaker. Nao espera que o mercado lhe diga as
probabilidades.

Cada Sinal tem:
  - tipo: nome do padrao detectado (ex: "goleada_consolidada", "cantos_pressao")
  - mercado + lado + linha: a aposta recomendada
  - prob_modelo: probabilidade segundo o modelo
  - odd_minima: odd minima que valeria a pena procurar (break-even EV=0)
  - odd_valor: odd desejada para EV=+5
  - racional: lista de factos que suportam o sinal

Scanner usa gerar_sinais(estado, prev) para converter o estado + previsao
num conjunto de sinais candidatos. Entao, se tiver odds live, compara;
se nao, emite sinal como "procura esta odd".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive
    from apostas.modelos.live_predictor import PrevisaoLive


@dataclass
class Sinal:
    tipo: str                      # nome do padrao
    mercado: str                   # golos | cantos | cartoes | btts | remates | ...
    lado: str                      # over | under | sim | nao
    linha: float | None            # linha de mercado tipica
    prob_modelo: float             # P segundo o modelo
    odd_minima: float              # odd para EV=0 (= 1/prob)
    odd_valor: float               # odd para EV=+5% (= 1.05/prob)
    racional: list[str] = field(default_factory=list)
    forca: str = "normal"          # "forte" se >=85% prob, "normal" >=70, "fraco" >=55

    @property
    def forca_num(self) -> int:
        return {"forte": 3, "normal": 2, "fraco": 1}.get(self.forca, 0)


def _classifica_forca(prob: float) -> str:
    if prob >= 0.85:
        return "forte"
    if prob >= 0.70:
        return "normal"
    if prob >= 0.55:
        return "fraco"
    return "ignora"


def _odds_minimas(prob: float, ev_desejado: float = 0.05) -> tuple[float, float]:
    """Devolve (odd_break_even, odd_para_ev_desejado).

    Clamp em 0.999 para probabilidades muito proximas de 1 (ex: Over 2.5
    quando ja ha 4 golos). Senao odd minima vai para infinito.
    """
    prob_c = min(max(prob, 0.01), 0.999)
    odd_be = 1.0 / prob_c
    odd_val = (1.0 + ev_desejado) / prob_c
    return round(odd_be, 2), round(odd_val, 2)


def gerar_sinais(estado: "EstadoLive", prev: "PrevisaoLive",
                 prob_minima: float = 0.55) -> list[Sinal]:
    """Converte o estado + previsao em sinais do analista.

    Nao olha para odds — gera sinais puros do modelo + stats observadas.
    """
    out: list[Sinal] = []

    # ─────────── GOLOS ────────────
    _sinal_golos(estado, prev, out, prob_minima)

    # ─────────── BTTS ────────────
    _sinal_btts(estado, prev, out, prob_minima)

    # ─────────── CANTOS ────────────
    _sinal_cantos(estado, prev, out, prob_minima)

    # ─────────── CARTOES ────────────
    _sinal_cartoes(estado, prev, out, prob_minima)

    # ─────────── REMATES ────────────
    _sinal_remates(estado, prev, out, prob_minima)

    # Filtra os que estao abaixo do limiar
    return [s for s in out if s.prob_modelo >= prob_minima]


# ─────────────────────── Geradores por mercado ───────────────────────

def _add(out: list[Sinal], tipo: str, mercado: str, lado: str, linha: float | None,
         prob: float, racional: list[str]) -> None:
    """Cria e adiciona Sinal se tiver forca minima."""
    forca = _classifica_forca(prob)
    if forca == "ignora":
        return
    odd_be, odd_val = _odds_minimas(prob)
    out.append(Sinal(
        tipo=tipo, mercado=mercado, lado=lado, linha=linha,
        prob_modelo=prob, odd_minima=odd_be, odd_valor=odd_val,
        racional=racional, forca=forca,
    ))


def _sinal_golos(estado, prev, out, prob_min) -> None:
    """Sinais do mercado de golos."""
    golos_actuais = estado.marcador_casa + estado.marcador_fora

    # Over 2.5 forte (jogo aberto)
    p_o25 = prev.p_over_total(2.5)
    if p_o25 >= prob_min:
        racional = [f"marcador actual {estado.marcador_casa}-{estado.marcador_fora}"]
        if estado.xg_casa is not None:
            racional.append(f"xG actual {estado.xg_casa:.1f}+{estado.xg_fora:.1f}")
        if golos_actuais >= 3:
            racional.append("ja tem 3+ golos, Over 2.5 e certeza")
        elif prev.minutos_restantes > 20:
            racional.append(f"{prev.minutos_restantes}min restantes, modelo espera mais golos")
        _add(out, "golos_abertos", "golos", "over", 2.5, p_o25, racional)

    # Under 2.5 forte (jogo fechado)
    p_u25 = 1 - p_o25
    if p_u25 >= prob_min and golos_actuais <= 2:
        racional = [f"marcador {estado.marcador_casa}-{estado.marcador_fora}, {prev.minutos_restantes}min restam"]
        if estado.xg_casa is not None and (estado.xg_casa + estado.xg_fora) < 1.5 and estado.minuto > 45:
            racional.append(f"xG total apenas {estado.xg_casa + estado.xg_fora:.1f} na 2a metade")
        if estado.marcador_casa == estado.marcador_fora and estado.minuto >= 70:
            racional.append("empate late = ambas defendem")
        _add(out, "golos_fechados", "golos", "under", 2.5, p_u25, racional)

    # Over 1.5 (quando 0-0 ao intervalo com stats fracas)
    if golos_actuais == 0 and estado.minuto >= 40 and estado.minuto <= 70:
        p_o15 = prev.p_over_total(1.5)
        if p_o15 >= prob_min:
            racional = ["0-0 em meio-jogo"]
            if estado.xg_casa is not None:
                racional.append(f"xG {estado.xg_casa:.1f}+{estado.xg_fora:.1f} indica golo iminente")
            _add(out, "golo_iminente", "golos", "over", 1.5, p_o15, racional)

    # Over 3.5 se jogo ja esta aberto
    if golos_actuais >= 2 and estado.minuto < 60:
        p_o35 = prev.p_over_total(3.5)
        if p_o35 >= prob_min:
            racional = [f"{golos_actuais} golos ao min {estado.minuto}, cadencia alta"]
            if estado.xg_casa is not None:
                tot_xg = estado.xg_casa + estado.xg_fora
                if tot_xg > golos_actuais:
                    racional.append(f"xG {tot_xg:.1f} > golos actuais, mais a caminho")
            _add(out, "goleada", "golos", "over", 3.5, p_o35, racional)


def _sinal_btts(estado, prev, out, prob_min) -> None:
    """BTTS."""
    # Ambas ja marcaram → nao faz sentido sugerir
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        return
    p_btts = prev.p_btts_final
    if p_btts >= prob_min:
        racional = []
        if estado.marcador_casa == 0 and estado.marcador_fora == 0:
            racional.append("0-0, ambas precisam marcar")
        else:
            lado_falta = "casa" if estado.marcador_casa == 0 else "fora"
            racional.append(f"{lado_falta} precisa marcar")
        if estado.minuto <= 60:
            racional.append(f"restam {prev.minutos_restantes}min, tempo que chegue")
        _add(out, "btts_a_caminho", "btts", "sim", None, p_btts, racional)

    # BTTS Nao se jogo apertado tarde
    p_nao = 1 - p_btts
    if p_nao >= prob_min and estado.minuto >= 60:
        if estado.marcador_casa == 0 or estado.marcador_fora == 0:
            racional = [
                f"ao min {estado.minuto} ainda {estado.marcador_casa}-{estado.marcador_fora}",
                "equipa sem golo nao tem rematado",
            ]
            if estado.xg_casa is not None:
                xg_sem = estado.xg_fora if estado.marcador_casa > 0 else estado.xg_casa
                if xg_sem < 0.5:
                    racional.append(f"xG do lado sem golo so {xg_sem:.1f}")
            _add(out, "btts_nao", "btts", "nao", None, p_nao, racional)


def _sinal_cantos(estado, prev, out, prob_min) -> None:
    """Cantos — depende do rate actual + posse + chasing."""
    cantos_actuais = (estado.corners_casa or 0) + (estado.corners_fora or 0)

    # Over 9.5 forte
    p_o95 = prev.p_over_cantos_total(9.5)
    if p_o95 >= prob_min:
        racional = [f"{cantos_actuais} cantos ao min {estado.minuto}"]
        rate = cantos_actuais / estado.minuto if estado.minuto > 0 else 0
        if rate > 0.11:   # > 10 cantos/90min
            racional.append(f"rate de {rate*90:.1f} cantos/90min (acima da media)")
        if estado.possession_casa is not None:
            posse_diff = abs(estado.possession_casa - 0.5)
            if posse_diff > 0.1:
                racional.append(f"posse dominante ({estado.possession_casa*100:.0f}-{(1-estado.possession_casa)*100:.0f})")
        _add(out, "cantos_altos", "cantos", "over", 9.5, p_o95, racional)

    # Over 10.5 se jogo super aberto
    if cantos_actuais >= 7 and estado.minuto < 70:
        p_o105 = prev.p_over_cantos_total(10.5)
        if p_o105 >= prob_min:
            racional = [f"ja {cantos_actuais} cantos ao min {estado.minuto}"]
            _add(out, "cantos_abertos", "cantos", "over", 10.5, p_o105, racional)

    # Under 8.5 se jogo tranquilo
    p_u85 = 1 - prev.p_over_cantos_total(8.5)
    if p_u85 >= prob_min and estado.minuto >= 60:
        rate = cantos_actuais / estado.minuto if estado.minuto > 0 else 0
        if rate < 0.08:  # < 7.2 cantos/90min
            racional = [
                f"so {cantos_actuais} cantos ao min {estado.minuto}",
                f"rate de {rate*90:.1f} cantos/90min (abaixo da media)",
            ]
            _add(out, "cantos_baixos", "cantos", "under", 8.5, p_u85, racional)


def _sinal_cartoes(estado, prev, out, prob_min) -> None:
    """Cartoes amarelos — depende do rate actual + arbitro + derby + fouls."""
    try:
        from apostas.modelos.referencias import eh_derby, fator_arbitro, rate_arbitro
    except ImportError:
        return

    amarelos_actuais = estado.yellow_cards_casa + estado.yellow_cards_fora
    derby = eh_derby(estado.home, estado.away)
    arb_rate = rate_arbitro(estado.arbitro) if estado.arbitro else 4.3
    arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0

    # Over 4.5 cartoes
    p_o45 = prev.p_over_cartoes_total(4.5)
    if p_o45 >= prob_min:
        racional = [f"{amarelos_actuais} amarelos ao min {estado.minuto}"]
        if derby:
            racional.append("derby, +15% rate historica")
        if arb_sev > 1.1:
            racional.append(f"arbitro {estado.arbitro} tira {arb_rate:.1f} cartoes/jogo (acima media)")
        if estado.red_cards_casa + estado.red_cards_fora > 0:
            racional.append("ja tem vermelho, jogo mais tenso")
        if estado.fouls_casa is not None:
            fpm = (estado.fouls_casa + estado.fouls_fora) / max(estado.minuto, 1)
            if fpm > 0.4:
                racional.append(f"{fpm*90:.0f} faltas/90min (muito acima media)")
        _add(out, "cartoes_altos", "cartoes", "over", 4.5, p_o45, racional)

    # Over 5.5 se derby ou ref muito severo
    if (derby or arb_sev > 1.2) and amarelos_actuais >= 3:
        p_o55 = prev.p_over_cartoes_total(5.5)
        if p_o55 >= prob_min:
            racional = [f"{amarelos_actuais} amarelos ao min {estado.minuto}"]
            if derby:
                racional.append("derby")
            if arb_sev > 1.2:
                racional.append(f"arbitro severo ({arb_rate:.1f} cards/jogo)")
            _add(out, "cartoes_explosao", "cartoes", "over", 5.5, p_o55, racional)

    # Under 3.5 se arbitro brando + sem contexto tenso
    p_u35 = 1 - prev.p_over_cartoes_total(3.5)
    if p_u35 >= prob_min and arb_sev < 0.95 and not derby and estado.minuto >= 60:
        racional = [
            f"so {amarelos_actuais} amarelos ao min {estado.minuto}",
            f"arbitro {estado.arbitro} brando ({arb_rate:.1f} cards/jogo)",
        ]
        _add(out, "cartoes_baixos", "cartoes", "under", 3.5, p_u35, racional)


def _sinal_remates(estado, prev, out, prob_min) -> None:
    """Remates — total e ao alvo."""
    shots_actuais = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    sot_actuais = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)

    # Over 24.5 total shots
    p_o245 = prev.p_over_remates_total(24.5)
    if p_o245 >= prob_min and estado.minuto < 80:
        racional = [f"{shots_actuais} remates ao min {estado.minuto}"]
        rate = shots_actuais / estado.minuto if estado.minuto > 0 else 0
        if rate > 0.27:  # > 24/90
            racional.append(f"rate de {rate*90:.1f} remates/90min")
        _add(out, "remates_altos", "remates", "over", 24.5, p_o245, racional)

    # Over 8.5 SoT
    p_o85_sot = prev.p_over_remates_a_baliza_total(8.5)
    if p_o85_sot >= prob_min and estado.minuto < 80:
        racional = [f"{sot_actuais} remates ao alvo ao min {estado.minuto}"]
        _add(out, "sot_altos", "remates_a_baliza", "over", 8.5, p_o85_sot, racional)
