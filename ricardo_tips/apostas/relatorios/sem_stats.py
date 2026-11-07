"""Jogos sem estatisticas no Sofascore: limpar resultados falsos antigos.

Ate 07/11 o resolver contava cantos/remates/cartoes em falta como 0. Nos jogos
sem estatisticas (algumas ligas/jogos de divisoes inferiores) os "over" ficavam
perdidos e os "under" ganhos sem o serem, e isso enganava o calibrador, o
PRIME e o corte de mercados.

Aqui: jogos guardados na BD sem cantos e sem remates nas 2 equipas sao jogos
sem estatisticas; as dicas de cantos/remates/remates a baliza desses jogos (e
de cartoes, se a BD tambem nao os tiver) passam de W/L a SD (sem dados).
Idempotente: corre no fim de cada resolver.
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

SEM_DADOS = "SD"
_MERCADOS_STATS = {"cantos", "remates", "remates_a_baliza", "cartoes"}


@dataclass
class Correccao:
    n: int = 0
    por_grupo: dict[str, int] = field(default_factory=dict)   # "under cantos (W)" -> n

    def texto(self) -> str:
        top = ", ".join(f"{g}: {n}" for g, n in
                        sorted(self.por_grupo.items(), key=lambda x: -x[1])[:6])
        return (f"🧹 {self.n} resultados antigos corrigidos para SD (jogos sem estatisticas no "
                f"Sofascore, tinham contado 0): {top}")


def _canonico(mercado: str) -> str:
    m = (mercado or "").strip().lower()
    return {"sot": "remates_a_baliza", "cards": "cartoes"}.get(m, m)


def jogos_sem_stats(sess) -> dict[str, set[str]]:
    """event_id (texto) -> mercados sem dados nesse jogo."""
    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    por_evento: dict[int, list] = {}
    for r in sess.execute(select(EEJ.sofascore_event_id, EEJ.cantos, EEJ.remates, EEJ.sot,
                                 EEJ.cards_amarelos)).all():
        por_evento.setdefault(int(r.sofascore_event_id), []).append(r)
    out: dict[str, set[str]] = {}
    for eid, rows in por_evento.items():
        if len(rows) != 2:
            continue
        if not all(r.cantos is None and r.remates is None for r in rows):
            continue  # o jogo tem estatisticas
        sem = {"cantos", "remates"}
        if all(r.sot is None for r in rows):
            sem.add("remates_a_baliza")
        if all(r.cards_amarelos is None for r in rows):
            sem.add("cartoes")
        out[str(eid)] = sem
    return out


def reparar(csv_path: Path, sem: dict[str, set[str]] | None = None,
            aplicar: bool = True) -> Correccao:
    if not csv_path.exists():
        return Correccao()
    with csv_path.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        linhas = list(reader)
        colunas = list(reader.fieldnames or [])
    candidatas = [r for r in linhas
                  if (r.get("resultado") or "").strip() in ("W", "L", "push")
                  and _canonico(r.get("mercado") or "") in _MERCADOS_STATS]
    if not candidatas:
        return Correccao()
    if sem is None:
        from apostas.utils.db import abrir_sessao
        with abrir_sessao() as s:
            sem = jogos_sem_stats(s)
    corr = Correccao()
    for row in candidatas:
        res = (row.get("resultado") or "").strip()
        mercados = sem.get((row.get("event_id") or "").strip())
        m = _canonico(row.get("mercado") or "")
        if not mercados or m not in mercados:
            continue
        grupo = f"{(row.get('lado') or '').strip().lower()} {m} ({res})"
        corr.por_grupo[grupo] = corr.por_grupo.get(grupo, 0) + 1
        corr.n += 1
        row["resultado"], row["lucro"] = SEM_DADOS, ""
    if corr.n and aplicar:
        tmp = csv_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
            w.writeheader()
            w.writerows(linhas)
        for tentativa in range(5):  # Windows: o scanner pode ter o ficheiro aberto
            try:
                tmp.replace(csv_path)
                break
            except PermissionError:
                if tentativa == 4:
                    raise
                time.sleep(0.5)
    return corr
