"""Previews pre-match com cenarios tacticos — para enviar as 17h diario
e as sextas (previa fim-de-semana).

A logica puxa:
  - Lista de jogos do(s) dia(s) via `sofascore_live.listar_jogos_do_dia`.
  - Para cada jogo, consulta `confronto.analisar_confronto(home_id, away_id)`
    (sem minuto/score — pre-match) → ResultadoConfronto com projeccoes
    esperadas, padroes cruzados e alertas de fadiga.
  - Para cada jogo, escolhe o MELHOR mercado (over/under golos, cards,
    cantos ou SoT) comparando a projeccao com as linhas tipicas das
    casas e calcula a odd justa Poisson. Pontua com estrelas pela
    confianca do edge.
  - Formata em texto Telegram-ready: por cada jogo, 1 linha "aposta
    recomendada" + racional (padroes + ROI historico se disponivel) +
    projecoes brutas compactas.

Nao chama Sofascore pelo live predictor — e so pre-match, mais barato.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from apostas.utils.logger import get_logger

log = get_logger(__name__)


# ─── Mercados e linhas tipicas ────────────────────────────────────────

# Linhas tipicas (half-point, sem push) que as casas oferecem em pre-match.
# Escolhemos a linha mais proxima da projeccao para calcular EV.
_LINHAS_TIPICAS = {
    "golos":  [1.5, 2.5, 3.5],
    "cards":  [2.5, 3.5, 4.5, 5.5, 6.5],
    "cantos": [7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
    "sot":    [6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
}

_NOMES_MERCADO = {
    "golos":  ("⚽", "golos"),
    "cards":  ("🟨", "cards amarelos"),
    "cantos": ("🚩", "cantos"),
    "sot":    ("🎯", "remates a baliza"),
}


def _poisson_prob_over(linha: float, lam: float) -> float:
    """P(X > linha) com X ~ Poisson(lam). Linha .5 = sem push.

    Para linha 2.5 → P(X >= 3) = 1 - P(X <= 2) = 1 - sum pmf(0..2).
    """
    if lam <= 0:
        return 0.0
    n_min = int(math.floor(linha)) + 1
    prob_abaixo = 0.0
    for k in range(n_min):
        prob_abaixo += math.exp(-lam) * (lam ** k) / math.factorial(k)
    return max(0.0, min(1.0, 1.0 - prob_abaixo))


@dataclass
class ApostaRecomendada:
    """Melhor mercado para 1 jogo, pronto para Telegram."""
    mercado: str          # 'golos' | 'cards' | 'cantos' | 'sot'
    linha: float
    direccao: str         # 'over' | 'under'
    projeccao: float
    prob: float           # 0.0-1.0
    odd_fair: float       # 1/prob
    edge: float           # |prob - 0.5|  (quanto mais, mais confianca)
    estrelas: int         # 1-3


@dataclass
class LinhaJogoPreview:
    """Resumo pre-match de 1 jogo para o preview."""
    hora: str              # "20:30" (Europe/Lisbon ja ajustado pelo chamador)
    liga: str
    home: str
    away: str
    event_id: int
    home_id: int | None
    away_id: int | None
    padroes: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    # Projecoes agregadas das 2 fichas
    cards_esperados: float | None = None
    cantos_esperados: float | None = None
    sot_esperados: float | None = None
    golos_esperados: float | None = None


@dataclass
class PreviewDia:
    """Preview completo do(s) dia(s)."""
    data_inicio: datetime
    data_fim: datetime
    jogos: list[LinhaJogoPreview] = field(default_factory=list)


def _hora_pt(ts_unix: int) -> str:
    """Converte unix timestamp (UTC) para HH:MM em Europe/Lisbon."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts_unix, ZoneInfo("Europe/Lisbon")).strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(ts_unix).strftime("%H:%M")


