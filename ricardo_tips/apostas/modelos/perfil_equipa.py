"""Perfil comportamental acumulado por equipa (ask especifico 2026-10-15).

Objectivo: para cada equipa que o scanner sugere, acumular padroes a
partir dos ultimos 10-15 jogos. Serve para enriquecer analise pre-match:

Ex. Juventus vs equipa fraca -> Juventus esperado perder cedo?
    - perfil Juventus: remates_atras_90 = 18, cantos_atras_90 = 7
    - sinal: ajusta λ_remates e λ_cantos para cima
    - sinal: virar_jogos_pct 45% -> nao apostar contra Juventus 1X2 live

Fluxo:
  1. scripts/atualizar_perfis_equipa.py chama atualizar_perfil(team_id)
     para equipas conhecidas nas sugestoes dos ultimos 7d.
  2. obter_perfil(team_id) le da BD (cache).
  3. Pre-live diario / scanner usa obter_perfil() para ajustar λ.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from apostas.ingestao import sofascore_contexto, sofascore_live
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import EquipaPerfil

log = get_logger(__name__)


# ─────────────────────────── Helpers ───────────────────────────────────────

def _stat_valor(stats_json: dict, grupo_nome: str, item_nome: str, lado: str) -> float | None:
    """Extrai um valor de /event/{id}/statistics do Sofascore.

    Estrutura: {"statistics": [{"period":"ALL", "groups":[{"groupName":...,
                 "statisticsItems":[{"name":..., "home":"X", "away":"Y"}]}]}]}
    lado = "home" ou "away".
    """
    try:
        for period in stats_json.get("statistics", []):
            if period.get("period") != "ALL":
                continue
            for grupo in period.get("groups", []):
                if grupo_nome.lower() not in (grupo.get("groupName") or "").lower():
                    continue
                for item in grupo.get("statisticsItems", []):
                    if item_nome.lower() not in (item.get("name") or "").lower():
                        continue
                    v = item.get(lado)
                    if v is None:
                        return None
                    # Formatos possiveis: "12", "12 (5)", "45%"
                    s = str(v).replace("%", "").split()[0]
                    try:
                        return float(s)
                    except ValueError:
                        return None
    except Exception:  # noqa: BLE001
        return None
    return None


# ─────────────────────────── Calculo perfil ────────────────────────────────

@dataclass
class _AgregadoJogo:
    """Metricas extraidas de 1 jogo do Sofascore."""
    event_id: int
    data: datetime
    minutos: int
    era_casa: bool
    golos_marcados: int
    golos_sofridos: int
    ganhou: bool
    perdeu: bool
    remates: float | None
    sot: float | None
    cantos: float | None
    cards_amarelos: float | None
    fouls: float | None


def _agregar_jogo(team_id: int, event_json: dict) -> Optional[_AgregadoJogo]:
    """A partir do payload do evento Sofascore extrai metricas."""
    ev = event_json
    status = ev.get("status") or {}
    if (status.get("code") or 0) != 100:  # 100 = terminado
        return None

    home_id = (ev.get("homeTeam") or {}).get("id")
    era_casa = home_id == team_id
    hs = (ev.get("homeScore") or {}).get("current") or 0
    aws = (ev.get("awayScore") or {}).get("current") or 0
    gm = hs if era_casa else aws
    gs = aws if era_casa else hs

    data_utc = ev.get("startTimestamp")
    data = datetime.fromtimestamp(int(data_utc)) if data_utc else datetime.utcnow()

    return _AgregadoJogo(
        event_id=int(ev.get("id") or 0),
        data=data,
        minutos=90,
        era_casa=era_casa,
        golos_marcados=int(gm),
        golos_sofridos=int(gs),
        ganhou=gm > gs,
        perdeu=gm < gs,
        remates=None,
        sot=None,
        cantos=None,
        cards_amarelos=None,
        fouls=None,
    )


def _enriquecer_com_stats(ag: _AgregadoJogo, team_id: int) -> _AgregadoJogo:
    """Chama /event/{id}/statistics e preenche remates/cantos/cartoes."""
    try:
        # Hack: reutiliza _get_json interno via sofascore_live
        from apostas.ingestao import _flaresolverr  # noqa: PLC0415
        resp = _flaresolverr.get(
            f"https://www.sofascore.com/api/v1/event/{ag.event_id}/statistics",
            headers={"Accept": "application/json",
                     "Referer": "https://www.sofascore.com/"},
            warmup_url="https://www.sofascore.com/",
        )
        if resp.status != 200:
            return ag
        stats = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("stats falhou evento %d: %s", ag.event_id, exc)
        return ag

    lado = "home" if ag.era_casa else "away"
    ag.remates = _stat_valor(stats, "shots", "total shots", lado) \
        or _stat_valor(stats, "shots", "shots", lado)
    ag.sot = _stat_valor(stats, "shots", "shots on target", lado)
    ag.cantos = _stat_valor(stats, "tv", "corner kicks", lado) \
        or _stat_valor(stats, "corners", "corner", lado)
    ag.cards_amarelos = _stat_valor(stats, "cards", "yellow", lado)
    ag.fouls = _stat_valor(stats, "fouls", "fouls", lado)
    return ag


def _mean(valores: list[float]) -> float | None:
    valores = [v for v in valores if v is not None]
    return sum(valores) / len(valores) if valores else None


def calcular_perfil(team_id: int, nome: str, liga: str | None = None,
                     n_jogos: int = 10) -> EquipaPerfil:
    """Chama Sofascore + agrega metricas comportamentais.

    Nao grava — apenas devolve objecto EquipaPerfil preenchido.
    """
    # Lista ultimos jogos via endpoint que ja sabemos funcionar
    try:
        from apostas.ingestao import _flaresolverr  # noqa: PLC0415
        resp = _flaresolverr.get(
            f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0",
            headers={"Accept": "application/json",
                     "Referer": "https://www.sofascore.com/"},
            warmup_url="https://www.sofascore.com/",
        )
        if resp.status != 200:
            return _perfil_vazio(team_id, nome, liga)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("perfil %d: /team/.../last/0 falhou: %s", team_id, exc)
        return _perfil_vazio(team_id, nome, liga)

    events = (data.get("events") or [])[:n_jogos]
    if not events:
        return _perfil_vazio(team_id, nome, liga)

    agregados = []
    for ev in events:
        ag = _agregar_jogo(team_id, ev)
        if not ag:
            continue
        # Enriquece com stats (uma chamada por jogo)
        ag = _enriquecer_com_stats(ag, team_id)
        agregados.append(ag)

    if not agregados:
        return _perfil_vazio(team_id, nome, liga)

    # Baseline
    gm_media = _mean([a.golos_marcados for a in agregados])
    gs_media = _mean([a.golos_sofridos for a in agregados])
    remates_media = _mean([a.remates for a in agregados if a.remates])
    sot_media = _mean([a.sot for a in agregados if a.sot])
    cantos_media = _mean([a.cantos for a in agregados if a.cantos])
    cards_media = _mean([a.cards_amarelos for a in agregados if a.cards_amarelos])
    fouls_media = _mean([a.fouls for a in agregados if a.fouls])

    # Comportamento quando perde — subset dos jogos onde perdeu
    perdeu = [a for a in agregados if a.perdeu]
    remates_atras = _mean([a.remates for a in perdeu if a.remates])
    sot_atras = _mean([a.sot for a in perdeu if a.sot])
    cantos_atras = _mean([a.cantos for a in perdeu if a.cantos])
    golos_em_atraso = _mean([a.golos_marcados for a in perdeu])

    # Virar jogos: % onde comecou a perder (golos sofridos > 0) e acabou
    # ganhando. Aproximacao: ganhou + golos_sofridos > 0 dividido por ganhou+empatou
    virou = sum(1 for a in agregados if a.ganhou and a.golos_sofridos > 0)
    denom = sum(1 for a in agregados if (a.ganhou or (not a.ganhou and not a.perdeu)))
    virar_pct = (virou / denom) if denom > 0 else None

    # Carga de jogos
    hoje = datetime.utcnow()
    carga_7d = sum(1 for a in agregados if (hoje - a.data).days <= 7)
    carga_14d = sum(1 for a in agregados if (hoje - a.data).days <= 14)

    return EquipaPerfil(
        sofascore_team_id=team_id,
        nome=nome,
        liga=liga,
        remates_atras_90=remates_atras,
        sot_atras_90=sot_atras,
        cantos_atras_90=cantos_atras,
        golos_em_atraso_media=golos_em_atraso,
        virar_jogos_pct=virar_pct,
        remates_media_90=remates_media,
        sot_media_90=sot_media,
        cantos_media_90=cantos_media,
        cards_media_90=cards_media,
        fouls_media_90=fouls_media,
        golos_marcados_90=gm_media,
        golos_sofridos_90=gs_media,
        carga_jogos_7d=carga_7d,
        carga_jogos_14d=carga_14d,
        viagens_longas_14d=0,  # TODO: inferir de ligas internacionais
        n_jogos_analisados=len(agregados),
        data_atualizacao=datetime.utcnow(),
    )


def _perfil_vazio(team_id: int, nome: str, liga: str | None) -> EquipaPerfil:
    return EquipaPerfil(
        sofascore_team_id=team_id, nome=nome, liga=liga,
        n_jogos_analisados=0, data_atualizacao=datetime.utcnow(),
    )


# ─────────── Calculo via historico persistente ────────────────────────────

def calcular_perfil_do_historico(
    team_id: int,
    nome: str,
    liga: str | None = None,
    n_jogos: int = 20,
) -> EquipaPerfil | None:
    """Calcula perfil a partir de EstatisticaEquipaJogo (historico persistente).

    Preferido sobre calcular_perfil() quando ja ha dados: nao faz scrape,
    tem stats por parte (1T/2T), e tem contexto (W/D/L por jogo).

    Devolve None se nao houver dados minimos (<3 jogos) para esta equipa —
    o chamador deve nesse caso cair para calcular_perfil() (scrape live).
    """
    from apostas.modelos import historico_equipas  # noqa: PLC0415

    jogos = historico_equipas.stats_recentes_equipa(team_id, n_jogos)
    if len(jogos) < 3:
        return None

    def _mn(vs):
        vs = [v for v in vs if v is not None]
        return sum(vs) / len(vs) if vs else None

    # Baseline global
    gm_media = _mn([j.golos_marcados for j in jogos])
    gs_media = _mn([j.golos_sofridos for j in jogos])
    remates_media = _mn([j.remates for j in jogos])
    sot_media = _mn([j.sot for j in jogos])
    cantos_media = _mn([j.cantos for j in jogos])
    cards_media = _mn([j.cards_amarelos for j in jogos])
    fouls_media = _mn([j.fouls for j in jogos])

    # Quando perde (contexto: resultado == 'L')
    perdeu = [j for j in jogos if j.resultado == "L"]
    remates_atras = _mn([j.remates for j in perdeu])
    sot_atras = _mn([j.sot for j in perdeu])
    cantos_atras = _mn([j.cantos for j in perdeu])
    golos_em_atraso = _mn([j.golos_marcados for j in perdeu])

    # Virar jogos: ganhou (resultado W) mas estava a perder/empatar ao
    # intervalo (resultado_1t em L/D)
    virou = sum(
        1 for j in jogos
        if j.resultado == "W" and j.resultado_1t in ("L", "D")
    )
    denom = sum(1 for j in jogos if j.resultado_1t in ("L", "D"))
    virar_pct = (virou / denom) if denom > 0 else None

    # Por parte: media de remates/cantos 1T vs 2T
    rem_1t = _mn([j.remates_1t for j in jogos])
    rem_2t = _mn([j.remates_2t for j in jogos])
    can_1t = _mn([j.cantos_1t for j in jogos])
    can_2t = _mn([j.cantos_2t for j in jogos])

    # Carga semanal
    hoje = datetime.utcnow()
    carga_7d = sum(1 for j in jogos if (hoje - j.data_jogo).days <= 7)
    carga_14d = sum(1 for j in jogos if (hoje - j.data_jogo).days <= 14)

    # Notas: resumo texto (padroes nao-numericos)
    notas_bits = []
    if virar_pct is not None and virar_pct >= 0.30:
        notas_bits.append(f"virar jogos {virar_pct*100:.0f}%")
    if remates_atras and remates_media and remates_atras > remates_media * 1.15:
        delta = (remates_atras / remates_media - 1) * 100
        notas_bits.append(f"a perder: +{delta:.0f}% remates")
    if rem_2t and rem_1t and rem_2t > rem_1t * 1.1:
        notas_bits.append("2T > 1T remates")
    notas_str = "; ".join(notas_bits) if notas_bits else None

    return EquipaPerfil(
        sofascore_team_id=team_id,
        nome=nome,
        liga=liga,
        remates_atras_90=remates_atras,
        sot_atras_90=sot_atras,
        cantos_atras_90=cantos_atras,
        golos_em_atraso_media=golos_em_atraso,
        virar_jogos_pct=virar_pct,
        remates_1a_parte_media=rem_1t,
        remates_2a_parte_media=rem_2t,
        cantos_1a_parte_media=can_1t,
        cantos_2a_parte_media=can_2t,
        remates_media_90=remates_media,
        sot_media_90=sot_media,
        cantos_media_90=cantos_media,
        cards_media_90=cards_media,
        fouls_media_90=fouls_media,
        golos_marcados_90=gm_media,
        golos_sofridos_90=gs_media,
        carga_jogos_7d=carga_7d,
        carga_jogos_14d=carga_14d,
        viagens_longas_14d=0,
        n_jogos_analisados=len(jogos),
        data_atualizacao=datetime.utcnow(),
        notas=notas_str,
    )


# ─────────────────────────── BD interface ──────────────────────────────────

def obter_perfil(team_id: int) -> EquipaPerfil | None:
    with abrir_sessao() as s:
        return s.scalars(
            select(EquipaPerfil).where(EquipaPerfil.sofascore_team_id == team_id)
        ).first()


def atualizar_perfil(team_id: int, nome: str, liga: str | None = None,
                      ttl_horas: int = 24) -> EquipaPerfil:
    """Upsert do perfil. So recalcula se cache > ttl_horas.

    Prefere calcular_perfil_do_historico() (append-only tabela nova).
    Fallback a calcular_perfil() (scrape live) se historico insuficiente.
    """
    perfil_atual = obter_perfil(team_id)
    if perfil_atual:
        idade = datetime.utcnow() - perfil_atual.data_atualizacao
        if idade < timedelta(hours=ttl_horas):
            return perfil_atual

    # 1a tentativa: historico persistente (sem scrape)
    novo = calcular_perfil_do_historico(team_id, nome, liga)
    if novo is None:
        # Fallback: scrape live (menos preciso, sem split 1T/2T)
        novo = calcular_perfil(team_id, nome, liga)
    with abrir_sessao() as s:
        existente = s.scalars(
            select(EquipaPerfil).where(EquipaPerfil.sofascore_team_id == team_id)
        ).first()
        if existente:
            for campo in (
                "nome", "liga", "remates_atras_90", "sot_atras_90",
                "cantos_atras_90", "golos_em_atraso_media", "virar_jogos_pct",
                "remates_1a_parte_media", "remates_2a_parte_media",
                "cantos_1a_parte_media", "cantos_2a_parte_media",
                "remates_media_90", "sot_media_90", "cantos_media_90",
                "cards_media_90", "fouls_media_90", "golos_marcados_90",
                "golos_sofridos_90", "carga_jogos_7d", "carga_jogos_14d",
                "viagens_longas_14d", "n_jogos_analisados", "data_atualizacao",
                "notas",
            ):
                setattr(existente, campo, getattr(novo, campo))
            s.commit()
            s.refresh(existente)
            return existente
        s.add(novo)
        s.commit()
        s.refresh(novo)
        return novo
