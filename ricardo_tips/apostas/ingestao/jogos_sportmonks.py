"""Ingestão de fixtures (jogos) + estatísticas via Sportmonks.

Para cada liga do config, descobre as épocas desejadas (atual + N anteriores),
puxa todos os fixtures da época com include=statistics,scores,participants, e
grava-os nas tabelas `jogos` e `estatisticas_jogo`.

Idempotente: usa `id_externo` (= fixture id da Sportmonks) para evitar duplicados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from apostas.ingestao._mocks_sm import (
    STAT_CORNERS,
    STAT_FOULS,
    STAT_POSSESSION,
    STAT_RED_CARDS,
    STAT_SHOTS_ON_TARGET,
    STAT_SHOTS_TOTAL,
    STAT_YELLOW_CARDS,
)
from apostas.ingestao.sportmonks import ClienteSportmonks, cliente
from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, EstatisticasJogo, Jogo, Liga

log = get_logger(__name__)


@dataclass
class ResultadoIngestaoJogos:
    jogos_criados: int = 0
    jogos_existentes: int = 0
    jogos_atualizados: int = 0
    stats_criados: int = 0
    equipas_em_falta: int = 0
    erros: list[str] = field(default_factory=list)


def sincronizar(
    epocas_atras: int = 2,
    cli: ClienteSportmonks | None = None,
    com_stats: bool = True,
) -> ResultadoIngestaoJogos:
    """Para cada liga, puxa as últimas ``epocas_atras`` épocas de fixtures + stats."""
    cli = cli or cliente()
    cfg = load_config()
    resultado = ResultadoIngestaoJogos()

    with abrir_sessao() as s:
        for liga_cfg in cfg["ligas"]:
            sm_id = int(liga_cfg["sportmonks_id"])
            liga = s.scalar(select(Liga).where(Liga.sportmonks_id == sm_id))
            if liga is None:
                resultado.erros.append(
                    f"Liga '{liga_cfg['nome']}' não existe na BD — correr ingestão de ligas primeiro."
                )
                continue

            try:
                season_ids = _descobrir_season_ids(cli, sm_id, epocas_atras)
            except Exception as exc:  # noqa: BLE001
                resultado.erros.append(f"{liga_cfg['nome']} épocas: {exc}")
                continue

            for season_id, ano_inicio in season_ids:
                try:
                    _sincronizar_epoca(s, cli, liga, season_id, ano_inicio, com_stats, resultado)
                except Exception as exc:  # noqa: BLE001
                    resultado.erros.append(
                        f"{liga_cfg['nome']} época {ano_inicio}: {exc}"
                    )

    log.info(
        "Sportmonks jogos: %d novos, %d já existentes, %d stats criadas, %d erros",
        resultado.jogos_criados,
        resultado.jogos_existentes,
        resultado.stats_criados,
        len(resultado.erros),
    )
    return resultado


def _descobrir_season_ids(
    cli: ClienteSportmonks, liga_sm_id: int, n_epocas: int
) -> list[tuple[int, int]]:
    """Devolve lista (season_id, ano_inicio) das últimas N épocas, mais recente primeiro."""
    resp = cli.get(f"/leagues/{liga_sm_id}", params={"include": "seasons"})
    seasons = (resp.get("data") or {}).get("seasons") or []
    ordenadas = sorted(
        seasons,
        key=lambda s: s.get("starting_at") or "",
        reverse=True,
    )[:n_epocas]
    out: list[tuple[int, int]] = []
    for s in ordenadas:
        starting_at = s.get("starting_at") or ""
        ano = int(starting_at[:4]) if starting_at[:4].isdigit() else 2024
        out.append((int(s["id"]), ano))
    return out


def _sincronizar_epoca(
    s,
    cli: ClienteSportmonks,
    liga: Liga,
    season_id: int,
    ano_inicio: int,
    com_stats: bool,
    resultado: ResultadoIngestaoJogos,
) -> None:
    include = "participants;scores"
    if com_stats:
        include += ";statistics"

    resp = cli.get(f"/fixtures/seasons/{season_id}", params={"include": include})
    fixtures = resp.get("data", [])

    for fixture in fixtures:
        _processar_fixture(s, fixture, liga, ano_inicio, com_stats, resultado)


def _processar_fixture(
    s, fixture: dict, liga: Liga, ano_inicio: int,
    com_stats: bool, resultado: ResultadoIngestaoJogos,
) -> None:
    fid = int(fixture["id"])
    casa_id_ext, fora_id_ext = _ids_participantes(fixture)
    if casa_id_ext is None or fora_id_ext is None:
        resultado.erros.append(f"Fixture {fid} sem participants válidos")
        return

    casa = s.scalar(select(Equipa).where(Equipa.sportmonks_id == casa_id_ext))
    fora = s.scalar(select(Equipa).where(Equipa.sportmonks_id == fora_id_ext))
    if casa is None or fora is None:
        resultado.equipas_em_falta += 1
        return

    golos_c, golos_f = _golos(fixture)
    kickoff = _parse_data(fixture.get("starting_at"))
    estado = _estado(fixture.get("state_id"))

    existente = s.scalar(select(Jogo).where(Jogo.id_externo == fid))
    if existente is not None:
        resultado.jogos_existentes += 1
        jogo = existente
    else:
        jogo = Jogo(
            id_externo=fid,
            liga_id=liga.id,
            epoca=ano_inicio,
            data_utc=kickoff,
            casa_id=casa.id,
            fora_id=fora.id,
            golos_casa=golos_c,
            golos_fora=golos_f,
            estado=estado,
        )
        s.add(jogo)
        s.flush()
        resultado.jogos_criados += 1

    if com_stats and "statistics" in fixture:
        _upsert_stats(s, jogo, fixture["statistics"], casa.id, fora.id, resultado)


def _ids_participantes(fixture: dict) -> tuple[int | None, int | None]:
    casa = fora = None
    for p in fixture.get("participants", []):
        meta = p.get("meta") or {}
        if meta.get("location") == "home":
            casa = int(p["id"])
        elif meta.get("location") == "away":
            fora = int(p["id"])
    return casa, fora


def _golos(fixture: dict) -> tuple[int | None, int | None]:
    gc = gf = None
    for sc in fixture.get("scores", []):
        if sc.get("description") != "CURRENT":
            continue
        s = sc.get("score") or {}
        if s.get("participant") == "home":
            gc = int(s.get("goals") or 0)
        elif s.get("participant") == "away":
            gf = int(s.get("goals") or 0)
    return gc, gf


def _parse_data(s: str | None) -> datetime:
    if not s:
        return datetime.utcnow()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def _estado(state_id: int | None) -> str:
    # IDs da Sportmonks: 1=NS, 3=INPLAY, 5=FT, 6=HT, outros = raros
    return {
        1: "agendado", 3: "em_curso", 5: "terminado", 6: "em_curso",
    }.get(int(state_id or 0), "agendado")


# ─── Estatísticas ────────────────────────────────────────────────────

_TIPO_PARA_CAMPO = {
    STAT_SHOTS_TOTAL: "remates",
    STAT_SHOTS_ON_TARGET: "remates_a_baliza",
    STAT_CORNERS: "cantos",
    STAT_POSSESSION: "posse_bola",
    STAT_FOULS: "faltas",
    STAT_YELLOW_CARDS: "cartoes_amarelos",
    STAT_RED_CARDS: "cartoes_vermelhos",
}


def _upsert_stats(
    s, jogo: Jogo, stats_list: list[dict],
    casa_db_id: int, fora_db_id: int,
    resultado: ResultadoIngestaoJogos,
) -> None:
    # Mapeia participant_id → id_bd
    valores_casa: dict[str, int | float] = {}
    valores_fora: dict[str, int | float] = {}
    participant_para_db = {jogo.casa_id: casa_db_id, jogo.fora_id: fora_db_id}

    # Primeiro passo: identifica id Sportmonks correspondente a cada equipa
    casa_equipa_sm = s.scalar(select(Equipa).where(Equipa.id == jogo.casa_id))
    fora_equipa_sm = s.scalar(select(Equipa).where(Equipa.id == jogo.fora_id))
    casa_sm_id = casa_equipa_sm.sportmonks_id if casa_equipa_sm else None
    fora_sm_id = fora_equipa_sm.sportmonks_id if fora_equipa_sm else None

    for stat in stats_list:
        campo = _TIPO_PARA_CAMPO.get(int(stat.get("type_id", 0)))
        if campo is None:
            continue
        pid = int(stat.get("participant_id") or 0)
        valor = (stat.get("data") or {}).get("value")
        if valor is None:
            continue
        if pid == casa_sm_id:
            valores_casa[campo] = valor
        elif pid == fora_sm_id:
            valores_fora[campo] = valor

    for equipa_db_id, valores in ((casa_db_id, valores_casa), (fora_db_id, valores_fora)):
        if not valores:
            continue
        existente = s.scalar(
            select(EstatisticasJogo).where(
                EstatisticasJogo.jogo_id == jogo.id,
                EstatisticasJogo.equipa_id == equipa_db_id,
            )
        )
        if existente is None:
            s.add(EstatisticasJogo(jogo_id=jogo.id, equipa_id=equipa_db_id, **valores))
            resultado.stats_criados += 1
        else:
            for k, v in valores.items():
                setattr(existente, k, v)
