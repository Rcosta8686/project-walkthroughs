"""Jogos sem estatisticas no Sofascore: limpar resultados falsos antigos.

Ate 07/11 o resolver contava cantos/remates/cartoes em falta como 0. Nos jogos
sem estatisticas (algumas ligas/jogos de divisoes inferiores) os "over" ficavam
perdidos e os "under" ganhos sem o serem, e isso enganava o calibrador, o
PRIME e o corte de mercados.

Um jogo e "sem estatisticas" quando nada mostra que as teve:
  - a BD guardou-o sem cantos e sem remates nas 2 equipas (pode ser so uma
    falha ao gravar, por isso nao chega);
  - nenhum momento ao vivo guardado (snapshot) tem cantos ou remates;
  - nenhuma dica desse jogo no CSV tem cantos/remates a baliza no momento;
  - nenhuma dica desse mercado tem um resultado impossivel com 0 (um "under"
    perdido ou um "over" ganho prova que o total era real).
So ai, e so as dicas que o 0 falso explica ("under" ganho, "over" perdido),
passam a SD (sem dados). Idempotente: corre no fim de cada resolver.
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
    jogos: int = 0             # jogos realmente sem estatisticas
    jogos_com_prova: int = 0   # candidatos que afinal tinham dados reais (mantidos)
    reabertas: int = 0         # SD da 1a versao (demasiado larga) que voltam a resolver

    def texto(self) -> str:
        top = ", ".join(f"{g}: {n}" for g, n in
                        sorted(self.por_grupo.items(), key=lambda x: -x[1])[:6])
        partes = []
        if self.n:
            partes.append(f"🧹 {self.n} resultados antigos corrigidos para SD em {self.jogos} "
                          f"jogos sem estatisticas no Sofascore (tinham contado 0): {top}")
        if self.jogos_com_prova:
            partes.append(f"   {self.jogos_com_prova} jogos mantidos: a BD nao os gravou, mas "
                          f"ha prova de que tinham dados reais.")
        if self.reabertas:
            partes.append(f"   ↩ {self.reabertas} dicas marcadas SD por engano voltam a ser "
                          f"resolvidas no Sofascore.")
        return "\n".join(partes)


def _canonico(mercado: str) -> str:
    m = (mercado or "").strip().lower()
    return {"sot": "remates_a_baliza", "cards": "cartoes"}.get(m, m)


def _cheio(v) -> bool:
    return str(v or "").strip() not in ("", "0", "0.0")


def _explicado_por_zero(lado: str, res: str) -> bool:
    """Com total 0 um "under" ganha sempre e um "over" perde sempre."""
    lado = lado.strip().lower()
    return (lado == "under" and res == "W") or (lado == "over" and res == "L")


def _provas_de_dados(linhas: list[dict], sem: dict[str, set[str]]) -> set[tuple[str, str]]:
    """(jogo, mercado) com prova no CSV de que os dados eram reais."""
    provas: set[tuple[str, str]] = set()
    for r in linhas:
        eid = (r.get("event_id") or "").strip()
        mercados = sem.get(eid)
        if not mercados:
            continue
        # Cantos/remates a baliza vistos ao vivo: o jogo tinha estatisticas.
        if any(_cheio(r.get(c)) for c in ("corners_c", "corners_f",
                                          "shots_on_target_c", "shots_on_target_f")):
            provas |= {(eid, m) for m in mercados}
        if "cartoes" in mercados and any(_cheio(r.get(c)) for c in ("yellows_c", "yellows_f")):
            provas.add((eid, "cartoes"))
        # Resultado impossivel com 0: o total usado era real. Cantos/remates
        # reais = o jogo tinha estatisticas (tudo); cartoes podem vir dos
        # incidentes, so provam os cartoes.
        m = _canonico(r.get("mercado") or "")
        res = (r.get("resultado") or "").strip()
        if m in mercados and res in ("W", "L") and not _explicado_por_zero(r.get("lado") or "",
                                                                             res):
            provas |= {(eid, x) for x in mercados} if m != "cartoes" else {(eid, m)}
    return provas


_COLS_BD = {"cantos": "cantos", "remates": "remates", "remates_a_baliza": "sot",
            "cartoes": "cards_amarelos"}
_COLS_SNAP = {"cantos": "corners_casa", "remates": "shots_total_casa",
              "remates_a_baliza": "shots_on_target_casa"}


def mercados_com_dados(sess, eventos: set[str]) -> set[tuple[str, str]]:
    """(jogo, mercado) com dados reais na BD (fichas ou momentos ao vivo)."""
    from sqlalchemy import select

    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    ids = sorted(int(e) for e in eventos if e.isdigit())
    out: set[tuple[str, str]] = set()
    for i in range(0, len(ids), 900):
        lote = ids[i:i + 900]
        for r in sess.execute(select(EEJ.sofascore_event_id, EEJ.cantos, EEJ.remates, EEJ.sot,
                                     EEJ.cards_amarelos)
                              .where(EEJ.sofascore_event_id.in_(lote))).all():
            out |= {(str(r.sofascore_event_id), m) for m, c in _COLS_BD.items()
                    if getattr(r, c) is not None}
        for m, c in _COLS_SNAP.items():
            out |= {(str(e), m) for e in sess.scalars(
                select(Snap.event_id).where(Snap.event_id.in_(lote),
                                            getattr(Snap, c).is_not(None)).distinct()).all()}
    return out


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
    # Momentos ao vivo com cantos/remates provam que o jogo tinha estatisticas.
    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    ids = [int(e) for e in out]
    com_stats: set[str] = set()
    for i in range(0, len(ids), 900):  # limite de variaveis por consulta no SQLite
        com_stats |= {str(e) for e in sess.scalars(
            select(Snap.event_id).where(
                Snap.event_id.in_(ids[i:i + 900]),
                (Snap.corners_casa.is_not(None)) | (Snap.shots_total_casa.is_not(None)))
            .distinct()).all()}
    return {e: m for e, m in out.items() if e not in com_stats}


def _marca_reabertura(csv_path: Path) -> Path:
    return csv_path.with_name(".sem_stats_reabertura_feita")


def reparar(csv_path: Path, sem: dict[str, set[str]] | None = None,
            aplicar: bool = True, com_dados: set[tuple[str, str]] | None = None) -> Correccao:
    if not csv_path.exists():
        return Correccao()
    with csv_path.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        linhas = list(reader)
        colunas = list(reader.fieldnames or [])
    candidatas = [r for r in linhas
                  if (r.get("resultado") or "").strip() in ("W", "L", "push")
                  and _canonico(r.get("mercado") or "") in _MERCADOS_STATS]
    # Uma so vez: a 1a versao (07/11) marcou SD tambem jogos que tinham dados.
    reabrir = not _marca_reabertura(csv_path).exists()
    sd = [r for r in linhas if (r.get("resultado") or "").strip() == SEM_DADOS
          and _canonico(r.get("mercado") or "") in _MERCADOS_STATS] if reabrir else []
    if not candidatas and not sd:
        if reabrir and aplicar:
            _marca_reabertura(csv_path).write_text("ok", encoding="utf-8")
        return Correccao()
    if sem is None or (sd and com_dados is None):
        from apostas.utils.db import abrir_sessao
        with abrir_sessao() as s:
            if sem is None:
                sem = jogos_sem_stats(s)
            if sd and com_dados is None:
                com_dados = mercados_com_dados(
                    s, {(r.get("event_id") or "").strip() for r in sd})
    corr = Correccao()
    provas = _provas_de_dados(linhas, sem)
    for row in sd:
        eid, m = (row.get("event_id") or "").strip(), _canonico(row.get("mercado") or "")
        if (eid, m) in provas or (eid, m) in (com_dados or set()):
            row["resultado"] = row["lucro"] = row["resolvido_em"] = ""
            corr.reabertas += 1
    alvo: set[str] = set()
    for row in candidatas:
        eid = (row.get("event_id") or "").strip()
        mercados = sem.get(eid)
        m = _canonico(row.get("mercado") or "")
        if not mercados or m not in mercados:
            continue
        if (eid, m) in provas:
            continue
        res = (row.get("resultado") or "").strip()
        if not _explicado_por_zero(row.get("lado") or "", res):
            continue
        alvo.add(eid)
        grupo = f"{(row.get('lado') or '').strip().lower()} {m} ({res})"
        corr.por_grupo[grupo] = corr.por_grupo.get(grupo, 0) + 1
        corr.n += 1
        row["resultado"], row["lucro"] = SEM_DADOS, ""
    corr.jogos = len(alvo)
    corr.jogos_com_prova = len({e for e, _ in provas} - alvo)
    if (corr.n or corr.reabertas) and aplicar:
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
    if reabrir and aplicar:
        _marca_reabertura(csv_path).write_text("ok", encoding="utf-8")
    return corr
