"""Incremento diario dos mini-cerebros: nenhum jogo terminado fica de fora.

O resolver so grava estatisticas dos jogos que o scanner viu (alertas ou
projeccoes) e o popular_cerebros so corre ao domingo. Este passo, todas as
noites, percorre as ligas que o scanner segue, apanha os jogos terminados dos
ultimos dias e grava o que faltar:
  - EstatisticaEquipaJogo (2 linhas por jogo: alimenta as fichas, que usam os
    ultimos 20 jogos de cada equipa);
  - EstatisticaJogadorJogo (titulares/suplentes: detecao de rotacao).
Jogos que ja estao completos na BD sao saltados sem pedidos ao Sofascore.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from apostas.utils.logger import get_logger

log = get_logger(__name__)

_TERMINADO = (100, 110, 120)  # FT, AET, PEN
_PAUSA_S = 0.3


def ligas_alvo() -> dict[int, str]:
    """id Sofascore -> nome: as ligas do scanner + as prioritarias."""
    from apostas.dados_referencia.ligas_prioritarias import LIGAS_PRIORITARIAS
    from apostas.ingestao.sofascore_live import LIGAS_SOFASCORE_IDS
    alvo = {int(tid): nome for nome, tid in LIGAS_SOFASCORE_IDS.items()}
    for l in LIGAS_PRIORITARIAS:
        alvo.setdefault(int(l.sofascore_id), l.nome)
    return alvo


def _get_json(path: str):
    from apostas.ingestao.sofascore_live import _get_json as get
    return get(path)


def _season_actual(tid: int) -> int | None:
    data = _get_json(f"/unique-tournament/{tid}/seasons")
    seasons = (data or {}).get("seasons") or []
    return seasons[0].get("id") if seasons else None


def _terminado(ev: dict) -> bool:
    status = ev.get("status") or {}
    desc = str(status.get("description") or status.get("type") or "").lower()
    return (status.get("code") or 0) in _TERMINADO or any(
        t in desc for t in ("ended", "finished", "aet", "pen"))


def eventos_terminados(tid: int, desde_ts: int, max_pags: int = 3) -> list[int]:
    """Jogos terminados da epoca corrente com inicio >= desde_ts."""
    season = _season_actual(tid)
    if season is None:
        return []
    ids: list[int] = []
    for pag in range(max_pags):
        data = _get_json(f"/unique-tournament/{tid}/season/{season}/events/last/{pag}")
        eventos = (data or {}).get("events") or []
        if not eventos:
            break
        recentes = [ev for ev in eventos if (ev.get("startTimestamp") or 0) >= desde_ts]
        ids += [int(ev["id"]) for ev in recentes if ev.get("id") and _terminado(ev)]
        if len(recentes) < len(eventos):  # a pagina ja chega a jogos antigos
            break
        time.sleep(_PAUSA_S)
    return ids


def ja_guardados(sess, ids: list[int]) -> tuple[set[int], set[int]]:
    """(jogos com as 2 equipas guardadas, jogos com jogadores guardados)."""
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    if not ids:
        return set(), set()
    equipas = set(sess.scalars(
        select(EEJ.sofascore_event_id).where(EEJ.sofascore_event_id.in_(ids))
        .group_by(EEJ.sofascore_event_id).having(func.count() >= 2)).all())
    jogadores = set(sess.scalars(
        select(EJJ.sofascore_event_id).where(EJJ.sofascore_event_id.in_(ids))
        .distinct()).all())
    return equipas, jogadores


@dataclass
class ResumoIncremento:
    ligas: int = 0
    jogos_vistos: int = 0
    jogos_novos: int = 0
    linhas_equipas: int = 0
    linhas_jogadores: int = 0
    erros: int = 0
    interrompido: bool = False  # Sofascore a bloquear: parou para nao piorar
    sem_tempo: bool = False     # acabou o prazo da noite: o resto fica para amanha
    equipas_fortes: int = 0     # >= 5 jogos na ficha
    equipas_total: int = 0
    por_liga: dict[str, int] = field(default_factory=dict)

    def texto(self) -> str:
        linhas = [
            "🧠 *Incremento diario dos mini-cerebros*",
            f"  {self.ligas} ligas · {self.jogos_vistos} jogos terminados · "
            f"{self.jogos_novos} novos",
            f"  +{self.linhas_equipas} linhas de equipa · +{self.linhas_jogadores} de jogadores",
        ]
        if self.equipas_total:
            linhas.append(f"  Fichas fortes (5+ jogos): {self.equipas_fortes} de "
                          f"{self.equipas_total} equipas "
                          f"({self.equipas_fortes / self.equipas_total:.0%})")
        if self.erros:
            linhas.append(f"  ⚠ {self.erros} jogos com erro (ficam para a proxima noite)")
        if self.interrompido:
            linhas.append("  ⚠ Sofascore a bloquear: parei para nao piorar o bloqueio do "
                          "scanner; o resto fica para a proxima noite")
        if self.sem_tempo:
            linhas.append("  ⏱ Acabou o tempo da noite: o que ficou por buscar vem na proxima")
        return "\n".join(linhas)


def fichas_fortes(sess, desde: datetime, min_jogos: int = 5) -> tuple[int, int]:
    """(equipas com >= min_jogos guardados, equipas que jogaram desde `desde`)."""
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    activas = select(EEJ.sofascore_team_id).where(EEJ.data_jogo >= desde).distinct()
    contagens = sess.execute(
        select(EEJ.sofascore_team_id, func.count())
        .where(EEJ.sofascore_team_id.in_(activas))
        .group_by(EEJ.sofascore_team_id)).all()
    return sum(1 for _, n in contagens if n >= min_jogos), len(contagens)


def correr(dias: int = 3, agora: datetime | None = None) -> ResumoIncremento:
    from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel
    from apostas.modelos import historico_equipas, historico_jogadores
    from apostas.utils.db import abrir_sessao

    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None)
    desde = agora - timedelta(days=dias)
    desde_ts = int(desde.replace(tzinfo=timezone.utc).timestamp())
    from apostas.ingestao.sofascore_live import sofascore_disponivel

    from apostas.utils import prazo

    resumo = ResumoIncremento()
    for tid, nome in sorted(ligas_alvo().items(), key=lambda x: x[1]):
        if prazo.esgotado():
            resumo.sem_tempo = True
            break
        if not sofascore_disponivel():
            resumo.interrompido = True
            break
        try:
            ids = eventos_terminados(tid, desde_ts)
        except FlaresolverrIndisponivel:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Incremento %s: lista de jogos falhou: %s", nome, exc)
            resumo.erros += 1
            continue
        resumo.ligas += 1
        resumo.jogos_vistos += len(ids)
        with abrir_sessao() as s:
            com_equipas, com_jogadores = ja_guardados(s, ids)
        novos = 0
        for eid in ids:
            falta_eq, falta_jog = eid not in com_equipas, eid not in com_jogadores
            if not (falta_eq or falta_jog):
                continue
            if prazo.esgotado():
                resumo.sem_tempo = True
                break
            if not sofascore_disponivel():
                resumo.interrompido = True
                break
            try:
                if falta_eq:
                    resumo.linhas_equipas += historico_equipas.gravar_jogo_terminado(eid)
                if falta_jog:
                    resumo.linhas_jogadores += historico_jogadores.gravar_jogadores_terminado(eid)
                novos += 1
            except FlaresolverrIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Incremento event %s falhou: %s", eid, exc)
                resumo.erros += 1
            time.sleep(_PAUSA_S)
        resumo.jogos_novos += novos
        if ids:
            resumo.por_liga[nome] = novos
            print(f"  {nome}: {len(ids)} terminados, {novos} novos")
    with abrir_sessao() as s:
        resumo.equipas_fortes, resumo.equipas_total = fichas_fortes(
            s, agora - timedelta(days=30))
    return resumo
