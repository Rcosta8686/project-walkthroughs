"""Analista independente: emite sinais com reasoning narrativo.

Diferenca face a versao anterior: usa TODOS os sinais do Sofascore
(defesa fraca, momentum, qualidade de distribuicao, duelos, big chances,
etc.) para gerar racionais credíveis.

Cada Sinal traz um `racional` que o Telegram transforma em narrativa
tipo:

  "⚽ OVER 1.5 golos ao min 35 (72%)
   Girona a dominar: 62% posse, 3 big chances, keeper 4 defesas.
   Mallorca defesa a ceder: so 44% tackles ganhos, 11 clearances.
   Momentum nos ultimos 10min: Girona (xG 0.6 vs 0.1)
   Odd alvo 1.40 — procura em casa."

Nao e opiniao vazia — e observacao de dados concretos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive
    from apostas.modelos.live_predictor import PrevisaoLive


# Linhas STANDARD oferecidas pelas casas de apostas (not inventadas pelo
# modelo). Modelo so emite para linhas que casas efectivamente abrem.
# Para cada mercado, lista as linhas tipicas do pre-match / live.
LINHAS_MERCADO = {
    "golos":     [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
    "cantos":    [6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5],
    "cartoes":   [2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5],
    "remates":   [16.5, 18.5, 20.5, 22.5, 24.5, 26.5, 28.5, 30.5],
    "remates_a_baliza": [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
}


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
    titulo: str = ""  # headline que capture o insight principal

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
    prob_c = min(max(prob, 0.01), 0.95)
    return round(1.0 / prob_c, 2), round((1.0 + ev_desejado) / prob_c, 2)


def _add(out: list, tipo: str, mercado: str, lado: str, linha: float | None,
         prob: float, racional: list[str], titulo: str = "") -> None:
    if prob > 0.92:
        return
    forca = _classifica_forca(prob)
    if forca == "ignora":
        return
    odd_be, odd_val = _odds_minimas(prob)
    if odd_val < 1.15:
        return
    out.append(Sinal(
        tipo=tipo, mercado=mercado, lado=lado, linha=linha,
        prob_modelo=prob, odd_minima=odd_be, odd_valor=odd_val,
        racional=racional, forca=forca, titulo=titulo,
    ))


def _estado_demasiado_cedo_para_sinal(estado: "EstadoLive") -> bool:
    """True se o jogo esta demasiado no inicio para o modelo ter sinal credivel.

    Nos primeiros 15-20 min, se nao houver stats concretas, o modelo cai
    para o baseline pre-match — nao e um insight live, e um reflexo da
    expectativa pre-jogo. Nao vale a pena enviar isso ao utilizador.
    """
    return estado.minuto < 20


def gerar_sinais(estado: "EstadoLive", prev: "PrevisaoLive",
                 prob_minima: float = 0.55) -> list[Sinal]:
    """Devolve sinais LIVE com reasoning rico.

    Primeiros 20 minutos: so emite sinais onde o estado observado DA
    evidencia real (ex: 3 big chances ja criadas, defesa a ceder, etc).
    Senao, sinais sao reflexo do baseline pre-match disfarçado de live.
    """
    if _estado_demasiado_cedo_para_sinal(estado):
        return []

    ctx = _construir_contexto(estado, prev)
    out: list[Sinal] = []
    _sinal_golos(estado, prev, ctx, out, prob_minima)
    _sinal_btts(estado, prev, ctx, out, prob_minima)
    _sinal_cantos(estado, prev, ctx, out, prob_minima)
    _sinal_cartoes(estado, prev, ctx, out, prob_minima)
    _sinal_remates(estado, prev, ctx, out, prob_minima)

    # Agrupar por (mercado, lado) e manter a linha MAIS ALTA (over) ou mais
    # BAIXA (under) com prob >= prob_minima. As casas abrem as linhas mais
    # desafiadoras cedo no jogo — emitir Over 2.5 cartoes quando a casa so
    # tem Over 3.5 e inutil. Preferimos a linha mais exigente onde o
    # modelo ainda e confiante.
    melhores: dict[tuple, Sinal] = {}
    for s in out:
        key = (s.mercado, s.lado)
        linha_s = s.linha if s.linha is not None else 0.0
        existente = melhores.get(key)
        if existente is None:
            melhores[key] = s
            continue
        linha_e = existente.linha if existente.linha is not None else 0.0
        # Para OVER: preferir linha ALTA (mais exigente, odd mais atractiva)
        if s.lado in ("over", "sim"):
            if linha_s > linha_e:
                melhores[key] = s
        # Para UNDER: preferir linha BAIXA (mais exigente, odd mais atractiva)
        else:  # under, nao
            if linha_s < linha_e:
                melhores[key] = s
    return [s for s in melhores.values() if s.prob_modelo >= prob_minima]


# ──────────────────── Contexto ricamente observado ────────────────────

def _construir_contexto(estado, prev) -> dict:
    """Extrai observacoes qualitativas do estado para enriquecer rationales.

    Devolve dict com strings prontas a aparecer no Telegram.
    """
    ctx: dict = {}

    # Momentum
    if estado.momentum_actual == "casa":
        ctx["momentum"] = f"{estado.home} com momentum (ultimos 10min)"
    elif estado.momentum_actual == "fora":
        ctx["momentum"] = f"{estado.away} com momentum (ultimos 10min)"

    # Defesa fraca? (<60% tackles won e >10 clearances = pressao)
    def _defesa_fraca(tackles_pct, clearances) -> bool:
        return tackles_pct is not None and tackles_pct < 0.55 and (clearances or 0) >= 10

    if _defesa_fraca(estado.tackles_won_pct_casa, estado.clearances_casa):
        ctx["defesa_fraca_casa"] = f"{estado.home} defesa a ceder ({estado.tackles_won_pct_casa*100:.0f}% tackles ganhos, {estado.clearances_casa} clearances)"
    if _defesa_fraca(estado.tackles_won_pct_fora, estado.clearances_fora):
        ctx["defesa_fraca_fora"] = f"{estado.away} defesa a ceder ({estado.tackles_won_pct_fora*100:.0f}% tackles ganhos, {estado.clearances_fora} clearances)"

    # Dominio territorial (final third entries diferenca grande)
    if (estado.final_third_entries_casa or 0) > (estado.final_third_entries_fora or 0) * 1.4:
        ctx["dominio_territorial"] = f"{estado.home} dominante no final third ({estado.final_third_entries_casa} vs {estado.final_third_entries_fora})"
    elif (estado.final_third_entries_fora or 0) > (estado.final_third_entries_casa or 0) * 1.4:
        ctx["dominio_territorial"] = f"{estado.away} dominante no final third ({estado.final_third_entries_fora} vs {estado.final_third_entries_casa})"

    # Big chances pendentes (criadas mas nao convertidas)
    bc_total = (estado.big_chances_casa or 0) + (estado.big_chances_fora or 0)
    golos_tot = estado.marcador_casa + estado.marcador_fora
    pendentes = bc_total - golos_tot
    if pendentes >= 2:
        ctx["big_chances_pendentes"] = f"{pendentes} big chances criadas sem golo (um vai cair)"

    # Keeper em grande (saves altas)
    if estado.saves_casa is not None and estado.saves_casa >= 5:
        ctx["keeper_casa_grande"] = f"GR {estado.home} com {estado.saves_casa} defesas"
    if estado.saves_fora is not None and estado.saves_fora >= 5:
        ctx["keeper_fora_grande"] = f"GR {estado.away} com {estado.saves_fora} defesas"

    # Score chasing
    diff = abs(estado.marcador_casa - estado.marcador_fora)
    if diff in (1, 2) and prev.minutos_restantes <= 25:
        quem_atras = estado.away if estado.marcador_casa > estado.marcador_fora else estado.home
        ctx["chasing"] = f"{quem_atras} precisa marcar — vai arriscar"

    # Jogo a fechar (empate late ou diferenca grande late)
    if estado.minuto >= 80 and (diff == 0 or diff >= 3):
        if diff == 0:
            ctx["jogo_a_fechar"] = "Empate ao min 80+ — ambas aceitam ponto"
        else:
            ctx["jogo_a_fechar"] = f"Resultado decidido ({estado.marcador_casa}-{estado.marcador_fora}), intensidade baixa"

    # Posse dominante
    if estado.possession_casa is not None:
        if estado.possession_casa >= 0.62:
            ctx["posse_dominante"] = f"{estado.home} com {estado.possession_casa*100:.0f}% posse"
        elif estado.possession_casa <= 0.38:
            ctx["posse_dominante"] = f"{estado.away} com {(1-estado.possession_casa)*100:.0f}% posse"

    # Arbitro severo
    try:
        from apostas.modelos.referencias import fator_arbitro, rate_arbitro, eh_derby
        arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
        if arb_sev > 1.1:
            ctx["arbitro_severo"] = f"Arbitro {estado.arbitro} ({rate_arbitro(estado.arbitro):.1f} cards/jogo)"
        if eh_derby(estado.home, estado.away):
            ctx["derby"] = "⚡DERBY — tensao extra"
    except Exception:  # noqa: BLE001
        pass

    # Faltas elevadas
    if estado.fouls_casa is not None and estado.minuto > 15:
        fpm = (estado.fouls_casa + estado.fouls_fora) / estado.minuto
        if fpm > 0.5:
            ctx["faltas_altas"] = f"{fpm*90:.0f} faltas/90min (jogo porreiro)"

    # Cartao vermelho
    reds = estado.red_cards_casa + estado.red_cards_fora
    if reds > 0:
        ctx["vermelho"] = f"{reds} vermelho(s) mostrado(s)"

    return ctx


# ─────────────── GOLOS ───────────────

def _sinal_golos(estado, prev, ctx, out, prob_min) -> None:
    """Itera as linhas standard do mercado e emite sinais."""
    golos = estado.marcador_casa + estado.marcador_fora

    for linha in LINHAS_MERCADO["golos"]:
        # Se jogo ja tem mais golos que a linha, mercado esta fechado
        if golos > linha:
            continue
        p_over = prev.p_over_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} golos"
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            if "big_chances_pendentes" in ctx:
                racional.append(ctx["big_chances_pendentes"])
            if "defesa_fraca_casa" in ctx:
                racional.append(ctx["defesa_fraca_casa"])
            if "defesa_fraca_fora" in ctx:
                racional.append(ctx["defesa_fraca_fora"])
            if "dominio_territorial" in ctx:
                racional.append(ctx["dominio_territorial"])
            if "chasing" in ctx:
                racional.append(ctx["chasing"])
            if estado.xg_casa is not None:
                xg_tot = estado.xg_casa + estado.xg_fora
                if xg_tot > golos + 0.3:
                    racional.append(f"xG total {xg_tot:.1f} > {golos} golos actuais")
            if not racional:
                racional.append(f"{prev.minutos_restantes}min restam, modelo espera +{int(linha - golos + 0.5)}")
            _add(out, "over_golos_live", "golos", "over", linha, p_over, racional, titulo)

        if p_under >= prob_min:
            racional = []
            titulo = f"Under {linha} golos"
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            if "keeper_casa_grande" in ctx:
                racional.append(ctx["keeper_casa_grande"])
            if "keeper_fora_grande" in ctx:
                racional.append(ctx["keeper_fora_grande"])
            if estado.xg_recent_casa is not None:
                xg_rec = estado.xg_recent_casa + estado.xg_recent_fora
                if xg_rec < 0.2 and estado.minuto >= 60:
                    racional.append(f"xG ultimos 15min apenas {xg_rec:.1f}")
            if not racional:
                racional.append(f"{golos} golos ao min {estado.minuto}, pouca intensidade")
            _add(out, "under_golos_live", "golos", "under", linha, p_under, racional, titulo)


# ─────────────── BTTS ───────────────

def _sinal_btts(estado, prev, ctx, out, prob_min) -> None:
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        return
    p_sim = prev.p_btts_final
    p_nao = 1 - p_sim

    if p_sim >= prob_min:
        racional = []
        titulo = "BTTS SIM"
        quem_falta = estado.home if estado.marcador_casa == 0 else estado.away
        racional.append(f"{quem_falta} precisa marcar")
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem >= 0.6:
                racional.append(f"xG {xg_sem:.1f} do lado sem golo — chances feitas")
        if "defesa_fraca_casa" in ctx and estado.marcador_fora == 0:
            racional.append(ctx["defesa_fraca_casa"])
        if "defesa_fraca_fora" in ctx and estado.marcador_casa == 0:
            racional.append(ctx["defesa_fraca_fora"])
        if "chasing" in ctx:
            racional.append(ctx["chasing"])
        _add(out, "btts_sim", "btts", "sim", None, p_sim, racional, titulo)

    if p_nao >= prob_min and estado.minuto >= 65:
        racional = []
        titulo = "BTTS NAO"
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem < 0.4:
                racional.append(f"xG {xg_sem:.1f} do lado sem golo — nao criam")
            else:
                return
        if "keeper_casa_grande" in ctx and estado.marcador_fora == 0:
            racional.append(ctx["keeper_casa_grande"])
        if "keeper_fora_grande" in ctx and estado.marcador_casa == 0:
            racional.append(ctx["keeper_fora_grande"])
        if "jogo_a_fechar" in ctx:
            racional.append(ctx["jogo_a_fechar"])
        _add(out, "btts_nao", "btts", "nao", None, p_nao, racional, titulo)


# ─────────────── CANTOS ───────────────

def _sinal_cantos(estado, prev, ctx, out, prob_min) -> None:
    cantos = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    for linha in LINHAS_MERCADO["cantos"]:
        if cantos > linha:
            continue
        p_over = prev.p_over_cantos_total(linha)
        p_under = 1 - p_over
        rate = cantos / max(estado.minuto, 1)

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} cantos"
            if rate > 0.11:
                racional.append(f"rate {rate*90:.1f} cantos/90min (acima media)")
            if "dominio_territorial" in ctx:
                racional.append(ctx["dominio_territorial"])
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            blocked = (estado.shots_blocked_casa or 0) + (estado.shots_blocked_fora or 0)
            if blocked >= 3:
                racional.append(f"{blocked} remates bloqueados — cantos a caminho")
            if (estado.crosses_total_casa or 0) + (estado.crosses_total_fora or 0) > 15:
                racional.append("Jogo pelos cruzamentos — cantos consequencia")
            if not racional:
                racional.append(f"{cantos} cantos ao min {estado.minuto}")
            _add(out, "cantos_over", "cantos", "over", linha, p_over, racional, titulo)

        if p_under >= prob_min:
            racional = []
            titulo = f"Under {linha} cantos"
            if rate < 0.08:
                racional.append(f"rate {rate*90:.1f} cantos/90min (abaixo media)")
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            diff = abs(estado.marcador_casa - estado.marcador_fora)
            if diff >= 2 and estado.minuto >= 75 and not racional:
                racional.append(f"Jogo decidido ({estado.marcador_casa}-{estado.marcador_fora}) ao min {estado.minuto}")
            if not racional:
                racional.append(f"{cantos} cantos ao min {estado.minuto}, pouca cadencia")
            _add(out, "cantos_under", "cantos", "under", linha, p_under, racional, titulo)


# ─────────────── CARTOES ───────────────

def _sinal_cartoes(estado, prev, ctx, out, prob_min) -> None:
    amarelos = estado.yellow_cards_casa + estado.yellow_cards_fora
    for linha in LINHAS_MERCADO["cartoes"]:
        if amarelos > linha:
            continue
        p_over = prev.p_over_cartoes_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} cartoes amarelos"
            if "arbitro_severo" in ctx:
                racional.append(ctx["arbitro_severo"])
            if "derby" in ctx:
                racional.append(ctx["derby"])
            if "vermelho" in ctx:
                racional.append(ctx["vermelho"] + " — jogo mais tenso")
            if "faltas_altas" in ctx:
                racional.append(ctx["faltas_altas"])
            if estado.ground_duels_won_pct_casa is not None:
                dd = abs(estado.ground_duels_won_pct_casa - 0.5)
                if dd > 0.07:
                    perdedor = estado.home if estado.ground_duels_won_pct_casa < 0.5 else estado.away
                    racional.append(f"{perdedor} a perder duelos — faltas a vir")
            if not racional:
                rate = amarelos / max(estado.minuto, 1)
                racional.append(f"{amarelos} amarelos ao min {estado.minuto} ({rate*90:.1f}/90min)")
            _add(out, "cards_over", "cartoes", "over", linha, p_over, racional, titulo)

        # UNDER cartoes: so late no jogo e sem contexto de agressividade
        try:
            from apostas.modelos.referencias import fator_arbitro, eh_derby
            arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
            is_derby = eh_derby(estado.home, estado.away)
        except Exception:  # noqa: BLE001
            arb_sev = 1.0
            is_derby = False
        if (p_under >= prob_min and prev.minutos_restantes <= 25
                and arb_sev < 1.05 and not is_derby):
            racional = []
            titulo = f"Under {linha} cartoes amarelos"
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            if estado.arbitro:
                racional.append(f"arbitro {estado.arbitro} brando")
            if not racional:
                racional.append(f"so {amarelos} amarelos ao min {estado.minuto}")
            _add(out, "cards_under", "cartoes", "under", linha, p_under, racional, titulo)


# ─────────────── REMATES ───────────────

def _sinal_remates(estado, prev, ctx, out, prob_min) -> None:
    shots = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    sot = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)
    rate_shots = shots / max(estado.minuto, 1)
    rate_sot = sot / max(estado.minuto, 1)

    for linha in LINHAS_MERCADO["remates"]:
        if shots > linha:
            continue
        p = prev.p_over_remates_total(linha)
        if p >= prob_min:
            projeccao_total = shots + rate_shots * prev.minutos_restantes
            if projeccao_total < linha * 0.75:
                continue
            racional = []
            titulo = f"Over {linha} remates"
            if rate_shots > 0.27:
                racional.append(f"rate {rate_shots*90:.1f} remates/90min")
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            if "big_chances_pendentes" in ctx:
                racional.append(ctx["big_chances_pendentes"])
            if not racional:
                continue
            _add(out, "shots_over", "remates", "over", linha, p, racional, titulo)

    for linha in LINHAS_MERCADO["remates_a_baliza"]:
        if sot > linha:
            continue
        p = prev.p_over_remates_a_baliza_total(linha)
        if p >= prob_min:
            # Consistencia: nao emitir Over X SoT quando rate actual e muito
            # baixa — significa que o modelo baseline esta a inflar.
            rate_90 = rate_sot * 90
            projeccao_total = sot + rate_sot * prev.minutos_restantes
            if projeccao_total < linha * 0.75:
                continue  # observacao contradiz a sugestao
            racional = []
            titulo = f"Over {linha} remates ao alvo"
            if estado.big_chances_casa is not None:
                bc = (estado.big_chances_casa or 0) + (estado.big_chances_fora or 0)
                if bc >= 3:
                    racional.append(f"{bc} big chances criadas")
            if "keeper_casa_grande" in ctx:
                racional.append(ctx["keeper_casa_grande"])
            if "keeper_fora_grande" in ctx:
                racional.append(ctx["keeper_fora_grande"])
            if rate_90 >= 7:
                racional.append(f"{sot} SoT ao min {estado.minuto} ({rate_90:.1f}/90min)")
            if not racional:
                continue  # sem racional credivel, nao emite
            _add(out, "sot_over", "remates_a_baliza", "over", linha, p, racional, titulo)
