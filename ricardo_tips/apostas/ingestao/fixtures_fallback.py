"""Lista de fixtures do dia sem depender de Sofascore.

Quando a Cloudflare bloqueia a Sofascore scheduled-events, o preview das
17h fica cego. Este modulo oferece a MESMA interface (`listar_jogos_do_dia`)
mas vai buscar fixtures ao `/v4/sports/{sport}/events` da Odds API.

Chaves:
    - Endpoint `/events` da Odds API e GRATIS (nao consome credits).
    - Nao devolve `sofascore_team_id`; mapeamos nome → sofascore_team_id
      consultando a nossa tabela EstatisticaEquipaJogo (que ja tem o
      cruzamento nome <-> id de todos os jogos ingeridos).
    - Shape de retorno identico a sofascore_live.listar_jogos_do_dia:
      [{"id", "startTimestamp", "homeTeam": {"id","name"},
        "awayTeam": {"id","name"}, "tournament": {"name"}}]

Isto permite usar o modulo sem alterar `preview.py` nem `_construir_linha`.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from sqlalchemy import func, select

from apostas.ingestao.odds_api import LIGAS_ODDS_API, _ALIAS_ODDS_API
from apostas.utils.config import get_env
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import EstatisticaEquipaJogo

log = get_logger(__name__)

_BASE_URL = "https://api.the-odds-api.com/v4"

# Nomes de liga que o preview conhece → sport_key da Odds API.
# Replicamos LIGAS_ODDS_API (que usa "La Liga" em vez de "LaLiga" como o
# Sofascore devolve) + aliases comuns.
_LIGA_PARA_SPORT: dict[str, str] = {
    # chaves canonicas = LIGAS_ODDS_API, aliases abaixo
    **LIGAS_ODDS_API,
    # aliases comuns (nomes como aparecem noutros sitios do codigo e BD)
    "LaLiga": "soccer_spain_la_liga",
    "La Liga Santander": "soccer_spain_la_liga",
    "Serie A (ITA)": "soccer_italy_serie_a",
    "Bundesliga (GER)": "soccer_germany_bundesliga",
    "Ligue 1 (FRA)": "soccer_france_ligue_one",
    "Liga Portugal Betclic": "soccer_portugal_primeira_liga",
    "Primeira Liga": "soccer_portugal_primeira_liga",
    "Eredivisie": "soccer_netherlands_eredivisie",
    "VriendenLoterij Eredivisie": "soccer_netherlands_eredivisie",
}

# cache nome_normalizado → sofascore_team_id (preenche no primeiro lookup)
_CACHE_NOME_SOFA_ID: dict[str, int] = {}


def _normaliza(nome: str) -> str:
    """Minusculas, sem espaços duplicados, trim."""
    return " ".join((nome or "").strip().lower().split())


def _canonical_name(nome_odds_api: str) -> str:
    """Nome Odds API → nome tal como aparece na BD (via _ALIAS_ODDS_API).

    Se nao houver alias registado, devolve o proprio nome.
    """
    return _ALIAS_ODDS_API.get(nome_odds_api, nome_odds_api)


def _carregar_cache_sofa_id() -> None:
    """Pre-carrega mapeamento nome_normalizado → sofascore_team_id a partir
    da tabela EstatisticaEquipaJogo. Idempotente; nunca re-carrega se ja
    existir cache populada."""
    if _CACHE_NOME_SOFA_ID:
        return
    try:
        with abrir_sessao() as s:
            # Para cada equipa, usa o id associado ao jogo MAIS RECENTE
            # (em caso de IDs antigos). Pequena optimizacao: group by nome.
            stmt = select(
                EstatisticaEquipaJogo.nome_equipa,
                EstatisticaEquipaJogo.sofascore_team_id,
                func.max(EstatisticaEquipaJogo.data_jogo),
            ).group_by(EstatisticaEquipaJogo.nome_equipa,
                       EstatisticaEquipaJogo.sofascore_team_id)
            for nome, sofa_id, _ in s.execute(stmt).all():
                if nome and sofa_id:
                    _CACHE_NOME_SOFA_ID[_normaliza(nome)] = int(sofa_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("fixtures_fallback: cache sofa_id falhou: %s", exc)


def _sofa_id_por_nome(nome: str) -> Optional[int]:
    """Dado um nome (nome como aparece na BD), devolve o sofascore_team_id.

    Tenta primeiro nome exacto. Se falhar, tenta nome sem sufixos comuns
    (FC, SC, CF, AFC) e variantes.
    """
    _carregar_cache_sofa_id()
    norm = _normaliza(nome)
    sid = _CACHE_NOME_SOFA_ID.get(norm)
    if sid is not None:
        return sid
    # Variantes sem sufixos
    sem_sufixos = norm
    for suf in (" fc", " sc", " cf", " afc", " ac", " ud", " cd", " sl", " sv"):
        if sem_sufixos.endswith(suf):
            sem_sufixos = sem_sufixos[: -len(suf)].strip()
    for pre in ("fc ", "ac ", "sc ", "cd ", "sl "):
        if sem_sufixos.startswith(pre):
            sem_sufixos = sem_sufixos[len(pre):].strip()
    if sem_sufixos != norm:
        sid = _CACHE_NOME_SOFA_ID.get(sem_sufixos)
        if sid is not None:
            return sid
    # Match por prefixo (ex "Man City" matches "manchester city" ou "man city")
    # cuidado: minimo 5 chars para evitar false matches
    if len(norm) >= 5:
        for n, s_id in _CACHE_NOME_SOFA_ID.items():
            if norm in n or n in norm:
                return s_id
    return None


def _hash_event_id(odds_api_event_id: str) -> int:
    """Converte string event id da Odds API num int determinista.

    O preview faz dedupe por event_id. 32 bits chegam para evitar colisoes
    plausiveis dentro de 1 dia.
    """
    h = hashlib.md5(odds_api_event_id.encode()).hexdigest()[:8]
    return int(h, 16)


def _parse_commence(ts_iso: str) -> Optional[datetime]:
    """ISO8601 → datetime UTC. Devolve None se invalido."""
    if not ts_iso:
        return None
    ts = ts_iso.replace("+00:00", "Z")
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts_iso).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None


def _listar_eventos_sport(sport: str, api_key: str, timeout: float = 20.0) -> list[dict]:
    """GET /sports/{sport}/events — gratis, devolve lista de eventos.

    Devolve [{"id","sport_key","home_team","away_team","commence_time"}, ...].
    """
    url = f"{_BASE_URL}/sports/{sport}/events"
    params = {"apiKey": api_key, "dateFormat": "iso"}
    resp = requests.get(url, params=params, timeout=timeout)
    if not resp.ok:
        raise requests.HTTPError(
            f"HTTP {resp.status_code} em /sports/{sport}/events: {resp.text[:200]}"
        )
    data = resp.json()
    return data if isinstance(data, list) else []


def listar_jogos_do_dia(
    data_iso: str,
    *,
    ids_ligas: tuple[int, ...] | None = None,
    incluir_live: bool = False,
    ligas_filtro: tuple[str, ...] | None = None,
) -> list[dict]:
    """Lista fixtures do dia a partir da Odds API, no shape Sofascore.

    data_iso: "YYYY-MM-DD" (data em UTC)
    ids_ligas: aceito por compatibilidade com a interface Sofascore;
      aqui filtramos por nome de liga (ligas_filtro) em vez de id.
    incluir_live: ignorado (devolvemos todos os jogos nessa data).
    ligas_filtro: subset de LIGAS_ODDS_API a consultar (default: todas).

    Devolve lista de dicts no shape:
      {"id", "startTimestamp", "homeTeam": {"id","name"},
       "awayTeam": {"id","name"}, "tournament": {"name"}}
    """
    try:
        dia = datetime.strptime(data_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("fixtures_fallback: data invalida %r", data_iso)
        return []
    inicio = dia
    fim = dia + timedelta(days=1)

    api_key = get_env("ODDS_API_KEY")
    if not api_key:
        log.warning("fixtures_fallback: ODDS_API_KEY nao configurada")
        return []

    # Quais sports consultar?
    if ligas_filtro:
        sports_a_consultar: list[tuple[str, str]] = [
            (liga, sport)
            for liga, sport in LIGAS_ODDS_API.items()
            if liga in ligas_filtro
        ]
    else:
        sports_a_consultar = list(LIGAS_ODDS_API.items())

    out: list[dict] = []
    seen_ids: set[int] = set()
    for liga_nome, sport in sports_a_consultar:
        try:
            eventos = _listar_eventos_sport(sport, api_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("fixtures_fallback %s (%s): %s", liga_nome, sport, exc)
            continue
        n_matched = 0
        n_sem_id = 0
        for ev in eventos:
            ts = _parse_commence(ev.get("commence_time") or "")
            if ts is None:
                continue
            if not (inicio <= ts < fim):
                continue
            home_raw = ev.get("home_team") or ""
            away_raw = ev.get("away_team") or ""
            home = _canonical_name(home_raw)
            away = _canonical_name(away_raw)
            home_sofa = _sofa_id_por_nome(home_raw) or _sofa_id_por_nome(home)
            away_sofa = _sofa_id_por_nome(away_raw) or _sofa_id_por_nome(away)
            if not home_sofa or not away_sofa:
                n_sem_id += 1
                log.debug(
                    "fixtures_fallback skip %s vs %s (sem sofa_id)",
                    home_raw, away_raw,
                )
                continue
            event_id = _hash_event_id(str(ev.get("id") or f"{home_raw}-{away_raw}-{ts}"))
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            out.append({
                "id": event_id,
                "startTimestamp": int(ts.timestamp()),
                "homeTeam": {"id": home_sofa, "name": home_raw},
                "awayTeam": {"id": away_sofa, "name": away_raw},
                "tournament": {"name": liga_nome},
            })
            n_matched += 1
        if eventos:
            log.info(
                "fixtures_fallback %s: %d/%d matched (sem id: %d)",
                liga_nome, n_matched, len(eventos), n_sem_id,
            )

    log.info("fixtures_fallback total %s: %d jogos", data_iso, len(out))
    return out


def listar_com_fallback(
    data_iso: str,
    *,
    ids_ligas: tuple[int, ...] | None = None,
    incluir_live: bool = False,
) -> list[dict]:
    """Tenta Sofascore primeiro; se devolve 0 ou lanca, usa Odds API.

    Interface identica a sofascore_live.listar_jogos_do_dia — pode ser
    passada directamente como `listar_fn` em `gerar_preview`.
    """
    try:
        from apostas.ingestao.sofascore_live import (
            listar_jogos_do_dia as _sofa,
        )
        jogos = _sofa(data_iso, ids_ligas=ids_ligas, incluir_live=incluir_live)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore listar_jogos_do_dia falhou (%s) — fallback Odds API", exc)
        jogos = []
    if jogos:
        return jogos
    log.info("Sofascore devolveu 0 jogos — tentando Odds API fallback")
    try:
        return listar_jogos_do_dia(data_iso, ids_ligas=ids_ligas, incluir_live=incluir_live)
    except Exception as exc:  # noqa: BLE001
        log.warning("Odds API fallback tambem falhou: %s", exc)
        return []
