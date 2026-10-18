"""Historico PERMANENTE de stats por jogador por jogo (Fase 6).

Parsea /event/{id}/lineups do Sofascore e extrai stats individuais para
cada jogador titular. Grava em EstatisticaJogadorJogo (append-only,
idempotente pela chave (sofascore_player_id, sofascore_event_id)).

Fluxo:
  gravar_jogadores_terminado(event_id) chama:
    1. GET /event/{id} para data/era_casa/liga
    2. GET /event/{id}/lineups para statistics por jogador
    3. GET /event/{id}/incidents para encontrar minuto do cartao
    4. Para cada player.statistics → cria EstatisticaJogadorJogo
"""
from __future__ import annotations

import html as _html
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from apostas.ingestao.sofascore_live import _get_json
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import EstatisticaJogadorJogo

log = get_logger(__name__)


def _unescape(s: str | None) -> str | None:
    if not s:
        return None
    return _html.unescape(s)


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _posicao_curta(pos: str | None) -> str | None:
    """'G' -> 'GK', 'D' -> 'CB' default, 'M' -> 'CM', 'F' -> 'ST', etc.

    Sofascore devolve 1 letra na posicao principal: G/D/M/F.
    """
    if not pos:
        return None
    pos = pos.upper().strip()
    mapa = {
        "G": "GK",
        "D": "CB",  # ou LB/RB — sem mais info usamos CB
        "M": "CM",
        "F": "ST",
    }
    return mapa.get(pos, pos[:3])


def _extrair_minutos_cartao(incidents_json: dict | None, player_id: int) -> tuple[int | None, int | None]:
    """Devolve (minuto_amarelo, minuto_vermelho) do jogador, ou None."""
    if not incidents_json:
        return None, None
    minuto_am = None
    minuto_vm = None
    for inc in incidents_json.get("incidents") or []:
        if inc.get("incidentType") != "card":
            continue
        p = inc.get("player") or {}
        if p.get("id") != player_id:
            continue
        min_inc = inc.get("time")
        tipo = (inc.get("incidentClass") or "").lower()
        if "yellow" in tipo and minuto_am is None:
            minuto_am = _int(min_inc)
        elif "red" in tipo:
            minuto_vm = _int(min_inc)
    return minuto_am, minuto_vm


def _criar_registo_jogador(
    player: dict,
    event_id: int,
    team_id: int,
    data_jogo: datetime,
    era_casa: bool,
    liga: str | None,
    incidents_json: dict | None,
    foi_titular: bool,
) -> EstatisticaJogadorJogo | None:
    """Parse 1 jogador do payload lineups → EstatisticaJogadorJogo."""
    p = player.get("player") or player  # Sofascore embute em player.player
    pid = p.get("id")
    if not pid:
        return None

    s = player.get("statistics") or {}
    if not s and not foi_titular:
        # Nao jogou e sem stats → nao vale a pena gravar
        return None

    min_am, min_vm = _extrair_minutos_cartao(incidents_json, pid)

    return EstatisticaJogadorJogo(
        sofascore_player_id=pid,
        sofascore_event_id=event_id,
        sofascore_team_id=team_id,
        nome_jogador=_unescape(p.get("name")) or f"player_{pid}",
        posicao=_posicao_curta(p.get("position")),
        liga=liga,
        data_jogo=data_jogo,
        era_casa=1 if era_casa else 0,
        foi_titular=1 if foi_titular else 0,
        minutos=_int(s.get("minutesPlayed")) or 0,
        rating=_float(s.get("rating")),
        golos=_int(s.get("goals")),
        assistencias=_int(s.get("goalAssist")),
        remates=_int(s.get("totalScoringAttempts")) or _int(s.get("shotsTotal")),
        sot=_int(s.get("onTargetScoringAttempt")) or _int(s.get("shotsOnTarget")),
        grandes_oportunidades=_int(s.get("bigChanceCreated")),
        grandes_oportunidades_falhadas=_int(s.get("bigChanceMissed")),
        passes=_int(s.get("totalPass")),
        passes_pct=_float(s.get("accuratePassPercent")),
        passes_chave=_int(s.get("keyPass")),
        tackles=_int(s.get("totalTackle")),
        interceptacoes=_int(s.get("interceptionWon")),
        desarmes=_int(s.get("challengeLost")),
        duels_won=_int(s.get("duelWon")),
        duels_lost=_int(s.get("duelLost")),
        dribles_tentados=_int(s.get("totalDribbles")),
        dribles_sucesso=_int(s.get("wonContest")),
        fouls_committed=_int(s.get("fouls")),
        fouls_drawn=_int(s.get("wasFouled")),
        cards_amarelos=_int(s.get("yellowCards")),
        cards_vermelhos=_int(s.get("redCards")),
        minuto_amarelo=min_am,
        minuto_vermelho=min_vm,
    )


