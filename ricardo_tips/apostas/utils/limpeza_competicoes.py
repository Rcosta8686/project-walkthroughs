"""Apaga da BD e do CSV os jogos que nao sao oficiais de clubes.

Amigaveis, jogos de seleccoes e de formacao nao entram em nada (ver
apostas/dados_referencia/competicoes.py). Os que ja estavam guardados saem:
  - BD: fichas das equipas e dos jogadores, momentos ao vivo, projeccoes,
    sinais do mega-cerebro e odds pre-jogo desses jogos;
  - CSV das dicas e odds das casas desses jogos.
As apostas reais do Ricardo nunca sao apagadas. As linhas tiradas do CSV ficam
em dados/tracking/removidas_amigaveis_selecoes.csv (por seguranca, nao se usa).
Corre todas as noites no incremento (nao faz nada se ja esta limpo).
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from apostas.dados_referencia.competicoes import motivo_por_nome


@dataclass
class ResultadoLimpeza:
    jogos: int = 0
    linhas_bd: dict[str, int] = field(default_factory=dict)
    linhas_csv: int = 0
    competicoes: dict[str, int] = field(default_factory=dict)   # nome -> jogos

    @property
    def vazio(self) -> bool:
        return not self.jogos and not self.linhas_csv

    def texto(self) -> str:
        if self.vazio:
            return "🧽 Sem amigaveis nem jogos de seleccoes guardados."
        bd = sum(self.linhas_bd.values())
        top = ", ".join(f"{n} ({j})" for n, j in
                        sorted(self.competicoes.items(), key=lambda x: -x[1])[:8])
        return (f"🧽 Fora: {self.jogos} jogos que nao sao oficiais de clubes · {bd} linhas da BD · "
                f"{self.linhas_csv} dicas do CSV\n   {top}")


def _tabelas():
    from apostas.utils.schema import (
        EstadoLiveSnapshot, EstatisticaEquipaJogo, EstatisticaJogadorJogo, ProjecaoLive,
        SinalMegaCerebro,
    )
    return [("fichas das equipas", EstatisticaEquipaJogo, "sofascore_event_id"),
            ("fichas dos jogadores", EstatisticaJogadorJogo, "sofascore_event_id"),
            ("momentos ao vivo", EstadoLiveSnapshot, "event_id"),
            ("projeccoes", ProjecaoLive, "sofascore_event_id"),
            ("sinais mega-cerebro", SinalMegaCerebro, "event_id")]


def eventos_excluidos(sess) -> dict[int, str]:
    """event_id -> competicao, para os jogos guardados que nao sao oficiais de clubes."""
    from sqlalchemy import select
    out: dict[int, str] = {}
    for _, modelo, col in _tabelas():
        nomes = [n for n in sess.scalars(select(modelo.liga).distinct()).all()
                 if n and motivo_por_nome(n)]
        if not nomes:
            continue
        for eid, liga in sess.execute(select(getattr(modelo, col), modelo.liga)
                                      .where(modelo.liga.in_(nomes)).distinct()).all():
            out.setdefault(int(eid), str(liga))
    return out


def _reescrever(path: Path, linhas: list[dict], colunas: list[str]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    for tentativa in range(5):  # Windows: o scanner pode ter o ficheiro aberto
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if tentativa == 4:
                raise
            time.sleep(0.5)


def _limpar_csv(path: Path, ids: set[str], aplicar: bool,
                guardar_em: Path | None) -> tuple[int, dict[str, str]]:
    """Tira do CSV as linhas desses jogos ou de competicoes fora.
    -> (linhas tiradas, {jogo: competicao})."""
    if not path.exists():
        return 0, {}
    with path.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        linhas = list(reader)
        colunas = list(reader.fieldnames or [])
    fora, ficam = [], []
    for r in linhas:
        sai = (r.get("event_id") or "").strip() in ids or motivo_por_nome(r.get("liga"))
        (fora if sai else ficam).append(r)
    jogos = {(r.get("event_id") or "").strip(): (r.get("liga") or "?").strip() for r in fora}
    if not fora or not aplicar:
        return len(fora), jogos
    if guardar_em is not None:
        novo = not guardar_em.exists()
        with guardar_em.open("a", encoding="utf-8", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
            if novo:
                w.writeheader()
            w.writerows(fora)
    _reescrever(path, ficam, colunas)
    return len(fora), jogos


def limpar(aplicar: bool = True, tracking: Path | None = None) -> ResultadoLimpeza:
    from sqlalchemy import delete, func, select

    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import OddsSnapshotPrelive
    if tracking is None:
        from apostas.utils.config import project_root
        tracking = project_root() / "dados" / "tracking"
    res = ResultadoLimpeza()
    with abrir_sessao() as s:
        ex = eventos_excluidos(s)
        ids = sorted(ex)
        tabelas = _tabelas() + [("odds pre-jogo", OddsSnapshotPrelive, "sofascore_event_id")]
        for nome, modelo, col in tabelas:
            coluna = getattr(modelo, col)
            n = 0
            for i in range(0, len(ids), 900):  # limite de variaveis por consulta no SQLite
                lote = ids[i:i + 900]
                n += int(s.scalar(select(func.count()).select_from(modelo)
                                  .where(coluna.in_(lote))) or 0)
                if aplicar:
                    s.execute(delete(modelo).where(coluna.in_(lote)))
            if n:
                res.linhas_bd[nome] = n
    jogos = {str(e): liga for e, liga in ex.items()}
    n_csv, do_csv = _limpar_csv(tracking / "sugestoes_live_v2.csv", set(jogos), aplicar,
                                tracking / "removidas_amigaveis_selecoes.csv")
    for e, liga in do_csv.items():
        jogos.setdefault(e, liga)
    res.linhas_csv = n_csv
    _limpar_csv(tracking / "odds_casa.csv", set(jogos), aplicar, None)
    res.jogos = len(jogos)
    for liga in jogos.values():
        res.competicoes[liga] = res.competicoes.get(liga, 0) + 1
    return res
