"""Narrativa final do mega-cerebro: previsao clara + certezas no fim do alerta.

Depois dos "fact bullets" da seccao Analise e das linhas dos treinadores,
este modulo acrescenta uma seccao final tipo:

    🧠 *Mega-Cerebro — Previsao*
    _Analiso: ..._
    *Prevejo:* ... → o que vai acontecer.
    *Certezas:*
      ✓ ...
      ✓ ...
      ⚠ ...  (quando ha risco palpavel)

As certezas sao extraidas de factos duros do sinal (ritmo, SoT, xG,
cantos, cards, dominancia, posse) + sintese do mega-cerebro.
"""
from __future__ import annotations

from typing import Optional


def _parse_marcador(s: dict) -> tuple[int, int]:
    """De '2-1' extrai (2, 1); se falhar, (0, 0)."""
    try:
        a, b = str(s.get("marcador") or "0-0").split("-", 1)
        return int(a.strip()), int(b.strip())
    except (ValueError, AttributeError):
        return 0, 0


def _parse_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _parse_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _ritmo_por_90(valor: int, minuto: int) -> float:
    if minuto <= 0 or valor <= 0:
        return 0.0
    return (valor / minuto) * 90.0


def _nome_mercado(mercado: str) -> str:
    """Nome amigavel do mercado para prosa."""
    return {
        "golos": "golos",
        "cantos": "cantos",
        "cards": "cartoes amarelos",
        "cartoes": "cartoes amarelos",
        "sot": "remates a baliza",
        "remates_a_baliza": "remates a baliza",
        "remates": "remates totais",
        "btts": "BTTS",
    }.get(mercado, mercado)


def _unidade(mercado: str) -> str:
    """Unidade singular/plural curta."""
    return {
        "golos": "golos",
        "cantos": "cantos",
        "cards": "amarelos",
        "cartoes": "amarelos",
        "sot": "SoT",
        "remates_a_baliza": "SoT",
        "remates": "remates",
    }.get(mercado, "unidades")


