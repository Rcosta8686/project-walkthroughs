"""Multiplas enviadas ao Telegram: registo, resultado e o que se aprende com elas.

Cada multipla enviada fica em dados/tracking/multiplas.csv, uma linha por
selecao (jogador, equipa ou o jogo, o mercado, a linha k de "k+" e a
probabilidade que o modelo deu). De noite, com os jogos ja na BD:

- jogador: remates, remates a baliza e amarelo da ficha dele no jogo
  (EstatisticaJogadorJogo). Nao jogou = nula, como nas casas;
- equipa: cantos, remates e remates a baliza da ficha da equipa no jogo;
- jogo: amarelos das duas equipas somados.

A multipla perde com uma selecao perdida, ganha quando todas as outras
ganharam (as nulas saem, como nas casas). Por tipo de selecao compara-se a
probabilidade media que o modelo deu com o que aconteceu: e assim que se ve
onde o modelo esta optimista ou pessimista demais.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from apostas.utils.config import project_root
from apostas.utils.tempo import agora_utc

CAMPOS = ["multipla_id", "ts_envio", "tipo", "event_id", "jogo", "mercado",
          "sujeito_id", "sujeito", "linha", "p", "descricao"]
DIAS_SEM_DADOS = 3   # jogo que ao fim de 3 dias continua sem estatisticas: SD

NOME_MERCADO = {
    "remates": "remates do jogador",
    "sot": "remates a baliza do jogador",
    "cartao": "amarelo do jogador",
    "cantos_equipa": "cantos da equipa",
    "remates_equipa": "remates da equipa",
    "sot_equipa": "remates a baliza da equipa",
    "cartoes_jogo": "amarelos do jogo",
}
_ICONE = {"W": "✅", "L": "❌", "nula": "↩", "pendente": "⏳", "SD": "∅"}
_TITULO = {"segura": "🎯 Segura", "maluca": "🎰 Maluca"}


def caminho() -> Path:
    return project_root() / "dados" / "tracking" / "multiplas.csv"


def registar(tipo: str, selecoes: list, ts: datetime | None = None,
             path: Path | None = None) -> str | None:
    """Guarda uma multipla enviada. `selecoes`: objectos com jogo, event_id,
    jogador, player_id, descricao, p, mercado, linha e sujeito_id."""
    if not selecoes:
        return None
    ts = ts or agora_utc()
    mid = f"{ts:%Y%m%d%H%M%S}-{tipo}"
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    novo = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        if novo:
            w.writerow(CAMPOS)
        for s in selecoes:
            sujeito_id = getattr(s, "sujeito_id", None)
            if sujeito_id is None:
                sujeito_id = s.player_id
            w.writerow([mid, ts.isoformat(timespec="seconds"), tipo, s.event_id, s.jogo,
                        s.mercado, sujeito_id, s.jogador, getattr(s, "linha", 1),
                        round(float(s.p), 4), s.descricao])
    return mid


def _int(x) -> int | None:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def _float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def carregar(path: Path | None = None) -> list[dict]:
    p = path or caminho()
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                row["ts"] = datetime.fromisoformat((row.get("ts_envio") or "")[:19])
            except ValueError:
                continue
            out.append(row)
    return out


@dataclass
class DadosJogos:
    """O que a BD sabe dos jogos das multiplas."""
    jogador: dict[tuple[int, int], dict] = field(default_factory=dict)   # (evento, jogador)
    equipa: dict[tuple[int, int], dict] = field(default_factory=dict)    # (evento, equipa)
    com_jogadores: set[int] = field(default_factory=set)
    com_equipas: set[int] = field(default_factory=set)                  # as 2 fichas


def dados_da_bd(sess, linhas: list[dict]) -> DadosJogos:
    from sqlalchemy import select

    from apostas.modelos.jogadores import _amarelo
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    d = DadosJogos()
    eventos = sorted({e for r in linhas if (e := _int(r.get("event_id"))) is not None})
    for i in range(0, len(eventos), 500):
        bloco = eventos[i:i + 500]
        for r in sess.scalars(select(EJJ).where(EJJ.sofascore_event_id.in_(bloco))):
            d.com_jogadores.add(r.sofascore_event_id)
            d.jogador[(r.sofascore_event_id, r.sofascore_player_id)] = {
                "minutos": r.minutos, "remates": r.remates, "sot": r.sot,
                "cartao": _amarelo(r)}
        por_evento: dict[int, int] = {}
        for r in sess.scalars(select(EEJ).where(EEJ.sofascore_event_id.in_(bloco))):
            por_evento[r.sofascore_event_id] = por_evento.get(r.sofascore_event_id, 0) + 1
            d.equipa[(r.sofascore_event_id, r.sofascore_team_id)] = {
                "cantos": r.cantos, "remates": r.remates, "sot": r.sot,
                "amarelos": r.cards_amarelos}
        d.com_equipas |= {e for e, n in por_evento.items() if n >= 2}
    return d


def _valor(r: dict, dados: DadosJogos) -> tuple[str, float | None]:
    """('ok', valor) | ('nula', None) | ('falta', None)."""
    eid, sid, m = _int(r.get("event_id")), _int(r.get("sujeito_id")), r.get("mercado") or ""
    if eid is None or sid is None:
        return "falta", None
    if m in ("remates", "sot", "cartao"):
        if eid not in dados.com_jogadores:
            return "falta", None
        ficha = dados.jogador.get((eid, sid))
        if ficha is None or not ficha.get("minutos"):
            return "nula", None          # nao jogou
        v = ficha.get(m)
        return ("ok", float(v)) if v is not None else ("falta", None)
    if eid not in dados.com_equipas:
        return "falta", None
    if m == "cartoes_jogo":
        vs = [f.get("amarelos") for (e, _), f in dados.equipa.items() if e == eid]
        if len(vs) < 2 or any(v is None for v in vs):
            return "falta", None
        return "ok", float(sum(v for v in vs if v is not None))
    if m.endswith("_equipa"):
        ficha = dados.equipa.get((eid, sid))
        v = ficha.get(m.removesuffix("_equipa")) if ficha else None
        return ("ok", float(v)) if v is not None else ("falta", None)
    return "falta", None


@dataclass
class SelecaoResolvida:
    jogo: str
    sujeito: str
    mercado: str
    descricao: str
    linha: int
    p: float
    resultado: str              # W | L | nula | pendente | SD
    valor: float | None = None


@dataclass
class MultiplaResolvida:
    id: str
    tipo: str
    ts: datetime
    selecoes: list[SelecaoResolvida]

    @property
    def resultado(self) -> str:
        rs = [s.resultado for s in self.selecoes]
        if "L" in rs:
            return "L"
        if "pendente" in rs:
            return "pendente"
        if "SD" in rs:
            return "SD"
        return "W" if "W" in rs else "nula"

    @property
    def p(self) -> float:
        p = 1.0
        for s in self.selecoes:
            if s.resultado != "nula":
                p *= s.p
        return p

    @property
    def acertadas(self) -> int:
        return sum(s.resultado == "W" for s in self.selecoes)

    @property
    def decididas(self) -> int:
        return sum(s.resultado in ("W", "L") for s in self.selecoes)


def resolver(linhas: list[dict], dados: DadosJogos,
             agora: datetime | None = None) -> list[MultiplaResolvida]:
    agora = agora or agora_utc()
    por_id: dict[str, MultiplaResolvida] = {}
    for r in linhas:
        mid = r.get("multipla_id") or ""
        m = por_id.setdefault(mid, MultiplaResolvida(mid, r.get("tipo") or "", r["ts"], []))
        estado, valor = _valor(r, dados)
        linha = _int(r.get("linha")) or 1
        if estado == "ok" and valor is not None:
            res = "W" if valor >= linha else "L"
        elif estado == "nula":
            res = "nula"
        else:
            res = "SD" if agora - r["ts"] > timedelta(days=DIAS_SEM_DADOS) else "pendente"
        m.selecoes.append(SelecaoResolvida(
            jogo=r.get("jogo") or "", sujeito=r.get("sujeito") or "",
            mercado=r.get("mercado") or "", descricao=r.get("descricao") or "",
            linha=linha, p=_float(r.get("p")) or 0.0, resultado=res, valor=valor))
    return sorted(por_id.values(), key=lambda m: m.ts)


def carregar_resolvidas(sess, inicio: datetime | None = None, fim: datetime | None = None,
                        path: Path | None = None,
                        agora: datetime | None = None) -> list[MultiplaResolvida]:
    linhas = [r for r in carregar(path)
              if (inicio is None or r["ts"] >= inicio) and (fim is None or r["ts"] < fim)]
    if not linhas:
        return []
    return resolver(linhas, dados_da_bd(sess, linhas), agora)


@dataclass
class Calibracao:
    mercado: str
    n: int = 0
    acertos: int = 0
    soma_p: float = 0.0

    @property
    def esperado(self) -> float:
        return self.soma_p / self.n if self.n else 0.0

    @property
    def real(self) -> float:
        return self.acertos / self.n if self.n else 0.0


def calibracao(mults: list[MultiplaResolvida]) -> list[Calibracao]:
    """Por tipo de selecao: probabilidade media dada vs o que aconteceu."""
    out: dict[str, Calibracao] = {}
    for m in mults:
        for s in m.selecoes:
            if s.resultado not in ("W", "L"):
                continue
            c = out.setdefault(s.mercado, Calibracao(s.mercado))
            c.n += 1
            c.acertos += s.resultado == "W"
            c.soma_p += s.p
    return sorted(out.values(), key=lambda c: -c.n)


def _linha_selecao(s: SelecaoResolvida) -> str:
    real = ""
    if s.valor is not None:
        real = f" → {s.valor:.0f}"
    elif s.resultado == "nula":
        real = " → nao jogou"
    return f"  {_ICONE.get(s.resultado, '⏳')} {s.sujeito} ({s.jogo}) · {s.descricao}{real}"


def texto_multiplas(mults: list[MultiplaResolvida], titulo: str) -> str:
    """Cada multipla com as selecoes uma a uma (o resumo da manha)."""
    if not mults:
        return ""
    linhas = [f"*{titulo}*"]
    for m in mults:
        nome = _TITULO.get(m.tipo, m.tipo)
        odd = f" · odd justa {1 / m.p:.1f}" if m.p > 0 else ""
        linhas.append(f"{_ICONE.get(m.resultado, '⏳')} *{nome}* ({m.ts:%d/%m %H:%M} UTC) · "
                      f"{m.acertadas}/{m.decididas or len(m.selecoes)} selecoes{odd}")
        linhas += [_linha_selecao(s) for s in m.selecoes]
    return "\n".join(linhas)


def formatar(mults: list[MultiplaResolvida]) -> str:
    """Balanco de todas as multiplas e a calibracao por tipo de selecao."""
    linhas = ["*🎰 Multiplas enviadas*"]
    if not mults:
        linhas.append("  Ainda nenhuma registada (ficam guardadas as enviadas com --telegram).")
        return "\n".join(linhas)
    for tipo in ("segura", "maluca"):
        ms = [m for m in mults if m.tipo == tipo]
        dec = [m for m in ms if m.resultado in ("W", "L")]
        if not ms:
            continue
        ganhas = sum(m.resultado == "W" for m in dec)
        esperado = sum(m.p for m in dec)
        sel = [s for m in dec for s in m.selecoes if s.resultado in ("W", "L")]
        sel_w = sum(s.resultado == "W" for s in sel)
        linhas.append(
            f"  {_TITULO.get(tipo, tipo)}: {ganhas}/{len(dec)} ganhas"
            + (f" (o modelo esperava {esperado:.1f})" if dec else "")
            + (f" · selecoes {sel_w}/{len(sel)} ({sel_w / len(sel) * 100:.0f}%)" if sel else "")
            + (f" · {len(ms) - len(dec)} por decidir" if len(ms) > len(dec) else ""))
    cal = calibracao(mults)
    if cal:
        linhas.append("  _Por tipo de selecao (o modelo dava → aconteceu):_")
        for c in cal:
            dif = (c.real - c.esperado) * 100
            if c.n < 10:
                icone = "⚪"
            elif dif <= -8:
                icone = "🔴"        # optimista demais
            elif dif >= 8:
                icone = "🔵"        # pessimista demais
            else:
                icone = "🟢"
            linhas.append(f"  {icone} {NOME_MERCADO.get(c.mercado, c.mercado)}: "
                          f"{c.esperado * 100:.0f}% → {c.real * 100:.0f}% ({c.acertos}/{c.n})")
        linhas.append("  _🟢 certo · 🔴 optimista demais · 🔵 pessimista demais · "
                      "⚪ ainda poucas (menos de 10)_")
    return "\n".join(linhas)