def _construir_linha(ev: dict, confronto_fn) -> LinhaJogoPreview | None:
    """A partir de 1 evento Sofascore raw, produz LinhaJogoPreview."""
    try:
        hid = (ev.get("homeTeam") or {}).get("id")
        aid = (ev.get("awayTeam") or {}).get("id")
        home = (ev.get("homeTeam") or {}).get("name") or "?"
        away = (ev.get("awayTeam") or {}).get("name") or "?"
        liga = ((ev.get("tournament") or {}).get("name")
                or (ev.get("tournament") or {}).get("uniqueTournament", {}).get("name")
                or "?")
        start_ts = int(ev.get("startTimestamp") or 0)
        event_id = int(ev.get("id") or 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("evento invalido: %s", exc)
        return None

    linha = LinhaJogoPreview(
        hora=_hora_pt(start_ts) if start_ts else "?",
        liga=liga, home=home, away=away,
        event_id=event_id, home_id=hid, away_id=aid,
    )

    # Confronto so se tiver os 2 IDs
    if hid is None or aid is None:
        return linha

    try:
        res = confronto_fn(hid, aid)
    except Exception as exc:  # noqa: BLE001
        log.debug("confronto preview %s-%s: %s", hid, aid, exc)
        return linha

    if res is None:
        return linha

    linha.padroes = list(res.padroes_cruzados)
    linha.alertas = list(res.alertas)
    linha.cards_esperados = res.cards_am_esperados
    linha.cantos_esperados = res.cantos_esperados
    linha.sot_esperados = res.sot_esperados
    linha.golos_esperados = res.golos_esperados
    return linha


def gerar_preview(
    data_inicio: datetime,
    data_fim: datetime,
    *,
    ids_ligas: tuple[int, ...] | None = None,
    listar_fn=None,
    confronto_fn=None,
) -> PreviewDia:
    """Gera preview com todos os jogos entre [data_inicio, data_fim).

    `listar_fn` e `confronto_fn` sao injectaveis para testes. Default usa
    sofascore_live.listar_jogos_do_dia e confronto.analisar_confronto.
    """
    if listar_fn is None:
        from apostas.ingestao.sofascore_live import listar_jogos_do_dia
        listar_fn = listar_jogos_do_dia
    if confronto_fn is None:
        from apostas.alertas.confronto import analisar_confronto
        confronto_fn = analisar_confronto

    preview = PreviewDia(data_inicio=data_inicio, data_fim=data_fim)
    seen_event_ids: set[int] = set()
    dia = data_inicio
    while dia < data_fim:
        try:
            jogos = listar_fn(dia.strftime("%Y-%m-%d"),
                              ids_ligas=ids_ligas, incluir_live=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("listar_jogos_do_dia %s: %s", dia, exc)
            jogos = []
        for ev in jogos:
            event_id = ev.get("id")
            if event_id in seen_event_ids:
                continue
            # Filtrar por janela temporal (start timestamp dentro da janela)
            start_ts = ev.get("startTimestamp") or 0
            if start_ts:
                when = datetime.utcfromtimestamp(start_ts)
                if when < data_inicio or when >= data_fim:
                    continue
            linha = _construir_linha(ev, confronto_fn)
            if linha is not None:
                preview.jogos.append(linha)
                if event_id:
                    seen_event_ids.add(event_id)
        dia = dia + timedelta(days=1)

    # Ordena por hora (string HH:MM funciona lexicograficamente)
    preview.jogos.sort(key=lambda j: (j.hora, j.liga))
    return preview


# ─── Formatacao Telegram ───────────────────────────────────────────

# Minimos sanity-check por mercado — projeccoes abaixo disto indicam
# ficha incompleta/vazia e geram recomendacoes falsas (ex: cantos 2.0
# da UNDER com prob 100%). Melhor silenciar.
_LAM_MINIMO = {
    "golos":  1.0,   # <1 golo esperado = jogo degenerado
    "cards":  2.0,   # <2 amarelos num jogo e improvavel
    "cantos": 6.0,   # <6 cantos indica ficha vazia (media real ~10)
    "sot":    5.0,   # <5 SoT indica ficha vazia
}


def _escolher_aposta(j: LinhaJogoPreview) -> ApostaRecomendada | None:
    """Dado um jogo com projecoes, devolve o mercado com maior edge
    vs linhas tipicas das casas. None se nenhum mercado tem edge
    significativo (>=55% ou <=45%) ou se as projeccoes parecem
    corrompidas por fichas vazias."""
    candidatos: list[ApostaRecomendada] = []
    projs = (
        ("golos", j.golos_esperados),
        ("cards", j.cards_esperados),
        ("cantos", j.cantos_esperados),
        ("sot", j.sot_esperados),
    )
    for mercado, lam in projs:
        if lam is None:
            continue
        if lam < _LAM_MINIMO[mercado]:
            # Projecao irrealisticamente baixa — ficha vazia. Ignora.
            continue
        linhas = _LINHAS_TIPICAS[mercado]
        linha = min(linhas, key=lambda l: abs(l - lam))
        prob_over = _poisson_prob_over(linha, lam)
        edge = abs(prob_over - 0.5)
        direccao = "over" if prob_over >= 0.5 else "under"
        prob = prob_over if direccao == "over" else 1 - prob_over
        # Prob >= 90% ou <= 10% nunca e credivel pre-match — rejeita.
        # Ou e sinal de linha mal escolhida ou de projeccao corrompida.
        if prob >= 0.9:
            continue
        candidatos.append(ApostaRecomendada(
            mercado=mercado, linha=linha, direccao=direccao,
            projeccao=round(lam, 1), prob=round(prob, 3),
            odd_fair=round(1.0 / prob, 2) if prob > 0.01 else 100.0,
            edge=round(edge, 3), estrelas=0,
        ))
    if not candidatos:
        return None
    melhor = max(candidatos, key=lambda a: a.edge)
    # Edge minimo para destacar — abaixo disto a projecao esta demasiado
    # proxima da linha e nao vale emitir recomendacao.
    if melhor.edge < 0.07:
        return None
    # Estrelas por edge: >=0.18 → 3, >=0.12 → 2, resto 1
    if melhor.edge >= 0.18:
        melhor.estrelas = 3
    elif melhor.edge >= 0.12:
        melhor.estrelas = 2
    else:
        melhor.estrelas = 1
    return melhor


_cache_perfis_preview: dict | None = None


def _roi_para_mercado(liga: str, mercado: str) -> str:
    """Devolve '📊 ROI +12% em 45 alertas' ou '' se nao ha historico."""
    global _cache_perfis_preview
    try:
        from apostas.alertas.historico_alertas import (
            agregar_por_mercado, carregar_perfis,
        )
        from apostas.utils.config import project_root
    except Exception:  # noqa: BLE001
        return ""
    if _cache_perfis_preview is None:
        try:
            csv_path = (project_root() / "dados" / "tracking"
                        / "sugestoes_live_v2.csv")
            _cache_perfis_preview = carregar_perfis(csv_path) or {}
        except Exception:  # noqa: BLE001
            _cache_perfis_preview = {}
    if not _cache_perfis_preview:
        return ""
    merc_key = "remates_a_baliza" if mercado == "sot" else mercado
    try:
        agg = agregar_por_mercado(_cache_perfis_preview).get((liga, merc_key))
    except Exception:  # noqa: BLE001
        return ""
    if agg is None or agg.n_resolvidos < 10 or agg.roi is None:
        return ""
    sinal = "+" if agg.roi >= 0 else ""
    return f"📊 ROI {sinal}{agg.roi:.1f}% em {agg.n_resolvidos} alertas"


def _formatar_aposta(aposta: ApostaRecomendada) -> str:
    emoji, nome = _NOMES_MERCADO[aposta.mercado]
    estrelas = "⭐" * aposta.estrelas
    direccao_up = aposta.direccao.upper()
    return (
        f"   {estrelas} {emoji} *{direccao_up} {aposta.linha:.1f} {nome}* "
        f"@ {aposta.odd_fair:.2f}  ({aposta.prob*100:.0f}% · "
        f"proj {aposta.projeccao})"
    )


def _emoji_padroes(padroes: list[str]) -> str:
    """Compacta padroes em icons sequenciais para a linha resumo."""
    icons = []
    for p in padroes:
        if p.startswith("⚡"):
            icons.append("⚡")
        elif p.startswith("🟡"):
            icons.append("🟡")
        elif p.startswith("🎯"):
            icons.append("🎯")
        elif p.startswith("🔄"):
            icons.append("🔄")
        elif p.startswith("🔥"):
            icons.append("🔥")
    return "".join(icons)


def _formatar_linha(j: LinhaJogoPreview) -> list[str]:
    """Produz linhas de texto para 1 jogo:
      1. Titulo: hora + equipas + icons de padroes
      2. Liga + projecoes brutas (compactas)
      3. Aposta recomendada (quando ha edge >=7%): mercado + linha + odd justa
      4. ROI historico do (liga, mercado) se disponivel
      5. Padrao detalhado principal (quando ha)
    """
    linhas = []
    titulo = f"⚽ *{j.hora}*  {j.home} vs {j.away}"
    icons = _emoji_padroes(j.padroes)
    if icons:
        titulo += f"  {icons}"
    linhas.append(titulo)

    # Projecoes compactas (quando temos dados)
    projs = []
    if j.golos_esperados is not None:
        projs.append(f"⚽ {j.golos_esperados:.1f}")
    if j.cards_esperados is not None:
        projs.append(f"🟨 {j.cards_esperados:.1f}")
    if j.cantos_esperados is not None:
        projs.append(f"🚩 {j.cantos_esperados:.1f}")
    if j.sot_esperados is not None:
        projs.append(f"🎯 {j.sot_esperados:.1f}")
    if projs:
        linhas.append(f"   _{j.liga}_  ·  {' · '.join(projs)}")
    else:
        linhas.append(f"   _{j.liga}_")

    # Aposta recomendada (melhor mercado por edge)
    aposta = _escolher_aposta(j)
    if aposta is not None:
        linhas.append(_formatar_aposta(aposta))
        # ROI historico desse mercado nessa liga (se >=10 resolvidos)
        roi_line = _roi_para_mercado(j.liga, aposta.mercado)
        if roi_line:
            linhas.append(f"   {roi_line}")

    # Padrao detalhado (max 1 — espaco Telegram)
    if j.padroes:
        linhas.append(f"   → {j.padroes[0]}")
    return linhas


def formatar_preview_telegram(
    preview: PreviewDia, *, titulo: str = "📅 Preview",
) -> str:
    """Formato Telegram-ready. Multi-dia agrupa por data."""
    linhas = [f"*{titulo}*"]
    span = preview.data_fim - preview.data_inicio
    if span.days > 1:
        linhas.append(
            f"_{preview.data_inicio:%d/%m} → {preview.data_fim:%d/%m}_"
        )
    else:
        linhas.append(f"_{preview.data_inicio:%d/%m/%Y}_")
    linhas.append("")

    if not preview.jogos:
        linhas.append("_Sem jogos nas ligas prioritarias nesta janela._")
        return "\n".join(linhas)

    linhas.append(f"*{len(preview.jogos)} jogos*")
    linhas.append("")

    # Agrupa por data quando multi-dia
    data_actual = None
    for j in preview.jogos:
        for linha in _formatar_linha(j):
            linhas.append(linha)
        linhas.append("")

    # Legenda icons no fim (compacta)
    linhas.append(
        "_Aposta_: ⭐⭐⭐ = edge forte (prob >=68%); @ e a odd justa — so apostar se a casa paga MAIS."
    )
    linhas.append(
        "_Padroes_: ⚡ ambas carregam 2T · 🟡 discrepancia cards · "
        "🎯 posse clara · 🔄 vira jogos · 🔥 a perder empurra"
    )

    return "\n".join(linhas)