def gravar_jogadores_terminado(event_id: int) -> int:
    """Puxa /event/{id}/lineups e grava 1 linha por jogador titular+suplente usado.

    Devolve numero de linhas efectivamente adicionadas (0 se tudo ja existia).
    """
    event_json = _get_json(f"/event/{event_id}")
    if event_json is None or "event" not in event_json:
        return 0
    ev = event_json["event"]
    status = ev.get("status") or {}
    code = status.get("code") or 0
    desc_s = str(status.get("description") or "").lower()
    acabado = code in (100, 110, 120) or any(
        t in desc_s for t in ("ended", "finished", "ft", "aet", "pen")
    )
    if not acabado:
        return 0

    home = ev.get("homeTeam") or {}
    away = ev.get("awayTeam") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    if not home_id or not away_id:
        return 0

    ts = ev.get("startTimestamp")
    if ts is None:
        return 0
    data_jogo = datetime.utcfromtimestamp(ts)
    liga_raw = ((ev.get("tournament") or {}).get("name")
                or (ev.get("tournament") or {}).get("uniqueTournament", {}).get("name"))
    liga = _unescape(liga_raw)

    lineups_json = _get_json(f"/event/{event_id}/lineups")
    if lineups_json is None:
        log.debug("event %s sem lineups ainda", event_id)
        return 0

    incidents_json = _get_json(f"/event/{event_id}/incidents")

    adic = 0
    with abrir_sessao() as s:
        for lado, team_id, era_casa in (
            ("home", home_id, True), ("away", away_id, False),
        ):
            lineup = lineups_json.get(lado) or {}
            if not lineup:
                continue
            titulares = lineup.get("players") or []
            suplentes_usados = []  # alguns players vem com minutesPlayed>0 so em suplentes
            # Marca titulares (primeiros 11, geralmente foram todos titulares)
            # Sofascore nao tem flag directa; mas podemos usar "substitute": True
            for pl in titulares:
                is_sub = pl.get("substitute", False)
                foi_tit = not is_sub
                try:
                    reg = _criar_registo_jogador(
                        pl, event_id, team_id, data_jogo, era_casa, liga,
                        incidents_json, foi_tit,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("parse jogador event %s: %s", event_id, exc)
                    continue
                if reg is None:
                    continue
                # Idempotencia
                existe = s.scalar(select(EstatisticaJogadorJogo).where(
                    EstatisticaJogadorJogo.sofascore_player_id == reg.sofascore_player_id,
                    EstatisticaJogadorJogo.sofascore_event_id == reg.sofascore_event_id,
                ))
                if existe:
                    continue
                s.add(reg)
                adic += 1

    return adic


# ─────────── Leitura / perfil jogador ─────────────────────────────────────

def stats_recentes_jogador(player_id: int, n: int = 15) -> list[EstatisticaJogadorJogo]:
    """Ultimas n stats do jogador, mais recente primeiro."""
    with abrir_sessao() as s:
        rows = s.scalars(
            select(EstatisticaJogadorJogo)
            .where(EstatisticaJogadorJogo.sofascore_player_id == player_id)
            .order_by(desc(EstatisticaJogadorJogo.data_jogo))
            .limit(n)
        ).all()
        for r in rows:
            s.expunge(r)
    return list(rows)


def _por_90(valor: int | None, minutos: int | None) -> float | None:
    """Normaliza para 90 minutos."""
    if valor is None or minutos is None or minutos <= 0:
        return None
    return (valor / minutos) * 90.0


def perfil_jogador(player_id: int, n: int = 10) -> dict | None:
    """Agrega metricas do jogador dos ultimos n jogos.

    Devolve dict com:
      - nome, posicao_moda, forma (rating medio ultimos 5)
      - agressividade (fouls/90), tendencia_amarelo (cards_am/90)
      - duels_pct (duels_won / (duels_won+duels_lost))
      - drible_pct (dribles_sucesso / dribles_tentados)
      - produtividade (goals+assists / 90)
      - papel = {atacante/criador/defesa/goleiro} inferido
    """
    jogos = stats_recentes_jogador(player_id, n)
    if len(jogos) < 3:
        return None

    def _med(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    # Soma stats, normalizando por minutos
    total_min = sum(j.minutos or 0 for j in jogos)
    if total_min < 90:  # menos de 90 min de total → nao vale a pena
        return None

    # Soma total e normaliza por 90min
    soma_fouls = sum((j.fouls_committed or 0) for j in jogos)
    soma_cards_am = sum((j.cards_amarelos or 0) for j in jogos)
    soma_goals = sum((j.golos or 0) for j in jogos)
    soma_assists = sum((j.assistencias or 0) for j in jogos)
    soma_tackles = sum((j.tackles or 0) for j in jogos)
    soma_duels_won = sum((j.duels_won or 0) for j in jogos)
    soma_duels_lost = sum((j.duels_lost or 0) for j in jogos)
    soma_dribbles_t = sum((j.dribles_tentados or 0) for j in jogos)
    soma_dribbles_s = sum((j.dribles_sucesso or 0) for j in jogos)
    soma_remates = sum((j.remates or 0) for j in jogos)
    soma_sot = sum((j.sot or 0) for j in jogos)

    agressividade = (soma_fouls / total_min) * 90.0
    tendencia_am = (soma_cards_am / total_min) * 90.0
    produtividade = ((soma_goals + soma_assists) / total_min) * 90.0
    duels_total = soma_duels_won + soma_duels_lost
    duels_pct = (soma_duels_won / duels_total) if duels_total > 0 else None
    drible_pct = (soma_dribbles_s / soma_dribbles_t) if soma_dribbles_t > 0 else None

    # Posicao moda
    from collections import Counter  # noqa: PLC0415
    posicoes = [j.posicao for j in jogos if j.posicao]
    posicao_moda = Counter(posicoes).most_common(1)[0][0] if posicoes else None

    # Forma (ultimos 5 ratings)
    ratings = [j.rating for j in jogos[:5] if j.rating]
    forma = sum(ratings) / len(ratings) if ratings else None

    # Papel inferido (heuristica)
    if posicao_moda == "GK":
        papel = "Guarda-redes"
    elif posicao_moda in ("CB", "LB", "RB", "WB"):
        papel = "Defesa"
    elif posicao_moda in ("CDM", "CM"):
        papel = "Medio"
    elif posicao_moda in ("CAM", "LW", "RW"):
        papel = "Criador"
    elif posicao_moda == "ST":
        papel = "Avancado"
    else:
        papel = posicao_moda or "Desconhecido"

    return {
        "nome": jogos[0].nome_jogador,
        "player_id": player_id,
        "posicao": posicao_moda,
        "papel": papel,
        "n_jogos": len(jogos),
        "minutos_totais": total_min,
        "forma": round(forma, 2) if forma else None,
        "agressividade": round(agressividade, 2),
        "tendencia_amarelo": round(tendencia_am, 3),
        "duels_pct": round(duels_pct, 3) if duels_pct is not None else None,
        "drible_pct": round(drible_pct, 3) if drible_pct is not None else None,
        "produtividade_90": round(produtividade, 2),
        "remates_90": round((soma_remates / total_min) * 90.0, 2),
        "sot_90": round((soma_sot / total_min) * 90.0, 2),
        "tackles_90": round((soma_tackles / total_min) * 90.0, 2),
    }


def jogador_amarelado_este_jogo(player_id: int, event_id: int) -> int | None:
    """Devolve minuto do amarelo deste jogador neste jogo, ou None.

    Util no scanner live: 'jogador X amarelado minuto Y → risco 2o cartao'.
    """
    with abrir_sessao() as s:
        row = s.scalar(select(EstatisticaJogadorJogo).where(
            EstatisticaJogadorJogo.sofascore_player_id == player_id,
            EstatisticaJogadorJogo.sofascore_event_id == event_id,
        ))
        return row.minuto_amarelo if row else None
