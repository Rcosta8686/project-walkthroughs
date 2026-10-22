"""Ingestão LIVE: puxa odds de jogos em curso (in-play).

Diferenças face a `odds_api.py` (que puxa pre-match):

1. **Endpoint diferente**: usa `/sports/{sport}/events/{event_id}/odds` em
   vez de `/sports/{sport}/odds`. Este endpoint tem três vantagens:
   - Suporta mais mercados: alem de h2h/totals/spreads, aceita
     `btts` (Both Teams To Score) e `double_chance` (dupla hipotese).
   - Devolve odds in-play (quando o jogo está a decorrer).
   - Permite filtrar por evento → so puxas odds dos jogos que te
     interessam agora.

2. **Custo em credits**: cada chamada custa `regions × markets`
   credits POR EVENTO. Com regions=eu+uk (2) + 5 mercados → 10
   credits por jogo por scan. Para 5 jogos live, scan de 60s → 50
   credits/scan. 1 hora de scanning intensivo = 3000 credits.

3. **Workflow**:
   - GET /sports/{sport}/events (grátis) → lista todos os eventos
   - Filtra: só eventos cujo commence_time já passou mas <2h30 atrás
     (um jogo de futebol dura ~2h incluindo intervalo + descontos)
   - Para cada evento in-play, GET /sports/{sport}/events/{id}/odds
   - Grava em `odds_correntes` com timestamp fresco

Esta não é "trading de arbitragem" (= apostar em todos os resultados
para lock profit); é apenas o mesmo scanner Pinnacle→softs, mas em
tempo real. A ideia: durante o jogo, Pinnacle (sharp) ajusta o preço
mais rápido ao estado real da partida. As softs atrasam. Quando a
diferença excede o teu EV mínimo, há oportunidade.

Importante: softs frequentemente SUSPENDEM mercados in-play (em
momentos de penálti, substituição, cartão vermelho). Verifica sempre
na casa se a aposta ainda está disponível antes de confirmar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

import requests

from apostas.ingestao.odds_api import (
    BOOKMAKERS_DEFAULT,
    LIGAS_ODDS_API,
    ResultadoOddsApi,
    _parse_commence_time,
    _processar_jogo,
)
from apostas.utils.config import get_env
from apostas.utils.tempo import agora_utc
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Liga
from sqlalchemy import select

log = get_logger(__name__)

_BASE_URL = "https://api.the-odds-api.com/v4"

# Mercados que o endpoint /events/{id}/odds suporta e que o standard /sports/
# nao suporta. Ordem reflecte priorid.: 1x2+golos+handicap sao mais líquidos.
MARKETS_LIVE_DEFAULT = "h2h,totals,spreads,btts,double_chance"


@dataclass
class ResultadoLive(ResultadoOddsApi):
    chamadas_api: int = 0
    jogos_live_vistos: int = 0
    sports_sem_live: list[str] = field(default_factory=list)


def puxar_live(
    sports: Iterable[str] | None = None,
    minutos_atras: float = 150.0,
    minutos_frente: float = 10.0,
    bookmakers: str = BOOKMAKERS_DEFAULT,
    regions: str = "eu,uk",
    markets: str = MARKETS_LIVE_DEFAULT,
    modo: str | None = None,
) -> ResultadoLive:
    """Puxa odds live dos jogos em curso para gravar em odds_correntes.

    Parameters
    ----------
    sports:
        Keys da Odds API (ex: ``soccer_epl``). Default: todas as 6 ligas
        configuradas em ``LIGAS_ODDS_API``.
    minutos_atras:
        Quão longe no passado para considerar um jogo ainda "live".
        Default 150min = 2h30 (jogo + intervalo + descontos).
    minutos_frente:
        Pequeno buffer para apanhar jogos que começam já de seguida.
        Default 10min.
    bookmakers:
        Lista de keys (separadas por vírgula). Default = Pinnacle + softs
        (mesma lista do pre-match).
    regions:
        Default ``eu,uk`` (reduz custo vs ``eu,uk,us,au`` do pre-match).
    markets:
        Default ``h2h,totals,spreads,btts,double_chance``. Mais mercados =
        mais credits por chamada.
    """
    modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
    resultado = ResultadoLive()

    if modo == "desenvolvimento":
        log.info("Modo desenvolvimento: puxar_live é no-op (sem mocks live).")
        return resultado

    sport_keys = list(sports) if sports is not None else list(LIGAS_ODDS_API.values())
    sport_para_liga = {v: k for k, v in LIGAS_ODDS_API.items()}
    key = get_env("ODDS_API_KEY", required=True)
    agora = agora_utc()
    inicio = agora - timedelta(minutes=minutos_atras)
    fim = agora + timedelta(minutes=minutos_frente)

    with abrir_sessao() as s:
        for sport in sport_keys:
            nome_liga = sport_para_liga.get(sport)
            if nome_liga is None:
                continue
            liga = s.scalar(select(Liga).where(Liga.nome == nome_liga))
            if liga is None:
                resultado.erros.append(f"Liga '{nome_liga}' nao esta na BD.")
                continue
            try:
                eventos_live = _listar_eventos_live(sport, key, inicio, fim, resultado)
            except Exception as exc:  # noqa: BLE001
                resultado.erros.append(f"{sport} /events: {exc}")
                continue
            if not eventos_live:
                resultado.sports_sem_live.append(sport)
                continue
            log.info("  → %s: %d jogos in-play detectados.", sport, len(eventos_live))
            resultado.jogos_live_vistos += len(eventos_live)
            for ev in eventos_live:
                event_id = ev.get("id")
                if not event_id:
                    continue
                try:
                    jogo_json = _puxar_odds_evento(
                        sport, event_id, key, bookmakers, regions, markets, resultado,
                    )
                except Exception as exc:  # noqa: BLE001
                    resultado.erros.append(
                        f"{sport}/{event_id}: {exc}"
                    )
                    continue
                if jogo_json is None:
                    continue
                _processar_jogo(s, jogo_json, liga, resultado)

    log.info(
        "LIVE: %d chamadas API, %d jogos live, %d atualizados, %d odds novas, "
        "credits restantes: %s",
        resultado.chamadas_api, resultado.jogos_live_vistos,
        resultado.jogos_atualizados, resultado.odds_criadas,
        resultado.credits_restantes,
    )
    return resultado


def _listar_eventos_live(
    sport: str, api_key: str,
    inicio: datetime, fim: datetime,
    resultado: ResultadoLive,
) -> list[dict]:
    """Lista eventos cujo commence_time esta na janela [inicio, fim].

    Chamada ao /events e GRATIS (nao consome credits). Devolve lista de
    eventos com id + home_team + away_team + commence_time.
    """
    url = f"{_BASE_URL}/sports/{sport}/events"
    params = {
        "apiKey": api_key,
        "dateFormat": "iso",
    }
    resp = requests.get(url, params=params, timeout=20)
    if not resp.ok:
        raise requests.HTTPError(
            f"HTTP {resp.status_code} em /sports/{sport}/events: {resp.text[:200]}"
        )
    # /events nao debita credits, mas o header continua lá
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        try:
            resultado.credits_restantes = int(remaining)
        except ValueError:
            pass
    data = resp.json()
    eventos_live = []
    for ev in data if isinstance(data, list) else []:
        ts = _parse_commence_time(
            (ev.get("commence_time") or "").replace("+00:00", "Z")
        )
        if ts is None:
            continue
        if inicio <= ts <= fim:
            eventos_live.append(ev)
    return eventos_live


def _puxar_odds_evento(
    sport: str, event_id: str, api_key: str,
    bookmakers: str, regions: str, markets: str,
    resultado: ResultadoLive,
) -> dict | None:
    """Puxa odds para 1 evento. Custo: regions × markets credits."""
    url = f"{_BASE_URL}/sports/{sport}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "decimal",
        "bookmakers": bookmakers,
        "dateFormat": "iso",
    }
    resp = requests.get(url, params=params, timeout=30)
    resultado.chamadas_api += 1
    if resp.status_code == 404:
        # Evento ja acabou ou nao existe
        return None
    if not resp.ok:
        raise requests.HTTPError(
            f"HTTP {resp.status_code}: {resp.text[:200]}"
        )
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        try:
            resultado.credits_restantes = int(remaining)
        except ValueError:
            pass
    data = resp.json()
    # /events/{id}/odds devolve 1 evento (dict), nao uma lista
    if isinstance(data, dict) and data.get("bookmakers"):
        return data
    return None
