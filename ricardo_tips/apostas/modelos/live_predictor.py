"""Modelo preditivo para in-play.

Dado o estado actual de um jogo (``EstadoLive`` do Sofascore) e opcionais
inputs pré-jogo (xG esperados), estima probabilidades para:

  - P(mais ≥N golos antes do fim)
  - P(BTTS acontecer antes do fim) — útil quando ainda não aconteceu
  - P(mais ≥N cantos antes do fim)
  - P(mais ≥N cartões amarelos antes do fim)
  - P(resultado final = 1 / X / 2) — bayesian update do pré-jogo

A matemática é Poisson time-decayed com ajustes empíricos documentados
no artigo "The Theory of In-play Betting" (Dixon & Robinson 1998, mais
versões modernas tipo Baio-Blangiardo 2010). Os coeficientes aqui são
razoáveis mas não calibrados ao histórico Ricardo Tips — isso virá
depois quando tivermos ≥500 jogos trackeados com stats live.

Nenhum destes outputs é "verdade"; são **probabilidades do modelo**.
Depois comparas com odd da casa (1/odd = prob da casa) e vês se há
edge. Mesmo com modelo imperfeito, se consistentemente apanhas edges
≥5-10%, a filosofia Pinnacle→softs mostra que o volume compensa a
imprecisão (variancia reduz com volume).

Formula central:
  λ_restantes = λ_pre × (90-min)/90 × fator_marcador(diff, min)
  λ_atualizado = w(min) × λ_restantes + (1-w(min)) × λ_from_xG(estado)

onde xG_from_rate = xG_actual / min * minutos_restantes.

P(≥N golos restantes) = 1 - F_Poisson(N-1, λ_atualizado)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import exp
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive


# Calibracao aprendida de jogos passados. aprender_jogos.py gera isto.
_CALIBRACAO_CACHE: dict | None = None


def _carregar_calibracao() -> dict:
    """Le dados/calibracao_live.json uma vez por processo. Se nao existir
    ou tiver menos de 20 amostras, usa calibracao identidade.
    """
    global _CALIBRACAO_CACHE
    if _CALIBRACAO_CACHE is not None:
        return _CALIBRACAO_CACHE
    try:
        from apostas.utils.config import project_root
        f = project_root() / "dados" / "calibracao_live.json"
        if f.exists():
            with f.open(encoding="utf-8") as fp:
                calib = json.load(fp)
            if calib.get("n_amostras", 0) >= 20:
                _CALIBRACAO_CACHE = calib
                return calib
    except Exception:  # noqa: BLE001
        pass
    _CALIBRACAO_CACHE = {}
    return _CALIBRACAO_CACHE


def calibrar_prob(prob_modelo: float, mercado: str = "golos") -> float:
    """Aplica calibracao aprendida. Reduz over-confidence identificado.

    Se o bucket 70-80% historicamente so acerta 65%, uma prob de 75%
    e corrigida para 65%.
    """
    calib = _carregar_calibracao()
    por_bucket = calib.get("por_prob_bucket", {})
    pct = prob_modelo * 100
    bucket = ("<55%" if pct < 55 else
              "55-65%" if pct < 65 else
              "65-75%" if pct < 75 else
              "75-85%" if pct < 85 else
              "85%+")
    stats = por_bucket.get(bucket)
    if stats is None or stats.get("n", 0) < 10:
        return prob_modelo
    hit_rate_real = stats.get("hit_rate", 50) / 100.0
    # Blend: 70% modelo original + 30% ajuste historico (nao sobre-reagir)
    return 0.7 * prob_modelo + 0.3 * hit_rate_real


# ───────────── Fatores empíricos (ajustar quando houver dados Ricardo) ─────────────

# Multiplicadores da rate de golos pelo marcador. Vem de análises
# agregadas: jogo empatado tardio tende a defender (<1), jogo com 1
# golo de diferença late game tem lose-urgency (>1).
# Chave: (diff_absoluto, "early"/"mid"/"late"), diff = goals_feitos - goals_sofridos
_FATOR_MARCADOR = {
    (0, "early"): 1.00,
    (0, "mid"):   0.90,
    (0, "late"):  0.75,   # 0-0 aos 75+ = defender quase sempre
    (1, "early"): 1.00,
    (1, "mid"):   1.05,
    (1, "late"):  1.20,   # losing team pressiona
    (2, "early"): 0.95,
    (2, "mid"):   0.85,
    (2, "late"):  0.80,
    # 3+: garbage time, intensidade baixa
    (3, "early"): 0.90,
    (3, "mid"):   0.70,
    (3, "late"):  0.60,
}


def _fase_do_jogo(minuto: int) -> str:
    if minuto < 30:
        return "early"
    if minuto < 65:
        return "mid"
    return "late"


def _fator_marcador(marcador_casa: int, marcador_fora: int, minuto: int) -> float:
    diff = min(abs(marcador_casa - marcador_fora), 3)
    fase = _fase_do_jogo(minuto)
    return _FATOR_MARCADOR.get((diff, fase), 1.0)


def _poisson_cdf(k: int, lam: float) -> float:
    """F_Poisson(k, lam) = sum_{i=0..k} lam^i * e^-lam / i!"""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    acc = 0.0
    termo = exp(-lam)
    acc += termo
    for i in range(1, k + 1):
        termo *= lam / i
        acc += termo
    return min(1.0, acc)


# ───────────────────────────── API ─────────────────────────────


@dataclass
class PrevisaoLive:
    minuto: int
    minutos_restantes: int
    lam_golos_restantes: float
    lam_cantos_restantes: float
    lam_cartoes_amarelos_restantes: float
    lam_shots_restantes: float = 0.0
    lam_shots_on_target_restantes: float = 0.0
    # Lambda splitting por equipa (para 1X2)
    lam_golos_casa_rest: float = 0.0
    lam_golos_fora_rest: float = 0.0
    # P(≥N golos restantes)
    p_mais_1_golo: float = 0.0
    p_mais_2_golos: float = 0.0
    # P(BTTS final)
    p_btts_final: float = 0.0
    # P(resultado final 1/X/2)
    p_casa_vence: float = 0.0
    p_empate: float = 0.0
    p_fora_vence: float = 0.0

    # Internos
    _marcador_casa: int = 0
    _marcador_fora: int = 0
    _shots_atual: int = 0
    _shots_on_target_atual: int = 0
    _cantos_atual: int = 0
    _amarelos_atual: int = 0

    # P(jogo acaba com Over N golos no total)
    def p_over_total(self, linha_total: float) -> float:
        """P(total golos no final > linha). Usa o marcador actual + λ_restantes."""
        n_actual = self._marcador_casa + self._marcador_fora
        n_para_bater = int(linha_total) - n_actual
        if linha_total % 1 == 0.5:
            # Linhas 2.5 etc: precisamos de >= ceil
            n_para_bater = int(linha_total + 1) - n_actual
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_golos_restantes)

    def p_mais_cantos(self, n: int) -> float:
        """P(mais ≥n cantos nos min restantes)."""
        return 1.0 - _poisson_cdf(n - 1, self.lam_cantos_restantes)

    def p_over_cantos_total(self, linha_total: float) -> float:
        """P(cantos totais no final > linha)."""
        n_para_bater = int(linha_total + 1) - self._cantos_atual if linha_total % 1 == 0.5 \
            else int(linha_total) - self._cantos_atual + 1
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_cantos_restantes)

    def p_mais_cartoes_amarelos(self, n: int) -> float:
        """P(mais ≥n cartoes amarelos nos min restantes)."""
        return 1.0 - _poisson_cdf(n - 1, self.lam_cartoes_amarelos_restantes)

    def p_over_cartoes_total(self, linha_total: float) -> float:
        """P(cartoes amarelos totais > linha)."""
        n_para_bater = int(linha_total + 1) - self._amarelos_atual if linha_total % 1 == 0.5 \
            else int(linha_total) - self._amarelos_atual + 1
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_cartoes_amarelos_restantes)

    def p_mais_remates(self, n: int) -> float:
        return 1.0 - _poisson_cdf(n - 1, self.lam_shots_restantes)

    def p_over_remates_total(self, linha_total: float) -> float:
        n_para_bater = int(linha_total + 1) - self._shots_atual if linha_total % 1 == 0.5 \
            else int(linha_total) - self._shots_atual + 1
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_shots_restantes)

    def p_mais_remates_a_baliza(self, n: int) -> float:
        return 1.0 - _poisson_cdf(n - 1, self.lam_shots_on_target_restantes)

    def p_over_remates_a_baliza_total(self, linha_total: float) -> float:
        n_para_bater = int(linha_total + 1) - self._shots_on_target_atual if linha_total % 1 == 0.5 \
            else int(linha_total) - self._shots_on_target_atual + 1
        if n_para_bater <= 0:
            return 1.0
        return 1.0 - _poisson_cdf(n_para_bater - 1, self.lam_shots_on_target_restantes)


def prever(
    estado: "EstadoLive",
    lam_golos_pre: float | None = None,
    lam_cantos_pre: float | None = None,
    lam_cartoes_pre: float | None = None,
    expectativa_pre=None,
) -> PrevisaoLive:
    """Devolve previsao de eventos restantes para um jogo in-play.

    Parameters
    ----------
    estado:
        Snapshot Sofascore (resultado + xG + shots + cards + posse).
    lam_golos_pre:
        Golos totais esperados pre-match. Default: 2.6 (media europeia).
    lam_cantos_pre:
        Cantos totais esperados pre-match. Default 10.5.
    lam_cartoes_pre:
        Cartoes amarelos totais esperados pre-match. Default 4.0.
    expectativa_pre:
        Objecto ExpectativaJogo (de form_ratings) com ratings historicos
        das 2 equipas. Se passado, override os lam_* defaults.

    Modelo:
      1. Prior: lam_golos_pre * (90-min)/90 * fator_marcador
      2. Update bayesian com xG actual observado (peso cresce com minuto)
      3. Shot quality: SoT/total ratio ajusta a eficiencia esperada
      4. Big chances: cada big chance pendente conta como mini-boost
      5. Intensidade recente (xg_recent nos ultimos 15min)
      6. Posse diferencial afecta rate de cantos
      7. Marcador apertado late = mais cartoes
    """
    if expectativa_pre is not None:
        lam_golos_pre = expectativa_pre.lam_golos_total
        lam_cantos_pre = expectativa_pre.lam_cantos_total
        lam_cartoes_pre = expectativa_pre.lam_cards_total
    lam_golos_pre = lam_golos_pre if lam_golos_pre is not None else 2.6
    lam_cantos_pre = lam_cantos_pre if lam_cantos_pre is not None else 10.5
    lam_cartoes_pre = lam_cartoes_pre if lam_cartoes_pre is not None else 4.0

    minuto = min(90, max(0, estado.minuto))
    minutos_restantes = max(0, 90 - minuto)
    frac_restante = minutos_restantes / 90.0

    fator_m = _fator_marcador(estado.marcador_casa, estado.marcador_fora, minuto)

    # 1. Prior do golo
    lam_golos_rest_prior = lam_golos_pre * frac_restante * fator_m

    # 2. Update com xG efectivo observado
    if estado.xg_casa is not None and estado.xg_fora is not None and minuto > 10:
        xg_actual = estado.xg_casa + estado.xg_fora
        xg_rate_actual = xg_actual / minuto
        lam_from_xg = xg_rate_actual * minutos_restantes * fator_m
        w_pre = max(0.2, 1 - minuto / 90)
        lam_golos_rest = w_pre * lam_golos_rest_prior + (1 - w_pre) * lam_from_xg
    else:
        lam_golos_rest = lam_golos_rest_prior

    # 3. Shot quality: SoT/total ratio. Ratio alto = equipa a tentar e a
    #    encontrar o alvo. Baixo = disparos aleatorios.
    sot_c = estado.shots_on_target_casa or 0
    sot_f = estado.shots_on_target_fora or 0
    tot_c = estado.shots_total_casa or 0
    tot_f = estado.shots_total_fora or 0
    sot_total = sot_c + sot_f
    tot_total = tot_c + tot_f
    if tot_total >= 4:
        sot_ratio = sot_total / tot_total
        # Benchmark europeu ~0.35. Ajuste:
        quality_mult = 0.8 + 0.6 * min(1.0, sot_ratio / 0.35)
        lam_golos_rest *= quality_mult

    # 4. Big chances pendentes: forte sinal de que algo esta a construir
    big_c = estado.big_chances_casa or 0
    big_f = estado.big_chances_fora or 0
    if big_c + big_f > 0 and minuto > 10:
        # Cada big chance que nao resultou em golo ainda pode levar a outro
        golos_actuais = estado.marcador_casa + estado.marcador_fora
        big_total = big_c + big_f
        pendentes = max(0, big_total - golos_actuais)
        lam_golos_rest += 0.10 * pendentes * (frac_restante)

    # 5. Intensidade recente
    if estado.xg_recent_casa is not None and estado.xg_recent_fora is not None:
        xg_recent = estado.xg_recent_casa + estado.xg_recent_fora
        xg_recent_rate = xg_recent / 15.0
        rate_medio_pre_minuto = lam_golos_pre / 90.0
        intensidade = xg_recent_rate / rate_medio_pre_minuto if rate_medio_pre_minuto > 0 else 1.0
        intensidade = min(2.0, max(0.5, intensidade))
        lam_golos_rest *= intensidade

    # Vermelho afecta golos: equipa com 10 marca menos (-30%), adversario mais (+15%)
    # Como nao distinguimos aqui qual equipa, aplicamos o net effect: -15% total.
    reds = estado.red_cards_casa + estado.red_cards_fora
    if reds > 0:
        lam_golos_rest *= (0.85 ** reds)

    # Score chasing: equipa a perder nos ultimos 20 min = +pressao
    diff_sign = estado.marcador_fora - estado.marcador_casa  # positivo = casa a perder
    chasing_late = minutos_restantes <= 25 and abs(diff_sign) in (1, 2)
    fator_chasing = 1.20 if chasing_late else 1.0
    lam_golos_rest *= fator_chasing

    # 6. Cantos: posse diferencial + shots blocked + chasing
    cantos_actual = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    if minuto > 10:
        cantos_rate = cantos_actual / minuto
    else:
        cantos_rate = lam_cantos_pre / 90.0
    lam_cantos_rest = cantos_rate * minutos_restantes * fator_m
    # Posse: equipa dominante gera mais cantos. Posse 70-30 vs 50-50 → +~15%
    if estado.possession_casa is not None:
        posse_diff = abs(estado.possession_casa - 0.5)
        lam_cantos_rest *= (1.0 + 0.3 * posse_diff)
    # Shots blocked recentes correlacionam com cantos (bola a sair pela linha)
    blocked = (estado.shots_blocked_casa or 0) + (estado.shots_blocked_fora or 0)
    if blocked > 0 and minuto > 15:
        blocked_rate = blocked / minuto
        # Cada shot blocked adicional sobe ~0.5 corners/90min equivalente
        lam_cantos_rest *= (1.0 + 0.10 * blocked_rate)
    # Score chasing: late pressao = mais cantos
    lam_cantos_rest *= fator_chasing

    # 7. Cartoes: 2H + jogos apertados + arbitro + rivalidade + fouls
    cards_actual = estado.yellow_cards_casa + estado.yellow_cards_fora
    if minuto > 10:
        cards_rate = cards_actual / minuto
    else:
        cards_rate = lam_cartoes_pre / 90.0
    fator_fase_cards = 1.3 if minuto >= 45 else 1.0
    diff = abs(estado.marcador_casa - estado.marcador_fora)
    fator_card_marcador = 1.1 if (diff <= 1 and minuto >= 70) else 1.0
    fator_red_cards = 1.0 + 0.25 * reds

    # NOVO: fator arbitro
    try:
        from apostas.modelos.referencias import fator_arbitro
        fator_arb = fator_arbitro(estado.arbitro)
    except Exception:  # noqa: BLE001
        fator_arb = 1.0

    # NOVO: fator rivalidade (derby = +15%)
    try:
        from apostas.modelos.referencias import fator_rivalidade
        fator_riv = fator_rivalidade(estado.home, estado.away)
    except Exception:  # noqa: BLE001
        fator_riv = 1.0

    # NOVO: fouls rate como sinal adicional (muitas faltas = mais cartoes)
    fator_fouls = 1.0
    if estado.fouls_casa is not None and estado.fouls_fora is not None and minuto > 15:
        fouls_total = estado.fouls_casa + estado.fouls_fora
        fouls_rate = fouls_total / minuto
        # Benchmark ~0.3 faltas/min. Acima disso aumenta rate de cartoes.
        if fouls_rate > 0.35:
            fator_fouls = 1.0 + min(0.4, (fouls_rate - 0.35) * 1.5)

    lam_cards_rest = (
        cards_rate * minutos_restantes
        * fator_fase_cards * fator_card_marcador * fator_red_cards
        * fator_arb * fator_riv * fator_fouls
    )

    # 8. REMATES (novo mercado): total + ao alvo
    shots_total_actual = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    shots_on_target_actual = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)
    # Baseline europeu: ~24 remates/jogo total, ~8 ao alvo
    if minuto > 10:
        shots_rate = shots_total_actual / minuto
        sot_rate = shots_on_target_actual / minuto
    else:
        shots_rate = 24.0 / 90.0
        sot_rate = 8.0 / 90.0
    lam_shots_rest = shots_rate * minutos_restantes * fator_m * fator_chasing
    lam_sot_rest = sot_rate * minutos_restantes * fator_m * fator_chasing

    # ─── Split lam_golos_rest por equipa (para 1X2) ───────────────
    # Peso: baseia-se em xG actual observado + home advantage + chasing.
    peso_casa = 0.55  # home advantage default (~0.25 golos/jogo)
    if estado.xg_casa is not None and estado.xg_fora is not None and minuto > 10:
        xg_t = estado.xg_casa + estado.xg_fora
        if xg_t > 0.3:
            peso_casa = estado.xg_casa / xg_t
            # mistura com home advantage para evitar amostras pequenas
            peso_casa = 0.65 * peso_casa + 0.35 * 0.55
    # Chasing: equipa a perder empurra mais nos ultimos 25 min
    chasing_amount = 0.08 if minutos_restantes <= 25 else 0.0
    if estado.marcador_casa < estado.marcador_fora:
        peso_casa = min(0.75, peso_casa + chasing_amount)
    elif estado.marcador_fora < estado.marcador_casa:
        peso_casa = max(0.25, peso_casa - chasing_amount)

    # Momentum amplifier: se a equipa a perder TEM momentum, boost extra.
    # Isto captura o "em estadio ves que esta a pressionar" — nao e so
    # o score que importa, e quem esta a atacar.
    if estado.momentum_actual == "casa" and estado.marcador_casa < estado.marcador_fora:
        # Modena-tipo: casa a perder MAS a atacar → +15% chance de reagir
        peso_casa = min(0.80, peso_casa + 0.15)
    elif estado.momentum_actual == "fora" and estado.marcador_fora < estado.marcador_casa:
        peso_casa = max(0.20, peso_casa - 0.15)
    # Momentum reforca o atual lider → +8%
    elif estado.momentum_actual == "casa" and estado.marcador_casa > estado.marcador_fora:
        peso_casa = min(0.80, peso_casa + 0.08)
    elif estado.momentum_actual == "fora" and estado.marcador_fora > estado.marcador_casa:
        peso_casa = max(0.20, peso_casa - 0.08)

    # Big chances pendentes: equipa com muitas chances nao convertidas e
    # mais provavel de marcar nos minutos finais
    bc_c = (estado.big_chances_casa or 0) - estado.marcador_casa
    bc_f = (estado.big_chances_fora or 0) - estado.marcador_fora
    bc_diff = bc_c - bc_f
    if bc_diff >= 2:
        peso_casa = min(0.80, peso_casa + 0.05)
    elif bc_diff <= -2:
        peso_casa = max(0.20, peso_casa - 0.05)
    # Red cards: equipa com 10 cria menos
    if reds > 0:
        # Nao distinguimos qual equipa; assume divisao igual do dano
        pass

    lam_golos_casa_rest = lam_golos_rest * peso_casa
    lam_golos_fora_rest = lam_golos_rest * (1 - peso_casa)

    # 1X2 live: convolui Poisson das 2 equipas para distribuicao final
    p_casa_vence, p_empate, p_fora_vence = _calc_1x2(
        estado.marcador_casa, estado.marcador_fora,
        lam_golos_casa_rest, lam_golos_fora_rest,
    )

    p_mais_1 = 1.0 - _poisson_cdf(0, lam_golos_rest)
    p_mais_2 = 1.0 - _poisson_cdf(1, lam_golos_rest)

    # P(BTTS final)
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        p_btts = 1.0
    else:
        if estado.marcador_casa == 0 and estado.marcador_fora == 0:
            lam_c = lam_golos_rest / 2
            lam_f = lam_golos_rest / 2
            p_casa_marca = 1.0 - exp(-lam_c)
            p_fora_marca = 1.0 - exp(-lam_f)
            p_btts = p_casa_marca * p_fora_marca
        else:
            lam_lado_zero = lam_golos_rest / 2
            p_btts = 1.0 - exp(-lam_lado_zero)
    # Mistura com observacao historica se disponivel
    if expectativa_pre is not None and (estado.marcador_casa == 0 or estado.marcador_fora == 0):
        p_btts = 0.8 * p_btts + 0.2 * expectativa_pre.p_btts_pre

    return PrevisaoLive(
        minuto=minuto,
        minutos_restantes=minutos_restantes,
        lam_golos_restantes=lam_golos_rest,
        lam_cantos_restantes=lam_cantos_rest,
        lam_cartoes_amarelos_restantes=lam_cards_rest,
        lam_shots_restantes=lam_shots_rest,
        lam_shots_on_target_restantes=lam_sot_rest,
        lam_golos_casa_rest=lam_golos_casa_rest,
        lam_golos_fora_rest=lam_golos_fora_rest,
        p_mais_1_golo=p_mais_1,
        p_mais_2_golos=p_mais_2,
        p_btts_final=p_btts,
        p_casa_vence=p_casa_vence,
        p_empate=p_empate,
        p_fora_vence=p_fora_vence,
        _marcador_casa=estado.marcador_casa,
        _marcador_fora=estado.marcador_fora,
        _shots_atual=shots_total_actual,
        _shots_on_target_atual=shots_on_target_actual,
        _cantos_atual=cantos_actual,
        _amarelos_atual=cards_actual,
    )


def _calc_1x2(marcador_casa: int, marcador_fora: int,
              lam_casa_rest: float, lam_fora_rest: float,
              max_golos_rest: int = 8) -> tuple[float, float, float]:
    """Convolui Poisson das 2 equipas para P(casa_vence), P(empate), P(fora_vence).

    Soma sobre todas as combinacoes de (gh, ga) com gh = golos extra casa,
    ga = golos extra fora. Final = marcador_actual + gh/ga.
    """
    p_casa, p_empate, p_fora = 0.0, 0.0, 0.0
    from math import exp
    for gh in range(max_golos_rest + 1):
        # P(exactamente gh golos extra casa)
        if lam_casa_rest > 0:
            p_gh = (lam_casa_rest ** gh) / _fatorial(gh) * exp(-lam_casa_rest)
        else:
            p_gh = 1.0 if gh == 0 else 0.0
        for ga in range(max_golos_rest + 1):
            if lam_fora_rest > 0:
                p_ga = (lam_fora_rest ** ga) / _fatorial(ga) * exp(-lam_fora_rest)
            else:
                p_ga = 1.0 if ga == 0 else 0.0
            p = p_gh * p_ga
            final_casa = marcador_casa + gh
            final_fora = marcador_fora + ga
            if final_casa > final_fora:
                p_casa += p
            elif final_casa < final_fora:
                p_fora += p
            else:
                p_empate += p
    # Normaliza para somar 1 (margem de truncagem)
    total = p_casa + p_empate + p_fora
    if total > 0:
        p_casa /= total
        p_empate /= total
        p_fora /= total
    return p_casa, p_empate, p_fora


def _fatorial(n: int) -> int:
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


def ev_contra_odd(prob: float, odd_casa: float) -> float:
    """EV = p × (odd - 1) - (1 - p). >0 = aposta de valor."""
    if odd_casa <= 1.0 or prob <= 0 or prob >= 1:
        return -1.0
    return prob * (odd_casa - 1) - (1 - prob)
