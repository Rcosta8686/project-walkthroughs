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

    grupo_match: substring (case-insensitive) do groupName. Se vazio ""
      ou None, procura em TODOS os grupos (Sofascore muda nomes com o tempo).
    item_match: substring do item.name (sempre obrigatorio)
    lado: 'home' ou 'away'
    """
    for grupo in period_json.get("groups", []):
        gn = (grupo.get("groupName") or "").lower()
        if grupo_match and grupo_match.lower() not in gn:
            continue
        for item in grupo.get("statisticsItems", []):
            inm = (item.get("name") or "").lower()
            if item_match.lower() not in inm:
                continue
            return _parse_valor(item.get(lado))
    return None


def _item_any_group(period_json: dict, *item_matches: str, lado: str) -> float | None:
    """Procura item.name que corresponda a QUALQUER um dos item_matches
    em QUALQUER grupo. Case-insensitive substring match.
    """
    for grupo in period_json.get("groups", []):
        for item in grupo.get("statisticsItems", []):
            inm = (item.get("name") or "").lower()
            for match in item_matches:
                if match.lower() in inm:
                    v = _parse_valor(item.get(lado))
                    if v is not None:
                        return v
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
    """lado = 'home' ou 'away'. Devolve _StatsEquipa (campos opcionais).

    Procura cada metrica em QUALQUER grupo — Sofascore muda nomes de
    grupos ao longo do tempo (ex: Attack -> Match overview -> Set pieces).
    """
    stats = _StatsEquipa()
    for periodo_name, atrib_sufixo in (
        ("ALL", ""), ("1ST", "_1t"), ("2ND", "_2t")
    ):
        p = _periodo(stats_json, periodo_name)
        if not p:
            continue
        # Remates
        v = _item_any_group(p, "Total shots", "Shots", lado=lado)
        setattr(stats, f"remates{atrib_sufixo}", v)
        # SOT
        v = _item_any_group(p, "Shots on target", "On target", lado=lado)
        setattr(stats, f"sot{atrib_sufixo}", v)
        # Cantos
        v = _item_any_group(p, "Corner kicks", "Corners", lado=lado)
        setattr(stats, f"cantos{atrib_sufixo}", v)
        # Amarelos
        v = _item_any_group(p, "Yellow cards", "Yellow", lado=lado)
        setattr(stats, f"cards_am{atrib_sufixo}", v)
        # Faltas
        v = _item_any_group(p, "Fouls", lado=lado)
        setattr(stats, f"fouls{atrib_sufixo}", v)

    # Campos apenas no total (ALL)
    p_all = _periodo(stats_json, "ALL")
    if p_all:
        stats.cards_vm = _item_any_group(p_all, "Red cards", "Red", lado=lado)
        stats.posse = _item_any_group(p_all, "Ball possession", "Possession", lado=lado)
        stats.passes = _item_any_group(p_all, "Passes", lado=lado)
        stats.passes_pct = _item_any_group(p_all, "Accurate passes", lado=lado)
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
