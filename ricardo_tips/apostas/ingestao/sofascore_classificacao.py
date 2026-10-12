"""Classificacao e importancia do jogo via Sofascore.

Endpoint: /unique-tournament/{id}/season/{id}/standings/total

Para cada equipa devolve: posicao, pontos, jogos, pontos atras do lider,
pontos frente da zona de descida. A partir disto o analista calcula
IMPORTANCIA DO JOGO:

  - Title race (ambos top 4, proximos): factor 1.3, "⚡ TITLE RACE"
  - 6-pointer descida: factor 1.3, "⚡ 6-POINTER DESCIDA"
  - Um deles na zona de descida: factor 1.15, "⚠ Luta pela permanencia"
  - Ambos mid-table, longe de tudo, > 25 jogos: factor 0.75,
    "↓ Jogo sem stakes"
  - Resto: factor 1.0

Cache 12h em disco: dados/classificacoes/{tournament_id}.json.
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

_CACHE_DIR = project_root() / "dados" / "classificacoes"
_CACHE_TTL = 12 * 60 * 60  # 12 horas


def _cache_path(tournament_id: int) -> Path:
    return _CACHE_DIR / f"{tournament_id}.json"


def _ler_cache(tournament_id: int) -> dict | None:
    p = _cache_path(tournament_id)
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


def _escrever_cache(tournament_id: int, data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_fetched_at"] = time.time()
    try:
        with _cache_path(tournament_id).open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha a escrever cache classificacao %d: %s", tournament_id, exc)


def _obter_season_actual(tournament_id: int) -> int | None:
    """Devolve o ID da epoca mais recente da liga."""
    try:
        resp = _flaresolverr.get(
            f"{_BASE}/unique-tournament/{tournament_id}/seasons",
            headers=_HEADERS_API, warmup_url=_WARMUP,
        )
        if resp.status != 200:
            return None
        data = resp.json()
        seasons = data.get("seasons", [])
        if seasons:
            return seasons[0]["id"]
    except Exception as exc:  # noqa: BLE001
        log.debug("Falha a obter season %d: %s", tournament_id, exc)
    return None


def obter_classificacao(tournament_id: int) -> dict:
    """{nome_equipa_lowercase: dados_posicao}. Vazio em falha.

    Dados por equipa:
      pos: posicao (1 = lider)
      pts: pontos
      played: jogos jogados
      pts_atras_lider: 0 = e o lider
      pts_frente_descida: 0 = esta na zona de descida
      gf, ga: golos marcados/sofridos
      total_teams: tamanho da divisao (p/ calcular zonas)
    """
    cached = _ler_cache(tournament_id)
    if cached:
        return cached.get("teams", {})

    season_id = _obter_season_actual(tournament_id)
    if not season_id:
        return {}

    try:
        resp = _flaresolverr.get(
            f"{_BASE}/unique-tournament/{tournament_id}/season/{season_id}/standings/total",
            headers=_HEADERS_API, warmup_url=_WARMUP,
        )
        if resp.status != 200:
            log.debug("Standings tournament %d: HTTP %d", tournament_id, resp.status)
            return {}
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("Falha a obter standings %d: %s", tournament_id, exc)
        return {}

    rows = []
    for standing in data.get("standings", []):
        if standing.get("type") == "total":
            rows = standing.get("rows", [])
            break
    if not rows:
        return {}

    total_teams = len(rows)
    # Zona de descida: ultimas 3 posicoes por convencao (ajusta por liga
    # se necessario — PL/LaLiga/Bundesliga descem 3, Championship 3, etc).
    descida_pos = max(1, total_teams - 2)
    lider_pts = rows[0].get("points", 0)
    descida_pts = rows[descida_pos - 1].get("points", 0) if descida_pos <= total_teams else 0

    teams = {}
    for row in rows:
        name = (row.get("team") or {}).get("name", "").lower()
        if not name:
            continue
        pts = row.get("points", 0)
        teams[name] = {
            "pos": row.get("position", 0),
            "pts": pts,
            "played": row.get("matches", 0),
            "pts_atras_lider": lider_pts - pts,
            "pts_frente_descida": pts - descida_pts,
            "gf": row.get("goalsFor", 0),
            "ga": row.get("goalsAgainst", 0),
            "total_teams": total_teams,
        }

    _escrever_cache(tournament_id, {"teams": teams, "season_id": season_id})
    return teams


def buscar_posicao(classificacao: dict, nome_equipa: str) -> dict | None:
    """Procura por nome exacto, depois por substring (Sofascore varia nomes)."""
    if not nome_equipa or not classificacao:
        return None
    n = nome_equipa.lower()
    if n in classificacao:
        return classificacao[n]
    for k, v in classificacao.items():
        if n in k or k in n:
            return v
    return None


def importancia_jogo(liga_id: int, home: str, away: str) -> tuple[float, str | None]:
    """Factor 0.7-1.3 baseado no contexto classificativo do jogo.

    Devolve (factor, descricao_racional). Descricao None se nada notavel.
    """
    classif = obter_classificacao(liga_id)
    if not classif:
        return 1.0, None

    pc = buscar_posicao(classif, home)
    pf = buscar_posicao(classif, away)
    if not pc or not pf:
        return 1.0, None

    total = pc.get("total_teams", 20)
    pos_c, pos_f = pc["pos"], pf["pos"]
    pts_atras_c, pts_atras_f = pc["pts_atras_lider"], pf["pts_atras_lider"]
    pts_frente_desc_c, pts_frente_desc_f = pc["pts_frente_descida"], pf["pts_frente_descida"]
    played = max(pc["played"], pf["played"])

    # 1. Title race: ambos top 4, proximos no pontos
    if pos_c <= 4 and pos_f <= 4 and max(pts_atras_c, pts_atras_f) <= 6:
        return 1.3, f"⚡ TITLE RACE (#{pos_c} vs #{pos_f})"

    # 2. 6-pointer descida: ambos nas ultimas 4 posicoes
    if pos_c >= total - 3 and pos_f >= total - 3:
        return 1.3, f"⚡ 6-POINTER DESCIDA (#{pos_c} vs #{pos_f})"

    # 3. Luta directa pela Europa (ambos entre 4 e 7, proximos)
    if 4 <= pos_c <= 7 and 4 <= pos_f <= 7 and abs(pos_c - pos_f) <= 2:
        return 1.15, f"⚡ Luta por Europa (#{pos_c} vs #{pos_f})"

    # 4. Um deles em zona de descida ou a 3pts da descida
    em_perigo_c = pts_frente_desc_c <= 3
    em_perigo_f = pts_frente_desc_f <= 3
    if em_perigo_c or em_perigo_f:
        qual = home if em_perigo_c else away
        return 1.15, f"⚠ {qual} luta pela permanencia"

    # 5. Jogo sem stakes: ambos mid-table, longe de tudo, muitos jogos
    mid_c = (total // 3) < pos_c < (total - total // 3)
    mid_f = (total // 3) < pos_f < (total - total // 3)
    if mid_c and mid_f:
        longe_lider = min(pts_atras_c, pts_atras_f) >= 12
        longe_descida = min(pts_frente_desc_c, pts_frente_desc_f) >= 8
        if longe_lider and longe_descida and played >= 25:
            return 0.75, "↓ Jogo sem stakes (ambos meio-tabela, longe de tudo)"

    return 1.0, None
