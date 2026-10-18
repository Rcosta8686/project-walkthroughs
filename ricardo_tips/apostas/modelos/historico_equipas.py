"""Historico persistente de stats por equipa, com split 1T/2T.

Funcoes principais:
  - gravar_jogo_terminado(event_id): puxa /event + /event/statistics do
    Sofascore, extrai stats para HOME e AWAY (por parte quando disponivel),
    calcula dias desde ultimo jogo e grava 2 linhas em EstatisticaEquipaJogo.
  - stats_recentes_equipa(sofascore_team_id, n): le as ultimas n linhas.
  - stats_por_contexto(sofascore_team_id, resultado): filtra por W/D/L.

A tabela e append-only. Chave unica (sofascore_team_id, sofascore_event_id)
impede duplicados — chamar varias vezes para o mesmo jogo e seguro (no-op).
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select, desc

from apostas.ingestao import sofascore_live
from apostas.ingestao.sofascore_live import _get_json
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import EstatisticaEquipaJogo


def _unescape(s: str | None) -> str:
    """Decode entidades HTML em nomes vindos da Sofascore ('&amp;' -> '&')."""
    if not s:
        return "?"
    return _html.unescape(s)

log = get_logger(__name__)


# ─────────── Extracao de stats do payload /statistics ─────────────────────

def _parse_valor(v) -> float | None:
    """Extrai float de '12', '12 (5)', '45%', '12/15 (80%)'."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("%", "").split("(")[0].split("/")[0].strip()
    try:
        return float(s)
    except ValueError:
        return None


def _item(period_json: dict, grupo_match: str, item_match: str, lado: str) -> float | None:
    """Procura 1 item nas statisticsItems do periodo dado.

    grupo_match: substring (case-insensitive) do groupName
    item_match: substring do item.name
    lado: 'home' ou 'away'
    """
    for grupo in period_json.get("groups", []):
        gn = (grupo.get("groupName") or "").lower()
        if grupo_match.lower() not in gn:
            continue
        for item in grupo.get("statisticsItems", []):
            inm = (item.get("name") or "").lower()
            if item_match.lower() not in inm:
                continue
            return _parse_valor(item.get(lado))
    return None


def _periodo(stats_json: dict, period_name: str) -> dict | None:
    """Devolve o bloco do periodo pedido ('ALL', '1ST', '2ND') ou None."""
    for p in stats_json.get("statistics", []):
        if p.get("period", "").upper() == period_name:
            return p
    return None


@dataclass
class _StatsEquipa:
    """Snapshot de stats para 1 equipa de 1 jogo."""
    remates: float | None = None
    sot: float | None = None
    cantos: float | None = None
    cards_am: float | None = None
    cards_vm: float | None = None
    fouls: float | None = None
    posse: float | None = None
    passes: float | None = None
    passes_pct: float | None = None
    remates_1t: float | None = None
    remates_2t: float | None = None
    sot_1t: float | None = None
    sot_2t: float | None = None
    cantos_1t: float | None = None
    cantos_2t: float | None = None
    cards_am_1t: float | None = None
    cards_am_2t: float | None = None
    fouls_1t: float | None = None
    fouls_2t: float | None = None


def _extrair_stats_equipa(stats_json: dict, lado: str) -> _StatsEquipa:
    """lado = 'home' ou 'away'. Devolve _StatsEquipa (campos opcionais)."""
    stats = _StatsEquipa()
    p_all = _periodo(stats_json, "ALL")
    if p_all:
        stats.remates = _item(p_all, "shots", "Total shots", lado) or \
            _item(p_all, "shots", "shots", lado)
        stats.sot = _item(p_all, "shots", "Shots on target", lado)
        stats.cantos = _item(p_all, "attack", "Corner kicks", lado) or \
            _item(p_all, "set pieces", "Corner", lado)
        stats.cards_am = _item(p_all, "cards", "Yellow", lado)
        stats.cards_vm = _item(p_all, "cards", "Red", lado)
        stats.fouls = _item(p_all, "foul", "Fouls", lado)
        stats.posse = _item(p_all, "possession", "Ball possession", lado)
        stats.passes = _item(p_all, "passes", "Passes", lado)
        stats.passes_pct = _item(p_all, "passes", "Accurate passes", lado)
    p_1t = _periodo(stats_json, "1ST")
    if p_1t:
        stats.remates_1t = _item(p_1t, "shots", "Total shots", lado) or \
            _item(p_1t, "shots", "shots", lado)
        stats.sot_1t = _item(p_1t, "shots", "Shots on target", lado)
        stats.cantos_1t = _item(p_1t, "attack", "Corner kicks", lado) or \
            _item(p_1t, "set pieces", "Corner", lado)
        stats.cards_am_1t = _item(p_1t, "cards", "Yellow", lado)
        stats.fouls_1t = _item(p_1t, "foul", "Fouls", lado)
    p_2t = _periodo(stats_json, "2ND")
    if p_2t:
        stats.remates_2t = _item(p_2t, "shots", "Total shots", lado) or \
            _item(p_2t, "shots", "shots", lado)
        stats.sot_2t = _item(p_2t, "shots", "Shots on target", lado)
        stats.cantos_2t = _item(p_2t, "attack", "Corner kicks", lado) or \
            _item(p_2t, "set pieces", "Corner", lado)
        stats.cards_am_2t = _item(p_2t, "cards", "Yellow", lado)
        stats.fouls_2t = _item(p_2t, "foul", "Fouls", lado)
    return stats


# ─────────── Dias desde ultimo jogo (fadiga) ──────────────────────────────

def _dias_desde_ultimo(team_id: int, data_actual: datetime) -> int | None:
    """Procura o jogo anterior na tabela e devolve dias de diferenca."""
    try:
        with abrir_sessao() as s:
            ult = s.scalar(
                select(EstatisticaEquipaJogo)
                .where(
                    EstatisticaEquipaJogo.sofascore_team_id == team_id,
                    EstatisticaEquipaJogo.data_jogo < data_actual,
                )
                .order_by(desc(EstatisticaEquipaJogo.data_jogo))
                .limit(1)
            )
            if ult is None:
                return None
            delta = data_actual - ult.data_jogo
            return max(0, delta.days)
    except Exception:  # noqa: BLE001
        return None


# ─────────── Entrada principal ─────────────────────────────────────────────

def _resultado_wdl(gm: int, gs: int) -> str:
    if gm > gs:
        return "W"
    if gm < gs:
        return "L"
    return "D"


def gravar_jogo_terminado(event_id: int) -> int:
    """Puxa stats do jogo (ja terminado) e grava 2 linhas (casa + fora).

    Devolve numero de linhas efectivamente adicionadas (0 se ja existia).
    Idempotente: UniqueConstraint impede duplicados.
    """
    event_json = _get_json(f"/event/{event_id}")
    if event_json is None or "event" not in event_json:
        return 0
    ev = event_json["event"]
    status = ev.get("status") or {}
    code = status.get("code") or 0
    # Codigos terminais Sofascore: 100=FT, 110=AET, 120=PEN. Tambem
    # aceita descricao textual "ended"/"finished"/"ft" como fallback.
    desc = str(status.get("description") or status.get("type") or "").lower()
    terminais_desc = ("ended", "finished", "ft", "aet", "pen", "full time")
    acabado = code in (100, 110, 120) or any(t in desc for t in terminais_desc)
    if not acabado:
        log.debug("event %s nao terminado (code=%s desc=%s)", event_id, code, desc)
        return 0

    stats_json = _get_json(f"/event/{event_id}/statistics") or {}

    home = ev.get("homeTeam") or {}
    away = ev.get("awayTeam") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    if not home_id or not away_id:
        return 0

    hs = (ev.get("homeScore") or {}).get("current") or 0
    aws = (ev.get("awayScore") or {}).get("current") or 0
    hs_1t = (ev.get("homeScore") or {}).get("period1")
    aws_1t = (ev.get("awayScore") or {}).get("period1")

    ts = ev.get("startTimestamp")
    if ts is None:
        return 0
    data_jogo = datetime.utcfromtimestamp(ts)

    liga_raw = ((ev.get("tournament") or {}).get("name")
                or (ev.get("tournament") or {}).get("uniqueTournament", {}).get("name"))
    liga = _unescape(liga_raw) if liga_raw else None

    stats_h = _extrair_stats_equipa(stats_json, "home")
    stats_a = _extrair_stats_equipa(stats_json, "away")

    dias_h = _dias_desde_ultimo(home_id, data_jogo)
    dias_a = _dias_desde_ultimo(away_id, data_jogo)

    def _linha(team_id, team_name, era_casa, gm, gs, gm_1t, gs_1t,
               stats, dias):
        res = _resultado_wdl(gm, gs)
        res_1t = _resultado_wdl(gm_1t, gs_1t) if (gm_1t is not None and gs_1t is not None) else None
        return EstatisticaEquipaJogo(
            sofascore_team_id=team_id,
            sofascore_event_id=event_id,
            nome_equipa=team_name,
            liga=liga,
            data_jogo=data_jogo,
            era_casa=1 if era_casa else 0,
            golos_marcados=gm,
            golos_sofridos=gs,
            golos_marcados_1t=gm_1t,
            golos_sofridos_1t=gs_1t,
            resultado=res,
            resultado_1t=res_1t,
            remates=stats.remates,
            sot=stats.sot,
            cantos=stats.cantos,
            cards_amarelos=stats.cards_am,
            cards_vermelhos=stats.cards_vm,
            fouls=stats.fouls,
            posse_pct=stats.posse,
            passes=stats.passes,
            passes_pct=stats.passes_pct,
            remates_1t=stats.remates_1t,
            remates_2t=stats.remates_2t,
            sot_1t=stats.sot_1t,
            sot_2t=stats.sot_2t,
            cantos_1t=stats.cantos_1t,
            cantos_2t=stats.cantos_2t,
            cards_amarelos_1t=stats.cards_am_1t,
            cards_amarelos_2t=stats.cards_am_2t,
            fouls_1t=stats.fouls_1t,
            fouls_2t=stats.fouls_2t,
            dias_desde_jogo_anterior=dias,
        )

    adic = 0
    with abrir_sessao() as s:
        for team_id, team_name, era_casa, gm, gs, gm_1t, gs_1t, stats, dias in [
            (home_id, _unescape(home.get("name")), True, hs, aws, hs_1t, aws_1t, stats_h, dias_h),
            (away_id, _unescape(away.get("name")), False, aws, hs, aws_1t, hs_1t, stats_a, dias_a),
        ]:
            existe = s.scalar(select(EstatisticaEquipaJogo).where(
                EstatisticaEquipaJogo.sofascore_team_id == team_id,
                EstatisticaEquipaJogo.sofascore_event_id == event_id,
            ))
            if existe:
                continue
            s.add(_linha(team_id, team_name, era_casa, gm, gs, gm_1t, gs_1t, stats, dias))
            adic += 1
    return adic


# ─────────── Leitura ─────────────────────────────────────────────────────

def stats_recentes_equipa(team_id: int, n: int = 20) -> list[EstatisticaEquipaJogo]:
    """Devolve as n stats mais recentes de uma equipa (mais recente primeiro)."""
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo)
            .where(EstatisticaEquipaJogo.sofascore_team_id == team_id)
            .order_by(desc(EstatisticaEquipaJogo.data_jogo))
            .limit(n)
        ).all()
        for r in rows:
            s.expunge(r)
    return list(rows)


def stats_por_contexto(team_id: int, resultado: str) -> list[EstatisticaEquipaJogo]:
    """Filtra por resultado W/D/L (do ponto de vista da equipa)."""
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaEquipaJogo)
            .where(
                EstatisticaEquipaJogo.sofascore_team_id == team_id,
                EstatisticaEquipaJogo.resultado == resultado,
            )
            .order_by(desc(EstatisticaEquipaJogo.data_jogo))
        ).all()
        for r in rows:
            s.expunge(r)
    return list(rows)
