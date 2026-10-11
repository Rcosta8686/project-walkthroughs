"""Sofascore LIVE: estado de jogos em curso via flaresolverr.

Expõe funções de alto nível que devolvem o estado de um jogo
in-play em dataclasses tipadas, prontas para alimentar o modelo
preditivo (``apostas.modelos.live_predictor``).

Endpoints Sofascore usados (não oficiais):
  /sport/football/events/live               — lista de jogos ao vivo
  /event/{id}                               — detalhes + marcador
  /event/{id}/statistics                    — remates, xG, posse, cantos, cartões
  /event/{id}/graph                         — xG momentum (série temporal)
  /event/{id}/incidents                     — golos, cartões, substituições
  /event/{id}/lineups                       — formações iniciais

Backend HTTP: flaresolverr (ver `_flaresolverr.py`). Falha com
``FlaresolverrIndisponivel`` se o container não estiver a correr.

**NÃO USAR em modo pré-match**: para pré-match já há
``apostas.ingestao.sofascore`` (que usa curl_cffi e tenta funcionar sem
Docker). Este módulo é só para o scanner live, onde já estamos a
pagar o custo de ter o docker a correr para evitar os 403.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apostas.ingestao import _flaresolverr
from apostas.utils.logger import get_logger

log = get_logger(__name__)

# NOTA: o subdomain `api.sofascore.com` tem auth mais apertada que
# devolve 403 mesmo com warmup/cookies. O dominio principal
# `www.sofascore.com` serve a mesma API em `/api/v1/...` sem o 403.
_BASE = "https://www.sofascore.com/api/v1"
_WARMUP = "https://www.sofascore.com/"

# Headers que a webapp Sofascore envia nos pedidos XHR a api.sofascore.com.
# Sem estes, a API devolve 403 Forbidden mesmo quando Cloudflare deixa passar.
_HEADERS_API = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


# ─────────────────────────────── Modelos ──────────────────────────────


@dataclass
class EstadoLive:
    """Snapshot do estado de um jogo in-play.

    Todos os floats podem ser None se o Sofascore ainda não tem o dado
    (ex: estatisticas detalhadas só aparecem após ~15 min).
    """
    event_id: int
    home: str
    away: str
    liga: str
    minuto: int               # minuto actual (ex: 73)
    estado: str               # "1H", "HT", "2H", "FT", etc
    marcador_casa: int
    marcador_fora: int

    # Expected goals acumulados
    xg_casa: float | None = None
    xg_fora: float | None = None

    # Remates
    shots_total_casa: int | None = None
    shots_total_fora: int | None = None
    shots_on_target_casa: int | None = None
    shots_on_target_fora: int | None = None
    big_chances_casa: int | None = None
    big_chances_fora: int | None = None

    # Posse e volume
    possession_casa: float | None = None   # 0.0-1.0
    possession_fora: float | None = None
    corners_casa: int | None = None
    corners_fora: int | None = None

    # Disciplina
    yellow_cards_casa: int = 0
    yellow_cards_fora: int = 0
    red_cards_casa: int = 0
    red_cards_fora: int = 0

    # Jogadores com cartão (nome, equipa, "yellow"/"red"), para
    # detectar jogadores em risco de 5º amarelo (contexto cross-jornada)
    cartoes_detalhe: list[dict] = field(default_factory=list)

    # xG momentum: xG nos últimos 15 min (quando o graph está disponivel)
    xg_recent_casa: float | None = None
    xg_recent_fora: float | None = None

    # Raw payloads (debug / para análises futuras)
    _raw_event: dict = field(default_factory=dict, repr=False)
    _raw_stats: dict = field(default_factory=dict, repr=False)

    def minutos_restantes(self) -> int:
        """Minutos de jogo que ainda faltam (regular, sem descontos)."""
        if self.estado in ("FT", "AET", "AP", "PEN"):
            return 0
        if self.estado == "HT":
            return 45
        return max(0, 90 - self.minuto)


# ─────────────────────────────── API ──────────────────────────────────


# IDs internos Sofascore das ligas que seguimos. Mais fiavel que
# matching por nome (ha mil "Premier League" no mundo).
# Confirmados via https://www.sofascore.com/tournament/football/<pais>/<liga>
LIGAS_SOFASCORE_IDS = {
    "Premier League (ENG)": 17,     # England Premier League
    "LaLiga":                8,     # Spain LaLiga
    "Serie A (ITA)":         23,    # Italy Serie A
    "Bundesliga":            35,    # Germany Bundesliga 1
    "Ligue 1 (FRA)":         34,    # France Ligue 1
    "Liga Portugal":         238,   # Portugal Primeira Liga
}


def listar_jogos_live(
    ligas_filter: tuple[str, ...] | None = None,
    ids_ligas: tuple[int, ...] | None = None,
) -> list[dict]:
    """Devolve lista de jogos a decorrer em todo o futebol.

    Cada entrada contém {id, homeTeam, awayTeam, tournament.name,
    tournament.category.name (pais), status.description,
    homeScore.current, awayScore.current, time}.

    Filtros disponiveis:
      ids_ligas: tuple de IDs internos Sofascore (ex: (17, 8, 23) para
                 EPL + LaLiga + Serie A). Precisao maxima.
      ligas_filter: substrings do tournament.name — menos preciso, apanha
                    "Premier League" de outros paises.
    Se ambos vazios, devolve todos os jogos.
    """
    resp = _flaresolverr.get(
        f"{_BASE}/sport/football/events/live",
        headers=_HEADERS_API,
        warmup_url=_WARMUP,
    )
    if resp.status != 200:
        log.warning("Sofascore live events: HTTP %d", resp.status)
        return []
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Resposta nao-JSON em events/live: %s", exc)
        return []
    eventos = data.get("events", [])
    if ids_ligas:
        ids_set = set(ids_ligas)
        eventos = [
            e for e in eventos
            if (e.get("tournament") or {}).get("uniqueTournament", {}).get("id") in ids_set
            or (e.get("tournament") or {}).get("id") in ids_set
        ]
    elif ligas_filter:
        lf = tuple(f.lower() for f in ligas_filter)
        eventos = [
            e for e in eventos
            if any(f in (e.get("tournament", {}).get("name") or "").lower() for f in lf)
        ]
    return eventos


def obter_estado_live(event_id: int) -> EstadoLive | None:
    """Puxa + agrega todas as estatisticas live de um jogo.

    Faz 3 chamadas Sofascore: /event, /event/statistics, /event/incidents.
    Devolve ``None`` se o jogo nao existe ou nao esta disponivel.
    """
    event_json = _get_json(f"/event/{event_id}")
    if event_json is None or "event" not in event_json:
        return None
    ev = event_json["event"]
    estado = EstadoLive(
        event_id=event_id,
        home=ev.get("homeTeam", {}).get("name", "?"),
        away=ev.get("awayTeam", {}).get("name", "?"),
        liga=ev.get("tournament", {}).get("name", "?"),
        minuto=_minuto_actual(ev),
        estado=ev.get("status", {}).get("code") or ev.get("status", {}).get("description", "?"),
        marcador_casa=int((ev.get("homeScore") or {}).get("current", 0) or 0),
        marcador_fora=int((ev.get("awayScore") or {}).get("current", 0) or 0),
        _raw_event=ev,
    )

    stats_json = _get_json(f"/event/{event_id}/statistics")
    if stats_json:
        _preencher_stats(estado, stats_json)

    incidents_json = _get_json(f"/event/{event_id}/incidents")
    if incidents_json:
        _preencher_cartoes(estado, incidents_json)

    # Graph (xG momentum) - opcional, pode nao existir em todos os jogos
    graph_json = _get_json(f"/event/{event_id}/graph")
    if graph_json:
        _preencher_xg_recent(estado, graph_json)

    return estado


# ───────────────────────── Helpers de parsing ─────────────────────────


def _get_json(path: str) -> dict | None:
    try:
        resp = _flaresolverr.get(
            f"{_BASE}{path}",
            headers=_HEADERS_API,
            warmup_url=_WARMUP,
        )
    except _flaresolverr.FlaresolverrIndisponivel:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore %s erro: %s", path, exc)
        return None
    if resp.status == 404:
        return None
    if resp.status != 200:
        log.debug("Sofascore %s HTTP %d", path, resp.status)
        return None
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("Sofascore %s nao-JSON: %s", path, exc)
        return None
    # Sofascore encapsula erros no body com status 200: {"error": {"code": 403, ...}}
    # 404 e benigno (ligas sem stats detalhadas), nao polui o terminal.
    if isinstance(data, dict) and "error" in data and "events" not in data:
        err = data.get("error") or {}
        code = err.get("code")
        if code == 404:
            log.debug("Sofascore %s body 404 (stats indisponiveis)", path)
        else:
            log.warning("Sofascore %s body error: %s", path, err)
        return None
    return data


def _minuto_actual(ev: dict) -> int:
    """Extrai o minuto actual (ou 45 se intervalo, 90+ se descontos)."""
    tempo = ev.get("time", {}) or {}
    # Em jogos ao vivo, time.initial e o segundo do inicio do periodo
    # time.currentPeriodStartTimestamp pode ser util; o mais simples
    # e usar o status.
    status_code = ev.get("status", {}).get("code")
    if status_code in (31, 32, 33):  # HT, End 1st half
        return 45
    if status_code == 6:  # 1st half
        periodo_duracao = (tempo.get("initial", 0) or 0) // 60
        return min(45, periodo_duracao)
    if status_code == 7:  # 2nd half
        periodo_duracao = (tempo.get("initial", 0) or 0) // 60
        return min(90, 45 + periodo_duracao)
    if status_code in (100, 110, 120):  # FT, AET, PEN
        return 90
    # Fallback: usa initial directamente
    return min(120, (tempo.get("initial", 0) or 0) // 60)


def _as_percent(v: Any) -> float:
    """Converte '58%' em 0.58."""
    return float(str(v).replace("%", "")) / 100.0


_MAPA_STATS = {
    # key normalizada → (attr_casa, attr_fora, converter)
    "expected goals":        ("xg_casa", "xg_fora", float),
    "total shots":           ("shots_total_casa", "shots_total_fora", int),
    "shots on target":       ("shots_on_target_casa", "shots_on_target_fora", int),
    "big chances":           ("big_chances_casa", "big_chances_fora", int),
    "ball possession":       ("possession_casa", "possession_fora", _as_percent),
    "corner kicks":          ("corners_casa", "corners_fora", int),
    "yellow cards":          ("yellow_cards_casa", "yellow_cards_fora", int),
    "red cards":             ("red_cards_casa", "red_cards_fora", int),
}


def _preencher_stats(estado: EstadoLive, stats_payload: dict) -> None:
    estado._raw_stats = stats_payload
    grupos = stats_payload.get("statistics", [])
    for periodo in grupos:
        if periodo.get("period") != "ALL":
            continue
        for grupo in periodo.get("groups", []):
            for item in grupo.get("statisticsItems", []):
                nome = (item.get("name") or "").lower()
                mapping = _MAPA_STATS.get(nome)
                if mapping is None:
                    continue
                attr_c, attr_f, conv = mapping
                try:
                    setattr(estado, attr_c, conv(item.get("home")))
                    setattr(estado, attr_f, conv(item.get("away")))
                except (ValueError, TypeError):
                    continue


def _preencher_cartoes(estado: EstadoLive, incidents_payload: dict) -> None:
    for inc in incidents_payload.get("incidents", []) or []:
        tipo = (inc.get("incidentType") or "").lower()
        if tipo != "card":
            continue
        classe = (inc.get("incidentClass") or "").lower()
        if classe not in ("yellow", "red", "yellowred"):
            continue
        is_home = bool(inc.get("isHome"))
        jogador = (inc.get("player") or {}).get("name", "?")
        estado.cartoes_detalhe.append({
            "minuto": inc.get("time"),
            "jogador": jogador,
            "equipa": "casa" if is_home else "fora",
            "classe": "red" if classe != "yellow" else "yellow",
        })


def _preencher_xg_recent(estado: EstadoLive, graph_payload: dict) -> None:
    """Soma o xG dos ultimos 15 minutos a partir da serie temporal."""
    # Estrutura: graphPoints = [{minute: 12, value: 0.15}, ...]
    serie_casa = (graph_payload.get("home") or {}).get("graphPoints") or graph_payload.get("homeGraphPoints") or []
    serie_fora = (graph_payload.get("away") or {}).get("graphPoints") or graph_payload.get("awayGraphPoints") or []
    corte = estado.minuto - 15
    if corte < 0:
        corte = 0
    xg_c = sum(float(p.get("value", 0) or 0) for p in serie_casa if (p.get("minute") or 0) >= corte)
    xg_f = sum(float(p.get("value", 0) or 0) for p in serie_fora if (p.get("minute") or 0) >= corte)
    estado.xg_recent_casa = xg_c
    estado.xg_recent_fora = xg_f


def _tratar_percent(v: Any) -> float:
    """Converte '58%' em 0.58. (Usado em _MAPA_STATS, aqui apenas para doc.)"""
    return float(str(v).replace("%", "")) / 100.0
