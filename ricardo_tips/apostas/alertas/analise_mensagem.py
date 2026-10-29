"""Construcao da seccao de analise detalhada na mensagem Telegram.

Cada bullet vem de um "fact finder" que:
  - devolve string com o facto quando ha dado concreto
  - devolve None quando falta dado (nao inventa frase generica)

Depois `construir_analise(s)` junta os nao-None. Resultado: cada alerta
tem bullets diferentes consoante o momento real do jogo, nao textos
repetidos.

Dedup: ao gerar o texto final, verifica se o mesmo facto ja apareceu
noutra linha (treinador, mega) para nao repetir.
"""
from __future__ import annotations

import math


# ─── Helpers ────────────────────────────────────────────────────────

def _int(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _valor_atual_mercado(s: dict, mercado: str) -> int | None:
    """Valor acumulado actual do mercado da aposta, se possivel.

    Baseado nos campos que o scanner guarda no sinal (xg, sot,
    corners, yellows).
    """
    if mercado == "cantos":
        c = _int(s.get("corners_c"))
        f = _int(s.get("corners_f"))
        if c is None or f is None:
            return None
        return c + f
    if mercado == "cartoes":
        c = _int(s.get("yellows_c"))
        f = _int(s.get("yellows_f"))
        if c is None or f is None:
            return None
        return c + f
    if mercado == "remates_a_baliza":
        c = _int(s.get("shots_on_target_c"))
        f = _int(s.get("shots_on_target_f"))
        if c is None or f is None:
            return None
        return c + f
    # Golos: do marcador
    if mercado == "golos":
        marcador = str(s.get("marcador") or "0-0")
        try:
            a, b = marcador.split("-", 1)
            return int(a.strip()) + int(b.strip())
        except (ValueError, AttributeError):
            return None
    return None


# ─── Bullets de analise ─────────────────────────────────────────────

def _bullet_ritmo_actual(s: dict) -> str | None:
    """Extrapola o valor actual para 90min e compara com a linha.

    Ex: "Ritmo actual 28.3 remates/90min (linha 24.5)"
    """
    mercado = str(s.get("mercado") or "")
    try:
        minuto = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        return None
    if minuto < 15:
        return None  # muito cedo para extrapolar
    valor = _valor_atual_mercado(s, mercado)
    if valor is None or valor == 0:
        return None
    rate_90 = valor * 90.0 / minuto
    linha = _float(s.get("linha"))
    if linha is None:
        return f"• Ritmo actual *{rate_90:.1f}*/90min"
    diff_pct = (rate_90 - linha) / linha * 100
    sinal = "acima" if diff_pct >= 0 else "abaixo"
    return (f"• Ritmo actual *{rate_90:.1f}*/90min "
            f"vs linha {linha} → *{abs(diff_pct):.0f}% {sinal}*")


def _bullet_shots_on_target(s: dict) -> str | None:
    """SoT acumulado por lado — mostra pressao real."""
    c = _int(s.get("shots_on_target_c"))
    f = _int(s.get("shots_on_target_f"))
    if c is None or f is None:
        return None
    total = c + f
    if total == 0:
        return None
    home = str(s.get("home") or "casa")
    away = str(s.get("away") or "fora")
    return f"• SoT {c}-{f} ({home} vs {away}) → total {total}"


def _bullet_xg_vs_golos(s: dict) -> str | None:
    """xG acumulado vs golos reais — indica se jogo esta por abrir."""
    xg_c = _float(s.get("xg_casa"))
    xg_f = _float(s.get("xg_fora"))
    if xg_c is None or xg_f is None:
        return None
    marcador = str(s.get("marcador") or "0-0")
    try:
        a, b = marcador.split("-", 1)
        gols_total = int(a.strip()) + int(b.strip())
    except (ValueError, AttributeError):
        return None
    xg_total = xg_c + xg_f
    if xg_total < 0.1:
        return None
    diff = xg_total - gols_total
    if diff >= 0.6:
        return (f"• xG {xg_total:.2f} vs {gols_total} golos → "
                f"jogo esta a *produzir mas nao a converter* (+{diff:.1f})")
    if diff <= -0.6:
        return (f"• xG {xg_total:.2f} vs {gols_total} golos → "
                f"*sorte na finalizacao* (-{abs(diff):.1f} xG abaixo do real)")
    return None


def _bullet_cantos_pressao(s: dict) -> str | None:
    """Cantos por lado — se um lado tem muitos mais, ha pressao assimetrica."""
    c = _int(s.get("corners_c"))
    f = _int(s.get("corners_f"))
    if c is None or f is None:
        return None
    if c + f < 4:
        return None
    diff = abs(c - f)
    if diff < 3:
        return None
    home = str(s.get("home") or "casa")
    away = str(s.get("away") or "fora")
    lado_dominante = home if c > f else away
    return f"• Cantos {c}-{f} → {lado_dominante} a pressionar pelas pontas"


def _bullet_cards_acumulando(s: dict) -> str | None:
    """Cards amarelos — se alto, jogo pode partir."""
    c = _int(s.get("yellows_c"))
    f = _int(s.get("yellows_f"))
    if c is None or f is None:
        return None
    total = c + f
    try:
        minuto = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        return None
    if minuto <= 0:
        return None
    rate_90 = total * 90.0 / minuto
    if total < 4 or rate_90 < 5.0:
        return None
    return (f"• Amarelos {c}+{f}={total} ao {minuto}' → "
            f"ritmo {rate_90:.1f}/90min, jogo *quente*")


def _bullet_roi_historico(s: dict) -> str | None:
    """ROI historico do (liga, mercado). Ja usado antes mas aqui metemos
    inline na seccao de analise."""
    liga = str(s.get("liga") or "")
    mercado = str(s.get("mercado") or "")
    if not liga or not mercado:
        return None
    try:
        from apostas.alertas.historico_alertas import (
            agregar_por_mercado, carregar_perfis,
        )
        from apostas.utils.config import project_root
    except Exception:  # noqa: BLE001
        return None
    try:
        csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
        perfis = carregar_perfis(csv_path) or {}
    except Exception:  # noqa: BLE001
        return None
    if not perfis:
        return None
    merc_key = mercado
    try:
        agg = agregar_por_mercado(perfis).get((liga, merc_key))
    except Exception:  # noqa: BLE001
        return None
    if agg is None or agg.n_resolvidos < 10 or agg.roi is None:
        return None
    sinal = "+" if agg.roi >= 0 else ""
    return f"📊 ROI historico ({liga}/{mercado}): {sinal}{agg.roi:.1f}% em {agg.n_resolvidos} alertas"


# ─── Bullets de risco (so quando ha item concreto) ──────────────────

def _risco_minutos_restantes(s: dict) -> str | None:
    """Alerta se o resto do jogo nao da para a aposta bater."""
    mercado = str(s.get("mercado") or "")
    lado = str(s.get("lado") or "")
    linha = _float(s.get("linha"))
    try:
        minuto = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        return None
    if linha is None or minuto >= 85 or lado != "over":
        return None
    valor_atual = _valor_atual_mercado(s, mercado)
    if valor_atual is None:
        return None
    falta = max(0, math.ceil(linha - valor_atual))
    if falta <= 0:
        return None  # ja bateu
    minutos_rest = max(1, 90 - minuto)
    if falta >= minutos_rest / 10:  # precisa de 1/10min
        return (f"• Faltam *{falta}+* unidades em {minutos_rest} min — "
                f"exigente")
    return None


def _risco_cards_proximo_expulsao(s: dict) -> str | None:
    """Se um lado tem 3+ amarelos ha chance real de 2o amarelo."""
    for lado_key, nome_key in (("yellows_c", "home"), ("yellows_f", "away")):
        n = _int(s.get(lado_key))
        if n is not None and n >= 3:
            nome = str(s.get(nome_key) or lado_key)
            return f"• {nome} com {n} amarelos — risco de 2o amarelo"
    return None


def _risco_rotacao_lineup(s: dict) -> str | None:
    """Verifica lineup actual vs habituais."""
    try:
        from apostas.alertas import rotacao as _rotacao
    except Exception:  # noqa: BLE001
        return None
    for id_key, nome_key, lineup_key in (
        ("home_id", "home", "lineup_titulares_casa"),
        ("away_id", "away", "lineup_titulares_fora"),
    ):
        tid = _int(s.get(id_key))
        nome = str(s.get(nome_key) or "")
        lineup = s.get(lineup_key) or []
        if tid is None or not lineup or not nome:
            continue
        try:
            ids_actuais = [pid for pid, _ in lineup]
            rel = _rotacao.analisar_lineup_actual(tid, nome, ids_actuais)
            if rel and rel.tem_rotacao(limiar=3):
                return "• " + rel.mensagem_curta().lstrip("⚠ ")
        except Exception:  # noqa: BLE001
            continue
    return None


# ─── API principal ──────────────────────────────────────────────────

def construir_analise(s: dict, excluir_frases: set[str] | None = None,
                       ) -> list[str]:
    """Devolve lista de bullets com factos reais do jogo.

    `excluir_frases` sao palavras-chave que ja apareceram noutro
    bloco (ex: treinador, mega) e nao devem repetir. Dedup soft.
    """
    excl = {s.lower() for s in (excluir_frases or set())}
    bullets = []
    for fn in (
        _bullet_ritmo_actual,
        _bullet_shots_on_target,
        _bullet_xg_vs_golos,
        _bullet_cantos_pressao,
        _bullet_cards_acumulando,
    ):
        try:
            b = fn(s)
        except Exception:  # noqa: BLE001
            b = None
        if b is None:
            continue
        # Dedup soft: nao repete palavras-chave do que ja esta exibido
        chave_bullet = b.lower()
        if any(frase in chave_bullet for frase in excl):
            continue
        bullets.append(b)
    # ROI historico sempre no fim
    try:
        roi = _bullet_roi_historico(s)
        if roi:
            bullets.append(roi)
    except Exception:  # noqa: BLE001
        pass
    return bullets


def construir_riscos(s: dict) -> list[str]:
    """Devolve lista de bullets com riscos reais do jogo (vazio se
    nao ha riscos concretos)."""
    bullets = []
    for fn in (
        _risco_minutos_restantes,
        _risco_cards_proximo_expulsao,
        _risco_rotacao_lineup,
    ):
        try:
            r = fn(s)
        except Exception:  # noqa: BLE001
            r = None
        if r:
            bullets.append(r)
    return bullets


def seccao_analise_telegram(s: dict, excluir_frases: set[str] | None = None,
                              ) -> str:
    """Produz o bloco completo:

      🧠 *Analise* (edge X%, modelo Y%)
        • bullet 1
        • bullet 2
        ...
        📊 ROI historico ...

    Vazio se nenhum bullet gerado.
    """
    bullets = construir_analise(s, excluir_frases=excluir_frases)
    if not bullets:
        return ""
    try:
        ev = float(s.get("ev", 0) or 0)
    except (TypeError, ValueError):
        ev = 0.0
    try:
        prob = float(s.get("prob_modelo", 0) or 0)
    except (TypeError, ValueError):
        prob = 0.0
    cabecalho = "🧠 *Analise*"
    if ev > 0 or prob > 0:
        partes = []
        if ev > 0:
            partes.append(f"edge {ev*100:.0f}%")
        if prob > 0:
            partes.append(f"modelo {prob*100:.0f}%")
        cabecalho += f" ({', '.join(partes)})"
    return cabecalho + "\n" + "\n".join(bullets)


def seccao_risco_telegram(s: dict) -> str:
    """Produz o bloco de risco. Vazio se nao ha riscos."""
    riscos = construir_riscos(s)
    if not riscos:
        return ""
    return "⚠ *Risco*\n" + "\n".join(riscos)
