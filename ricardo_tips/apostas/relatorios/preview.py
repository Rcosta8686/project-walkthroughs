"""Previews pre-match com cenarios tacticos — para enviar as 17h diario
e as sextas (previa fim-de-semana).

A logica puxa:
  - Lista de jogos do(s) dia(s) via `sofascore_live.listar_jogos_do_dia`.
  - Para cada jogo, consulta `confronto.analisar_confronto(home_id, away_id)`
    (sem minuto/score — pre-match) → ResultadoConfronto com projeccoes
    esperadas, padroes cruzados e alertas de fadiga.
  - Formata em texto Telegram-ready com 1-2 linhas por jogo + os padroes
    descobertos.

Nao chama Sofascore pelo live predictor — e so pre-match, mais barato.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from apostas.utils.logger import get_logger

log = get_logger(__name__)


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
    """Produz 1-3 linhas de texto para 1 jogo."""
    linhas = []
    titulo = f"⚽ *{j.hora}*  {j.home} vs {j.away}"
    icons = _emoji_padroes(j.padroes)
    if icons:
        titulo += f"  {icons}"
    linhas.append(titulo)

    # Projecoes (quando temos dados)
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
        linhas.append(f"   _{j.liga}_  ·  Projecoes: {' · '.join(projs)}")
    else:
        linhas.append(f"   _{j.liga}_")

    # Primeiro padrao detalhado (nao mais que 1 por jogo para nao estourar
    # o limite Telegram de 4096 chars num preview de 15-30 jogos)
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
        "_Legenda_: ⚡ ambas carregam 2T · 🟡 discrepancia cards · "
        "🎯 posse clara · 🔄 vira jogos · 🔥 a perder empurra"
    )

    return "\n".join(linhas)
