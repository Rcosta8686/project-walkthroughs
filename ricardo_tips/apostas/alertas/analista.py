"""Analista independente: emite sinais baseados no modelo + estado,
sem depender de odds live da Odds API.

Regra crucial: **nunca sugerir linhas ja decididas**. Se jogo esta 4-2,
Over 2.5 ja aconteceu — nao e aposta, e fait accompli. O analista sugere
sempre linhas LIVE (que ainda podem ir para qualquer lado), tipicamente:

  - Para OVER golos: linha = atual + 0.5 (next line above current)
  - Para UNDER golos: linha = atual + 0.5 (ainda tem de ultrapassar
    para over ganhar; under ganha se parar aqui)
  - Para OVER cantos/cards/shots: idem

Cada Sinal tem:
  - tipo: nome do padrao
  - mercado + lado + linha: a aposta recomendada
  - prob_modelo: probabilidade segundo o modelo
  - odd_minima: 1/prob (break-even)
  - odd_valor: odd que daria +5% EV
  - racional: factos que suportam
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive
    from apostas.modelos.live_predictor import PrevisaoLive


@dataclass
class Sinal:
    tipo: str
    mercado: str
    lado: str
    linha: float | None
    prob_modelo: float
    odd_minima: float
    odd_valor: float
    racional: list[str] = field(default_factory=list)
    forca: str = "normal"

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
    """Devolve (odd_break_even, odd_para_ev_desejado) com clamp."""
    prob_c = min(max(prob, 0.01), 0.98)   # clamp para evitar odds absurdas
    odd_be = 1.0 / prob_c
    odd_val = (1.0 + ev_desejado) / prob_c
    return round(odd_be, 2), round(odd_val, 2)


def _add(out: list, tipo: str, mercado: str, lado: str, linha: float | None,
         prob: float, racional: list[str]) -> None:
    forca = _classifica_forca(prob)
    if forca == "ignora":
        return
    odd_be, odd_val = _odds_minimas(prob)
    out.append(Sinal(
        tipo=tipo, mercado=mercado, lado=lado, linha=linha,
        prob_modelo=prob, odd_minima=odd_be, odd_valor=odd_val,
        racional=racional, forca=forca,
    ))


def gerar_sinais(estado: "EstadoLive", prev: "PrevisaoLive",
                 prob_minima: float = 0.55) -> list[Sinal]:
    """Converte estado + previsao em sinais LIVE do analista.

    Nunca sugere linhas ja consumadas. Para cada mercado procura
    a proxima linha significativa que ainda pode ir para qualquer lado.
    """
    out: list[Sinal] = []
    _sinal_golos(estado, prev, out, prob_minima)
    _sinal_btts(estado, prev, out, prob_minima)
    _sinal_cantos(estado, prev, out, prob_minima)
    _sinal_cartoes(estado, prev, out, prob_minima)
    _sinal_remates(estado, prev, out, prob_minima)
    return [s for s in out if s.prob_modelo >= prob_minima]


# ─────────────── GOLOS ───────────────

def _sinal_golos(estado, prev, out, prob_min) -> None:
    golos = estado.marcador_casa + estado.marcador_fora

    # Linhas live: proxima acima do actual (sempre .5)
    linhas_candidatas = [golos + 0.5, golos + 1.5]

    for linha in linhas_candidatas:
        # Only consider sensible lines (0.5-6.5)
        if linha < 0.5 or linha > 6.5:
            continue

        p_over = prev.p_over_total(linha)
        p_under = 1 - p_over

        # OVER linha
        if p_over >= prob_min:
            racional = [f"marcador {estado.marcador_casa}-{estado.marcador_fora} ao min {estado.minuto}"]
            if estado.xg_casa is not None:
                xg_tot = estado.xg_casa + estado.xg_fora
                racional.append(f"xG total {xg_tot:.1f}")
                if xg_tot > golos + 0.5:
                    racional.append("xG acima dos golos actuais indica mais a caminho")
            if prev.minutos_restantes >= 15:
                racional.append(f"{prev.minutos_restantes}min restam para +1 golo")
            # Score chasing
            diff = abs(estado.marcador_casa - estado.marcador_fora)
            if diff in (1, 2) and prev.minutos_restantes <= 25:
                racional.append("equipa a perder pressiona late")
            _add(out, f"over_{linha}_live", "golos", "over", linha, p_over, racional)

        # UNDER linha (so se jogo esta a abrandar)
        if p_under >= prob_min and prev.minutos_restantes <= 30:
            racional = [f"marcador {estado.marcador_casa}-{estado.marcador_fora} ao min {estado.minuto}"]
            if estado.xg_casa is not None:
                xg_recent = (estado.xg_recent_casa or 0) + (estado.xg_recent_fora or 0)
                if xg_recent < 0.2:
                    racional.append(f"xG ultimos 15min apenas {xg_recent:.1f}")
            if estado.marcador_casa == estado.marcador_fora and estado.minuto >= 75:
                racional.append("empate late — equipas tendem a fechar")
            _add(out, f"under_{linha}_live", "golos", "under", linha, p_under, racional)


# ─────────────── BTTS ───────────────

def _sinal_btts(estado, prev, out, prob_min) -> None:
    # Ja consumado → ignora
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        return
    p_sim = prev.p_btts_final
    if p_sim >= prob_min:
        racional = []
        if estado.marcador_casa == 0 and estado.marcador_fora == 0:
            racional.append(f"0-0 ao min {estado.minuto}, ambas precisam marcar")
        else:
            quem_falta = "casa" if estado.marcador_casa == 0 else "fora"
            racional.append(f"so {quem_falta} precisa marcar")
        if estado.minuto <= 60:
            racional.append(f"{prev.minutos_restantes}min para marcar")
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem >= 0.6:
                racional.append(f"xG do lado sem golo = {xg_sem:.1f} (ja criaram chances)")
        _add(out, "btts_sim_live", "btts", "sim", None, p_sim, racional)

    p_nao = 1 - p_sim
    if p_nao >= prob_min and estado.minuto >= 65:
        racional = [f"ao min {estado.minuto} ainda {estado.marcador_casa}-{estado.marcador_fora}"]
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem < 0.4:
                racional.append(f"xG do lado sem golo so {xg_sem:.1f} (nao estao a criar)")
            else:
                return  # tem xG, pode marcar — nao emite under
        _add(out, "btts_nao_live", "btts", "nao", None, p_nao, racional)


# ─────────────── CANTOS ───────────────

def _sinal_cantos(estado, prev, out, prob_min) -> None:
    cantos = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    linhas = [cantos + 0.5, cantos + 1.5, cantos + 2.5]

    for linha in linhas:
        if linha < 2.5 or linha > 15.5:
            continue
        p_over = prev.p_over_cantos_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = [f"{cantos} cantos ao min {estado.minuto}"]
            rate = cantos / estado.minuto if estado.minuto > 0 else 0
            racional.append(f"rate {rate*90:.1f} cantos/90min")
            if estado.possession_casa is not None:
                posse_diff = abs(estado.possession_casa - 0.5)
                if posse_diff > 0.1:
                    racional.append(f"posse dominante ({estado.possession_casa*100:.0f}-{(1-estado.possession_casa)*100:.0f})")
            if estado.shots_blocked_casa is not None:
                blocked = (estado.shots_blocked_casa or 0) + (estado.shots_blocked_fora or 0)
                if blocked >= 3:
                    racional.append(f"{blocked} remates bloqueados = cantos a caminho")
            _add(out, f"cantos_over_{linha}", "cantos", "over", linha, p_over, racional)

        if p_under >= prob_min and prev.minutos_restantes <= 25:
            racional = [f"so {cantos} cantos ao min {estado.minuto}"]
            rate = cantos / estado.minuto if estado.minuto > 0 else 0
            if rate < 0.08:
                racional.append(f"rate {rate*90:.1f} cantos/90min (muito abaixo da media)")
            _add(out, f"cantos_under_{linha}", "cantos", "under", linha, p_under, racional)


# ─────────────── CARTOES ───────────────

def _sinal_cartoes(estado, prev, out, prob_min) -> None:
    try:
        from apostas.modelos.referencias import eh_derby, fator_arbitro, rate_arbitro
    except ImportError:
        return

    amarelos = estado.yellow_cards_casa + estado.yellow_cards_fora
    derby = eh_derby(estado.home, estado.away)
    arb_rate = rate_arbitro(estado.arbitro) if estado.arbitro else 4.3
    arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
    reds = estado.red_cards_casa + estado.red_cards_fora

    linhas = [amarelos + 0.5, amarelos + 1.5, amarelos + 2.5]

    for linha in linhas:
        if linha < 1.5 or linha > 9.5:
            continue
        p_over = prev.p_over_cartoes_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = [f"{amarelos} amarelos ao min {estado.minuto}"]
            if derby:
                racional.append("⚡DERBY — +15% rate historica")
            if arb_sev > 1.1:
                racional.append(f"arbitro {estado.arbitro} tira {arb_rate:.1f} cards/jogo")
            if reds > 0:
                racional.append(f"{reds} vermelho(s) ja — jogo mais tenso")
            if estado.fouls_casa is not None:
                fpm = (estado.fouls_casa + estado.fouls_fora) / max(estado.minuto, 1)
                if fpm > 0.4:
                    racional.append(f"{fpm*90:.0f} faltas/90min (acima media)")
            _add(out, f"cards_over_{linha}", "cartoes", "over", linha, p_over, racional)

        if p_under >= prob_min and prev.minutos_restantes <= 25 and arb_sev < 1.05 and not derby:
            racional = [
                f"so {amarelos} amarelos ao min {estado.minuto}",
                f"arbitro {estado.arbitro} brando ({arb_rate:.1f} cards/jogo)",
            ]
            _add(out, f"cards_under_{linha}", "cartoes", "under", linha, p_under, racional)


# ─────────────── REMATES ───────────────

def _sinal_remates(estado, prev, out, prob_min) -> None:
    shots = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    sot = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)

    # OVER total shots: proximas linhas acima do actual
    linhas_shots = [shots + 0.5, shots + 2.5, shots + 4.5]
    for linha in linhas_shots:
        if linha < 5.5 or linha > 35.5:
            continue
        p = prev.p_over_remates_total(linha)
        if p >= prob_min and estado.minuto < 80:
            racional = [f"{shots} remates ao min {estado.minuto}"]
            rate = shots / estado.minuto if estado.minuto > 0 else 0
            racional.append(f"rate {rate*90:.1f} remates/90min")
            _add(out, f"shots_over_{linha}", "remates", "over", linha, p, racional)

    # OVER SoT: proximas linhas acima do actual
    linhas_sot = [sot + 0.5, sot + 1.5, sot + 2.5]
    for linha in linhas_sot:
        if linha < 2.5 or linha > 15.5:
            continue
        p = prev.p_over_remates_a_baliza_total(linha)
        if p >= prob_min and estado.minuto < 80:
            racional = [f"{sot} remates ao alvo ao min {estado.minuto}"]
            if estado.big_chances_casa is not None:
                bc = (estado.big_chances_casa or 0) + (estado.big_chances_fora or 0)
                if bc >= 3:
                    racional.append(f"{bc} big chances ja criadas")
            _add(out, f"sot_over_{linha}", "remates_a_baliza", "over", linha, p, racional)
