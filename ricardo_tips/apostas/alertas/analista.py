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


def gerar_sinais(estado: "EstadoLive", prev: "PrevisaoLive",
                 prob_minima: float = 0.55) -> list[Sinal]:
    """Devolve sinais LIVE com reasoning rico."""
    ctx = _construir_contexto(estado, prev)
    out: list[Sinal] = []
    _sinal_golos(estado, prev, ctx, out, prob_minima)
    _sinal_btts(estado, prev, ctx, out, prob_minima)
    _sinal_cantos(estado, prev, ctx, out, prob_minima)
    _sinal_cartoes(estado, prev, ctx, out, prob_minima)
    _sinal_remates(estado, prev, ctx, out, prob_minima)
    return [s for s in out if s.prob_modelo >= prob_minima]


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
    golos = estado.marcador_casa + estado.marcador_fora
    linhas = [golos + 0.5, golos + 1.5]

    for linha in linhas:
        if linha < 0.5 or linha > 6.5:
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
                racional.append(f"{prev.minutos_restantes}min restam, modelo espera +1")
            _add(out, "over_golos_live", "golos", "over", linha, p_over, racional, titulo)

        if p_under >= prob_min and prev.minutos_restantes <= 30:
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
                if xg_rec < 0.2:
                    racional.append(f"xG ultimos 15min apenas {xg_rec:.1f}")
            if not racional:
                racional.append(f"intensidade baixa, {prev.minutos_restantes}min restam")
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
    for linha in [cantos + 0.5, cantos + 1.5, cantos + 2.5]:
        if linha < 2.5 or linha > 15.5:
            continue
        p_over = prev.p_over_cantos_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} cantos"
            rate = cantos / max(estado.minuto, 1)
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
                racional.append("Jogo pelos cruzamentos (cantos consequencia)")
            if not racional:
                racional.append(f"{cantos} cantos ao min {estado.minuto}")
            _add(out, "cantos_over", "cantos", "over", linha, p_over, racional, titulo)

        if p_under >= prob_min and prev.minutos_restantes <= 25:
            racional = []
            titulo = f"Under {linha} cantos"
            rate = cantos / max(estado.minuto, 1)
            if rate < 0.08:
                racional.append(f"rate {rate*90:.1f} cantos/90min (abaixo media)")
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            # Fallback: diferenca grande no marcador late
            diff = abs(estado.marcador_casa - estado.marcador_fora)
            if diff >= 2 and estado.minuto >= 75 and not racional:
                racional.append(f"Jogo decidido ({estado.marcador_casa}-{estado.marcador_fora}) ao min {estado.minuto} — ambas aceitam ritmo")
            if not racional:
                racional.append(f"{cantos} cantos ao min {estado.minuto}, {prev.minutos_restantes}min restam para +{int(linha - cantos + 0.5)}")
            _add(out, "cantos_under", "cantos", "under", linha, p_under, racional, titulo)


# ─────────────── CARTOES ───────────────

def _sinal_cartoes(estado, prev, ctx, out, prob_min) -> None:
    amarelos = estado.yellow_cards_casa + estado.yellow_cards_fora
    for linha in [amarelos + 0.5, amarelos + 1.5, amarelos + 2.5]:
        if linha < 1.5 or linha > 9.5:
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
            # Duels won % baixo indica dispossessions = fouls
            if estado.ground_duels_won_pct_casa is not None:
                diff = abs(estado.ground_duels_won_pct_casa - 0.5)
                if diff > 0.07:
                    perdedor = estado.home if estado.ground_duels_won_pct_casa < 0.5 else estado.away
                    racional.append(f"{perdedor} a perder duelos — faltas a vir")
            if not racional:
                racional.append(f"{amarelos} amarelos ao min {estado.minuto}")
            _add(out, "cards_over", "cartoes", "over", linha, p_over, racional, titulo)


# ─────────────── REMATES ───────────────

def _sinal_remates(estado, prev, ctx, out, prob_min) -> None:
    shots = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    sot = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)

    for linha in [shots + 0.5, shots + 2.5, shots + 4.5]:
        if linha < 5.5 or linha > 35.5:
            continue
        p = prev.p_over_remates_total(linha)
        if p >= prob_min and estado.minuto < 80:
            racional = []
            titulo = f"Over {linha} remates"
            rate = shots / max(estado.minuto, 1)
            if rate > 0.27:
                racional.append(f"rate {rate*90:.1f} remates/90min")
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            if "big_chances_pendentes" in ctx:
                racional.append(ctx["big_chances_pendentes"])
            if not racional:
                racional.append(f"{shots} remates ao min {estado.minuto}")
            _add(out, "shots_over", "remates", "over", linha, p, racional, titulo)

    for linha in [sot + 0.5, sot + 1.5, sot + 2.5]:
        if linha < 2.5 or linha > 15.5:
            continue
        p = prev.p_over_remates_a_baliza_total(linha)
        if p >= prob_min and estado.minuto < 80:
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
            if not racional:
                racional.append(f"{sot} remates ao alvo ao min {estado.minuto}")
            _add(out, "sot_over", "remates_a_baliza", "over", linha, p, racional, titulo)