def narrativa_final(s: dict, *, sintese_razao: str = "") -> str:
    """Devolve a seccao mega-cerebro final para colar no fim do alerta.

    s: sugestao com campos minimo { mercado, lado, linha, minuto, prob_modelo,
       marcador, shots_on_target_c/f, corners_c/f, yellows_c/f, xg_casa/fora }
    sintese_razao: razao textual do mega_cerebro.sintetizar (opcional).

    Devolve string ja formatada com '\\n' no inicio.
    Se o sinal nao tem dados minimos devolve '' (nao polui mensagem).
    """
    mercado = str(s.get("mercado") or "").lower()
    lado = str(s.get("lado") or "").lower()
    minuto = _parse_int(s.get("minuto")) or 0
    linha = _parse_float(s.get("linha"))
    prob = _parse_float(s.get("prob_modelo"))

    if not mercado or not lado or minuto <= 0:
        return ""

    sc_c, sc_f = _parse_marcador(s)
    sot_c = _parse_int(s.get("shots_on_target_c"))
    sot_f = _parse_int(s.get("shots_on_target_f"))
    can_c = _parse_int(s.get("corners_c"))
    can_f = _parse_int(s.get("corners_fora") or s.get("corners_f"))
    yel_c = _parse_int(s.get("yellows_c"))
    yel_f = _parse_int(s.get("yellows_f"))
    xg_c = _parse_float(s.get("xg_casa"))
    xg_f = _parse_float(s.get("xg_fora"))

    min_restante = max(0, 90 - minuto)
    nome_merc = _nome_mercado(mercado)
    unidade = _unidade(mercado)

    # ───── 1. Analiso: contexto + ritmo observado ────────────────────────
    analises: list[str] = []
    if sintese_razao:
        analises.append(sintese_razao)

    actual: Optional[int] = None
    if mercado in ("sot", "remates_a_baliza") and sot_c is not None and sot_f is not None:
        actual = sot_c + sot_f
    elif mercado == "cantos" and can_c is not None and can_f is not None:
        actual = can_c + can_f
    elif mercado in ("cards", "cartoes") and yel_c is not None and yel_f is not None:
        actual = yel_c + yel_f
    elif mercado == "golos":
        actual = sc_c + sc_f

    if actual is not None and minuto > 0:
        ritmo = _ritmo_por_90(actual, minuto)
        if ritmo > 0:
            analises.append(
                f"ritmo actual {ritmo:.1f} {unidade}/90min (observado: {actual} ao {minuto}')"
            )

    # xG mismatch (so para golos)
    if mercado == "golos" and xg_c is not None and xg_f is not None:
        xg_tot = xg_c + xg_f
        gol_tot = sc_c + sc_f
        if xg_tot - gol_tot >= 0.8:
            analises.append(
                f"xG {xg_tot:.2f} vs {gol_tot} golos — merecem mais do que marcam (+{xg_tot - gol_tot:.1f})"
            )

    # Dominancia por posse/cards (indirecta)
    if mercado == "golos" and sc_c != sc_f:
        if sc_c > sc_f:
            analises.append(
                f"casa a ganhar {sc_c}-{sc_f} vai gerir ritmo; fora obrigado a abrir espacos"
            )
        else:
            analises.append(
                f"fora a ganhar {sc_f}-{sc_c} vai gerir; casa obrigada a abrir e expor-se"
            )

    # ───── 2. Previsao concreta ──────────────────────────────────────────
    previsao = ""
    if actual is not None and linha is not None and min_restante > 0:
        faltam = linha + 0.5 - actual if lado == "over" else actual - (linha - 0.5)
        # Para OVER, faltam X unidades para bater; para UNDER, nao devem passar Y
        if lado == "over":
            unidades_necessarias = max(0, int(linha - actual) + 1)
            if unidades_necessarias <= 0:
                previsao = f"a linha ja esta batida ({actual} vs {linha}) — procura outra."
            else:
                ritmo_restante = _ritmo_por_90(actual, minuto) * (min_restante / 90.0)
                total_projeccao = actual + ritmo_restante
                margem = total_projeccao - linha
                if margem >= 2:
                    previsao = (
                        f"para OVER {linha} faltam ~{unidades_necessarias} "
                        f"{unidade} em {min_restante} min; "
                        f"projeccao {total_projeccao:.1f} ({margem:+.1f} sobre a linha) — margem clara"
                    )
                elif margem >= 0:
                    previsao = (
                        f"para OVER {linha} faltam ~{unidades_necessarias} {unidade}; "
                        f"projeccao {total_projeccao:.1f} — vai ao fio"
                    )
                else:
                    previsao = (
                        f"projeccao {total_projeccao:.1f} abaixo da linha {linha} — "
                        f"OVER arriscado, so se o jogo abrir"
                    )
        else:  # under
            ritmo_restante = _ritmo_por_90(actual, minuto) * (min_restante / 90.0)
            total_projeccao = actual + ritmo_restante
            folga = linha - total_projeccao
            if folga >= 1.5:
                previsao = (
                    f"para UNDER {linha} ha margem — projeccao {total_projeccao:.1f}, "
                    f"folga de {folga:.1f} ate a linha"
                )
            elif folga >= 0:
                previsao = (
                    f"projeccao {total_projeccao:.1f} roca a linha {linha} — "
                    f"UNDER vai ao fio"
                )
            else:
                previsao = (
                    f"projeccao {total_projeccao:.1f} acima da linha {linha} — "
                    f"UNDER arriscado"
                )

    if not previsao and prob is not None:
        previsao = f"modelo da {prob*100:.0f}% ao {lado.upper()} {linha} — sinal ha mas sem forma concreta."

    # ───── 3. Certezas (bullets) ─────────────────────────────────────────
    certezas: list[str] = []
    riscos: list[str] = []

    if prob is not None and prob >= 0.75:
        certezas.append(f"probabilidade {prob*100:.0f}% — modelo muito confiante")
    elif prob is not None and prob >= 0.65:
        certezas.append(f"probabilidade {prob*100:.0f}% — forte mas nao certo")

    if actual is not None and minuto > 0:
        ritmo = _ritmo_por_90(actual, minuto)
        if linha is not None and lado == "over" and ritmo > (linha * 90 / 90) * 1.2:
            certezas.append(f"ritmo {ritmo:.1f}/90min, 20%+ acima da linha {linha}")
        elif linha is not None and lado == "under" and ritmo < linha * 0.75:
            certezas.append(f"ritmo {ritmo:.1f}/90min, bem abaixo da linha {linha}")

    if mercado == "golos" and xg_c is not None and xg_f is not None:
        if lado == "over" and (xg_c + xg_f) - (sc_c + sc_f) >= 0.8:
            certezas.append(f"xG {xg_c + xg_f:.2f} aponta para mais golo que o actual")
        if lado == "under" and xg_c + xg_f <= (sc_c + sc_f) + 0.3:
            certezas.append(f"xG {xg_c + xg_f:.2f} consistente com {sc_c + sc_f} golos — defesa a aguentar")

    if mercado in ("sot", "remates_a_baliza") and sot_c is not None and sot_f is not None:
        if lado == "over" and sot_c + sot_f >= 5:
            certezas.append(f"{sot_c + sot_f} SoT ao {minuto}' sinaliza jogo ofensivo")

    # Riscos
    if min_restante <= 12 and lado == "over" and actual is not None and linha is not None:
        if actual < linha:
            riscos.append(f"so {min_restante} min para fechar {int(linha + 0.5 - actual)} {unidade} — pouca folga")

    if mercado in ("cards", "cartoes") and yel_c is not None and yel_f is not None:
        yel_tot = yel_c + yel_f
        if lado == "over" and yel_tot >= linha - 1 and min_restante <= 15:
            certezas.append(f"ja {yel_tot} amarelos, 1 so para OVER — final tenso costuma dar")
        if lado == "under" and yel_tot >= linha - 1:
            riscos.append(f"{yel_tot} amarelos ja no board, 1 so bate linha")

    # ───── 4. Formatacao final ───────────────────────────────────────────
    partes: list[str] = ["", "🧠 *Mega-Cerebro — Previsao*"]
    if analises:
        partes.append("_Analiso: " + "; ".join(analises) + "._")
    if previsao:
        partes.append(f"*Prevejo:* {previsao}")
    if certezas or riscos:
        partes.append("*Certezas:*")
        for c in certezas:
            partes.append(f"  ✓ {c}")
        for r in riscos:
            partes.append(f"  ⚠ {r}")
    if len(partes) <= 2:
        # So o cabecalho — nada util para dizer
        return ""
    return "\n".join(partes)
