"""Contexto de competicao: le o peso do jogo e a posicao na tabela.

Para o mega-cerebro poder dizer "esta equipa esta a jogar para segurar
porque ja esta qualificada" ou "esta equipa precisa de pontos — vai
atacar". Lia a competicao + fase + standings e devolve 1 objecto
compacto.

Fontes:
  - Standings via Sofascore /unique-tournament/{id}/season/{sid}/standings/total
  - Cache 24h em disco (gratis via flaresolverr)
  - Fallback silencioso: se nao conseguir pullar, devolve ContextoJogo
    com peso baseado so no nome da liga

Peso do jogo (ordem crescente):
  baixo    - ligas regionais, youth, pre-epoca
  medio    - liga domestica meia-tabela
  alto     - top-6 ligas, Taca, UCL/UEL fase de grupos
  muito_alto - final, semi, playoff decisivo, descida/subida
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from apostas.utils.config import project_root
from apostas.utils.logger import get_logger

log = get_logger(__name__)


_CACHE_DIR = project_root() / "dados" / "contexto_competicao"
_CACHE_TTL = 24 * 60 * 60  # 24 horas


# ─── Nomes de competicoes de peso ────────────────────────────────────

_UCL_UEL_CONFERENCE = (
    "champions league", "europa league", "conference league",
    "uefa super cup", "fifa club world",
)
_FINAIS = ("final",)
_SEMIS_PLAYOFFS = ("semi-final", "semifinal", "playoff", "play-off",
                   "quarter-final", "quarterfinal")
_TOP5_LIGAS = (
    "premier league", "laliga", "la liga", "serie a", "bundesliga",
    "ligue 1", "primeira liga", "eredivisie", "liga portugal",
)


@dataclass
class ContextoJogo:
    """Peso e posicao resumida para 1 jogo."""
    peso_jogo: str   # 'baixo' | 'medio' | 'alto' | 'muito_alto'
    razao: str
    # Standings (quando disponivel)
    posicao_casa: int | None = None
    posicao_fora: int | None = None
    pontos_casa: int | None = None
    pontos_fora: int | None = None
    jogos_restantes_casa: int | None = None
    jogos_restantes_fora: int | None = None
    # Resumos uteis
    casa_ja_qualificada: bool = False
    fora_ja_qualificada: bool = False
    casa_precisa_pts: bool = False
    fora_precisa_pts: bool = False


# ─── Cache ───────────────────────────────────────────────────────────

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
        log.debug("Falha cache standings %s: %s", key, exc)


# ─── Classificar peso ────────────────────────────────────────────────

def _classificar_peso(liga_nome: str, fase: str | None = None) -> tuple[str, str]:
    """Devolve (peso, razao) baseado no nome da liga e fase da competicao."""
    n = (liga_nome or "").lower()
    f = (fase or "").lower()

    # Youth/reserve/pre-season
    if any(x in n for x in ("u19", "u21", "u23", "reserve", "youth",
                              "friendly", "pre-season", "pre season")):
        return "baixo", "liga nao competitiva"

    # Finais e semis
    if any(x in f for x in _FINAIS) or " final" in n:
        return "muito_alto", "final"
    if any(x in f for x in _SEMIS_PLAYOFFS):
        return "muito_alto", "semi/playoff decisivo"

    # UCL/UEL/Conference
    if any(x in n for x in _UCL_UEL_CONFERENCE):
        return "alto", "competicao europeia"

    # Taca domestica
    if any(x in n for x in ("fa cup", "copa del rey", "coppa italia",
                              "dfb pokal", "coupe de france", "taca de portugal")):
        return "alto", "taca domestica"

    # Top 5 ligas
    if any(x in n for x in _TOP5_LIGAS):
        return "medio", "liga top"

    # Fallback
    return "baixo", "liga regular"


# ─── Fetcher Sofascore standings ─────────────────────────────────────

def _fetch_standings(tournament_id: int, season_id: int) -> list[dict]:
    """Puxa standings e devolve lista {team_id, posicao, pontos, jogos}."""
    try:
        from apostas.ingestao import flaresolverr as _flaresolverr
    except Exception:  # noqa: BLE001
        return []

    key = f"standings_{tournament_id}_{season_id}"
    cached = _ler_cache(key)
    if cached:
        return cached.get("data", [])

    path = f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
    url = f"https://api.sofascore.com/api/v1{path}"
    try:
        resp = _flaresolverr.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status != 200:
            _escrever_cache(key, {"data": []})
            return []
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("Standings fetch falhou %s: %s", key, exc)
        _escrever_cache(key, {"data": []})
        return []

    rows: list[dict] = []
    for stand in (payload.get("standings") or []):
        for row in (stand.get("rows") or []):
            team = row.get("team") or {}
            rows.append({
                "team_id": team.get("id"),
                "team_name": team.get("name"),
                "posicao": row.get("position"),
                "pontos": row.get("points"),
                "jogos": row.get("matches"),
                "vitorias": row.get("wins"),
                "derrotas": row.get("losses"),
            })

    _escrever_cache(key, {"data": rows})
    return rows


def _posicao_para(rows: list[dict], team_id: int) -> dict | None:
    for r in rows:
        if r.get("team_id") == team_id:
            return r
    return None


# ─── Classificar necessidade ──────────────────────────────────────────

def _classificar_necessidade(
    liga_nome: str, posicao: int | None, pontos: int | None,
    jogos: int | None, total_rows: int,
) -> tuple[bool, bool]:
    """Devolve (ja_qualificada, precisa_pts).

    Heuristicas simples por tipo de competicao:
      - UCL/UEL fase de grupos: top-2 = qualifica (ou top-8 na nova liguilha)
      - Liga domestica: top-4 = Champions, 15+ = perigo descida
    """
    if posicao is None:
        return False, False
    n = (liga_nome or "").lower()

    if any(x in n for x in _UCL_UEL_CONFERENCE):
        # Nova liguilha UCL (36 equipas): top-8 qualifica, 9-24 playoff, 25+ fora
        if total_rows >= 24:
            ja = posicao <= 6         # bastante confortavel
            prec = posicao >= 20      # fora da zona de playoff
            return ja, prec
        # Legado fase de grupos (4 equipas por grupo): dificil inferir sem grupo
        return False, False

    # Liga domestica: top-4 confortavel, 15+ a precisar
    if total_rows >= 16:
        ja = posicao <= 2             # muito acima da zona europa
        prec = posicao >= total_rows - 4  # na zona de descida
        return ja, prec
    return False, False


# ─── API principal ───────────────────────────────────────────────────

def obter_contexto(
    *,
    liga_nome: str,
    tournament_id: int | None = None,
    season_id: int | None = None,
    home_id: int | None = None,
    away_id: int | None = None,
    fase: str | None = None,
) -> ContextoJogo:
    """Devolve um ContextoJogo com peso + standings (quando dispon)."""
    peso, razao = _classificar_peso(liga_nome, fase)
    ctx = ContextoJogo(peso_jogo=peso, razao=razao)

    if not (tournament_id and season_id and home_id and away_id):
        return ctx

    rows = _fetch_standings(tournament_id, season_id)
    if not rows:
        return ctx

    pos_c = _posicao_para(rows, int(home_id))
    pos_f = _posicao_para(rows, int(away_id))
    if pos_c:
        ctx.posicao_casa = pos_c.get("posicao")
        ctx.pontos_casa = pos_c.get("pontos")
    if pos_f:
        ctx.posicao_fora = pos_f.get("posicao")
        ctx.pontos_fora = pos_f.get("pontos")

    total = len(rows)
    if pos_c:
        ja, prec = _classificar_necessidade(
            liga_nome, ctx.posicao_casa, ctx.pontos_casa,
            pos_c.get("jogos"), total,
        )
        ctx.casa_ja_qualificada = ja
        ctx.casa_precisa_pts = prec
    if pos_f:
        ja, prec = _classificar_necessidade(
            liga_nome, ctx.posicao_fora, ctx.pontos_fora,
            pos_f.get("jogos"), total,
        )
        ctx.fora_ja_qualificada = ja
        ctx.fora_precisa_pts = prec

    return ctx


def resumo_telegram(ctx: ContextoJogo, nome_casa: str, nome_fora: str) -> str:
    """1 linha curta com o contexto do jogo. Vazio se nao ha nada util."""
    if ctx.peso_jogo in ("baixo", "medio") and ctx.posicao_casa is None:
        return ""
    partes = []
    if ctx.peso_jogo == "muito_alto":
        partes.append(f"⚖ Peso MUITO ALTO ({ctx.razao})")
    elif ctx.peso_jogo == "alto":
        partes.append(f"⚖ Peso alto ({ctx.razao})")
    # Posicoes quando ha
    if ctx.posicao_casa and ctx.posicao_fora:
        partes.append(
            f"{nome_casa} #{ctx.posicao_casa} vs {nome_fora} #{ctx.posicao_fora}"
        )
    # Necessidades
    tags = []
    if ctx.casa_ja_qualificada:
        tags.append(f"{nome_casa} segura")
    if ctx.casa_precisa_pts:
        tags.append(f"{nome_casa} precisa pts")
    if ctx.fora_ja_qualificada:
        tags.append(f"{nome_fora} segura")
    if ctx.fora_precisa_pts:
        tags.append(f"{nome_fora} precisa pts")
    if tags:
        partes.append(" · ".join(tags))
    if not partes:
        return ""
    return " · ".join(partes)
