"""Contexto pre-jogo via Sofascore: H2H, forma recente, AI Insights.

Chamados UMA VEZ por jogo (nao mudam durante os 90min, so faria sentido
re-fetch se queres garantir ultima atualizacao). Cache 24h em disco.

Endpoints:
  /event/{id}/h2h/events       — ultimos jogos entre as 2 equipas
  /team/{team_id}/events/last/{page}  — ultimos jogos de uma equipa
  /event/{id}/pregame-insights  — AI insights (Sofascore gera automatico)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from apostas.ingestao import _flaresolverr
from apostas.utils.config import project_root
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE = "https://www.sofascore.com/api/v1"
_WARMUP = "https://www.sofascore.com/"
_HEADERS_API = {
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

_CACHE_DIR = project_root() / "dados" / "contexto_jogos"
_CACHE_TTL = 24 * 60 * 60  # 24 horas


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _ler_cache(key: str) -> dict | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as fp:
            data = json.load(fp)
        if time.time() - data.get("_fetched_at", 0) > _CACHE_TTL:
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def _escrever_cache(key: str, data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_fetched_at"] = time.time()
    try:
        with _cache_path(key).open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("Falha cache contexto %s: %s", key, exc)


def _get(path: str) -> dict | None:
    try:
        resp = _flaresolverr.get(
            f"{_BASE}{path}", headers=_HEADERS_API, warmup_url=_WARMUP,
        )
        if resp.status != 200:
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("Sofascore %s falha: %s", path, exc)
        return None


# ───────────────────── H2H ─────────────────────

def obter_h2h(event_id: int) -> dict:
    """Agrega stats dos ultimos jogos H2H entre as 2 equipas.

    Devolve dict com:
      n_jogos: total analisados
      over_2_5_rate: fraccao de jogos com total >= 3 golos
      btts_rate: fraccao com ambas marcam
      media_golos_total: media total golos/jogo
      vitorias_casa_rate: fraccao com equipa casa (deste jogo) venceu
      ultimo_jogo_golos: total do ultimo jogo
    """
    key = f"h2h_{event_id}"
    cached = _ler_cache(key)
    if cached:
        return cached.get("data", {})

    data = _get(f"/event/{event_id}/h2h/events")
    if not data:
        _escrever_cache(key, {"data": {}})
        return {}

    events = data.get("events") or []
    if not events:
        _escrever_cache(key, {"data": {}})
        return {}

    # Identificar as 2 equipas do jogo actual via event meta
    # Os eventos H2H tem homeTeam.id e awayTeam.id. Precisamos identificar
    # qual delas corresponde ao home actual. Fetch event para obter home_id.
    event_atual = _get(f"/event/{event_id}")
    if event_atual:
        ev = event_atual.get("event") or {}
        home_id = (ev.get("homeTeam") or {}).get("id")
    else:
        home_id = None

    total = 0
    over_2_5 = 0
    btts = 0
    soma_golos = 0
    vitorias_casa_actual = 0
    ultimo_jogo_total = None

    for i, e in enumerate(events[:20]):  # ultimos 20 H2H
        hs = (e.get("homeScore") or {}).get("current")
        aws = (e.get("awayScore") or {}).get("current")
        if hs is None or aws is None:
            continue
        total += 1
        tot = hs + aws
        soma_golos += tot
        if tot >= 3:
            over_2_5 += 1
        if hs > 0 and aws > 0:
            btts += 1
        # Qual equipa deste jogo H2H corresponde ao "home actual"?
        this_home_id = (e.get("homeTeam") or {}).get("id")
        if home_id and this_home_id == home_id:
            if hs > aws:
                vitorias_casa_actual += 1
        elif home_id:
            # home actual era fora neste H2H
            if aws > hs:
                vitorias_casa_actual += 1
        if i == 0:
            ultimo_jogo_total = tot

    if total == 0:
        _escrever_cache(key, {"data": {}})
        return {}

    resultado = {
        "n_jogos": total,
        "over_2_5_rate": over_2_5 / total,
        "btts_rate": btts / total,
        "media_golos_total": soma_golos / total,
        "vitorias_casa_rate": vitorias_casa_actual / total,
        "ultimo_jogo_total": ultimo_jogo_total,
    }
    _escrever_cache(key, {"data": resultado})
    return resultado


# ───────────────────── Forma recente ─────────────────────

def obter_forma_recente(team_id: int, n: int = 5) -> dict:
    """Ultimos N jogos da equipa.

    Devolve:
      wdl: str tipo "WWLDW" (W=vitoria, D=empate, L=derrota)
      pts: pontos nesses jogos (W=3, D=1, L=0)
      golos_marcados_media: por jogo
      golos_sofridos_media: por jogo
      sem_marcar: ultimos N em que nao marcou
      sem_sofrer: ultimos N consecutivos em que nao sofreu (clean sheets)
    """
    key = f"forma_{team_id}"
    cached = _ler_cache(key)
    if cached:
        return cached.get("data", {})

    data = _get(f"/team/{team_id}/events/last/0")
    if not data:
        _escrever_cache(key, {"data": {}})
        return {}

    events = data.get("events") or []
    if not events:
        _escrever_cache(key, {"data": {}})
        return {}

    # Ordem Sofascore: do mais recente para o mais antigo
    jogados = [e for e in events if (e.get("status") or {}).get("code") == 100][:n]
    if not jogados:
        _escrever_cache(key, {"data": {}})
        return {}

    wdl_chars = []
    pts = 0
    gm_total = 0
    gs_total = 0
    sem_marcar_streak = 0
    sem_sofrer_streak = 0
    streak_vivo_sm = True
    streak_vivo_ss = True

    for e in jogados:
        hs = (e.get("homeScore") or {}).get("current") or 0
        aws = (e.get("awayScore") or {}).get("current") or 0
        this_home_id = (e.get("homeTeam") or {}).get("id")
        era_casa = this_home_id == team_id
        gm = hs if era_casa else aws
        gs = aws if era_casa else hs

        gm_total += gm
        gs_total += gs
        if gm > gs:
            wdl_chars.append("W"); pts += 3
        elif gm == gs:
            wdl_chars.append("D"); pts += 1
        else:
            wdl_chars.append("L")

        if streak_vivo_sm:
            if gm == 0:
                sem_marcar_streak += 1
            else:
                streak_vivo_sm = False
        if streak_vivo_ss:
            if gs == 0:
                sem_sofrer_streak += 1
            else:
                streak_vivo_ss = False

    nj = len(jogados)
    resultado = {
        "wdl": "".join(wdl_chars),
        "pts": pts,
        "pts_max": nj * 3,
        "golos_marcados_media": gm_total / nj,
        "golos_sofridos_media": gs_total / nj,
        "sem_marcar": sem_marcar_streak,
        "sem_sofrer": sem_sofrer_streak,
        "n_jogos": nj,
    }
    _escrever_cache(key, {"data": resultado})
    return resultado


# ───────────────────── AI Insights ─────────────────────

def obter_ai_insights(event_id: int) -> list[str]:
    """Devolve strings de insights que o Sofascore gera automaticamente.

    Ex: ["Barcelona scored in 7 of last 8 home matches",
         "Girona has lost 4 of last 5 away matches"].

    Nao traduz; usa diretamente. O analista interpreta via keywords.
    """
    key = f"insights_{event_id}"
    cached = _ler_cache(key)
    if cached:
        return cached.get("data", [])

    data = _get(f"/event/{event_id}/pregame-insights")
    if not data:
        _escrever_cache(key, {"data": []})
        return []

    textos = []
    # Estruturas possiveis: pregameInsights.insights[].text ou
    # pregameInsights.statistics[].description
    insights_data = data.get("pregameInsights") or data
    for secao in ("insights", "statistics", "facts"):
        for item in insights_data.get(secao, []) or []:
            if isinstance(item, dict):
                txt = (item.get("text") or item.get("description")
                       or item.get("value") or "")
                if txt:
                    textos.append(str(txt).strip())
            elif isinstance(item, str):
                textos.append(item.strip())

    textos = [t for t in textos if t][:10]
    _escrever_cache(key, {"data": textos})
    return textos
