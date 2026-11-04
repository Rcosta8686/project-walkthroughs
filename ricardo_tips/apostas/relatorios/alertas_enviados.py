"""Registo do que foi de facto enviado ao Telegram, para medir se ganha.

O sugestoes_live_v2.csv tem TODAS as sugestoes do scanner (centenas por dia,
a maioria filtrada e nunca enviada) e a BD guarda projeccoes que nunca sairam.
Aqui fica uma linha por mensagem enviada:

- tipo "alerta": sinal com alert_id; o resultado e o lucro vem do CSV de
  sugestoes (preenchido pelo resolver).
- tipo "projeccao": projeccao 40'/55'/70'; regista-se a linha e o lado da
  conclusao do mega-cerebro e a odd justa dessa linha. O resultado sai do
  total final das 2 equipas na BD (EstatisticaEquipaJogo).

Lucro sempre a 1 unidade, a odd indicada na mensagem.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from apostas.utils.config import project_root

CAMPOS = ["alert_id", "ts_envio", "tipo", "event_id", "mercado", "lado", "linha", "odd"]
_NOME_MERCADO = {
    "remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza",
    "sot": "🎯 remates a baliza", "cantos": "🚩 cantos", "cartoes": "🟨 cartoes",
    "cards": "🟨 cartoes", "golos": "⚽ golos", "btts": "🔃 BTTS", "1x2": "🏆 1X2",
}


def caminho() -> Path:
    return project_root() / "dados" / "tracking" / "alertas_enviados.csv"


def registar(alert_id=None, ts: datetime | None = None, path: Path | None = None, *,
             tipo: str = "alerta", event_id=None, mercado: str = "", lado: str = "",
             linha=None, odd=None) -> None:
    if tipo == "alerta" and not alert_id:
        return
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    novo = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        if novo:
            w.writerow(CAMPOS)
        w.writerow([alert_id or "", (ts or datetime.now()).isoformat(timespec="seconds"),
                    tipo, event_id or "", mercado, lado,
                    "" if linha is None else linha,
                    "" if odd is None else round(float(odd), 3)])


def carregar(path: Path | None = None) -> list[dict]:
    p = path or caminho()
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            try:
                row["ts"] = datetime.fromisoformat((row.get("ts_envio") or "")[:19])
            except ValueError:
                continue
            row["tipo"] = row.get("tipo") or "alerta"
            out.append(row)
    return out


def _float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def projeccoes_no_periodo(inicio: datetime, fim: datetime,
                          path: Path | None = None) -> set[tuple[int, str]]:
    """(event_id, mercado) das projeccoes enviadas, para ir buscar os totais a BD."""
    return {(eid, r["mercado"]) for r in carregar(path)
            if r["tipo"] == "projeccao" and inicio <= r["ts"] < fim
            and (eid := _int(r.get("event_id"))) is not None}


def totais_finais_bd(sess, chaves: set[tuple[int, str]]) -> dict[tuple[int, str], float]:
    """Total final (2 equipas) por (event_id, mercado). Jogos sem as 2 fichas ficam de fora."""
    from sqlalchemy import func, select

    from apostas.modelos.correcao_projecoes import _COLUNA
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ

    out: dict[tuple[int, str], float] = {}
    for mercado in {m for _, m in chaves}:
        coluna = _COLUNA.get(mercado)
        ids = [e for e, m in chaves if m == mercado]
        if coluna is None or not ids:
            continue
        q = (select(EEJ.sofascore_event_id, func.sum(coluna), func.count(coluna))
             .where(EEJ.sofascore_event_id.in_(ids))
             .group_by(EEJ.sofascore_event_id))
        for eid, total, n in sess.execute(q).all():
            if n == 2 and total is not None:
                out[(int(eid), mercado)] = float(total)
    return out


@dataclass
class LinhaMercado:
    nome: str
    vitorias: int = 0
    derrotas: int = 0
    lucro: float = 0.0


@dataclass
class ResumoEnviados:
    inicio: datetime
    fim: datetime
    registo_desde: datetime | None  # primeiro envio registado (qualquer mes)
    n: int = 0
    n_projeccoes: int = 0
    vitorias: int = 0
    derrotas: int = 0
    push: int = 0
    pendentes: int = 0
    lucro: float = 0.0
    n_com_lucro: int = 0
    por_mercado: list[LinhaMercado] = field(default_factory=list)

    @property
    def roi(self) -> float | None:
        return self.lucro / self.n_com_lucro * 100 if self.n_com_lucro else None


def _resultado_projeccao(r: dict, totais: dict) -> tuple[str, float | None]:
    total = totais.get((_int(r.get("event_id")), r.get("mercado")))
    linha = _float(r.get("linha"))
    if total is None or linha is None:
        return "pendente", None
    lado = (r.get("lado") or "").lower()
    if total == linha:
        return "push", 0.0
    ganhou = total > linha if lado == "over" else total < linha
    odd = _float(r.get("odd"))
    if odd is None or odd <= 1.0:
        return ("W" if ganhou else "L"), None
    return ("W", odd - 1.0) if ganhou else ("L", -1.0)


def resumir(inicio: datetime, fim: datetime, csv_sugestoes: Path,
            csv_enviados: Path | None = None,
            totais_proj: dict[tuple[int, str], float] | None = None) -> ResumoEnviados:
    enviados = carregar(csv_enviados)
    rel = ResumoEnviados(inicio=inicio, fim=fim,
                         registo_desde=min(r["ts"] for r in enviados) if enviados else None)
    no_mes = [r for r in enviados if inicio <= r["ts"] < fim]

    # Alertas: resultado do CSV de sugestoes (1 linha por alert_id)
    ids = {(r.get("alert_id") or "").strip() for r in no_mes if r["tipo"] == "alerta"}
    ids.discard("")
    linhas_csv: dict[str, dict] = {}
    if ids and csv_sugestoes.exists():
        with csv_sugestoes.open(encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                aid = (row.get("alert_id") or "").strip()
                if aid in ids and aid not in linhas_csv:
                    linhas_csv[aid] = row

    itens: list[tuple[str, str, float | None]] = []  # (nome, resultado, lucro)
    for aid in ids:
        row = linhas_csv.get(aid)
        if row is None:
            itens.append(("?", "pendente", None))
            continue
        m = (row.get("mercado") or "?").strip().lower()
        nome = f"{_NOME_MERCADO.get(m, m)} {(row.get('lado') or '').strip().lower()}"
        itens.append((nome, (row.get("resultado") or "").strip() or "pendente",
                      _float(row.get("lucro"))))
    for r in no_mes:
        if r["tipo"] != "projeccao":
            continue
        rel.n_projeccoes += 1
        m = (r.get("mercado") or "?").lower()
        res, lucro = _resultado_projeccao(r, totais_proj or {})
        itens.append((f"{_NOME_MERCADO.get(m, m)} {r.get('lado', '')} (projecção)", res, lucro))

    mercados: dict[str, LinhaMercado] = {}
    for nome, res, lucro in itens:
        rel.n += 1
        lm = mercados.setdefault(nome.strip(), LinhaMercado(nome=nome.strip()))
        if res == "W":
            rel.vitorias += 1
            lm.vitorias += 1
        elif res == "L":
            rel.derrotas += 1
            lm.derrotas += 1
        elif res == "push":
            rel.push += 1
        else:
            rel.pendentes += 1
            continue
        if lucro is not None:
            rel.lucro += lucro
            rel.n_com_lucro += 1
            lm.lucro += lucro
    rel.por_mercado = sorted(mercados.values(),
                             key=lambda l: (-(l.vitorias + l.derrotas), l.nome))
    return rel


def formatar(rel: ResumoEnviados) -> str:
    linhas = ["*📲 Dicas enviadas ao Telegram*"]
    if rel.n == 0:
        if rel.registo_desde is None or rel.registo_desde >= rel.fim:
            linhas.append("  _O registo das dicas enviadas começou agora; "
                          "o próximo relatório já as conta._")
        else:
            linhas.append("  Nenhuma dica enviada neste mês.")
        return "\n".join(linhas)
    if rel.registo_desde and rel.registo_desde > rel.inicio:
        linhas.append(f"  _(registo desde {rel.registo_desde:%d/%m %H:%M})_")
    decididas = rel.vitorias + rel.derrotas
    taxa = f"{rel.vitorias / decididas * 100:.0f}%" if decididas else "—"
    linhas.append(
        f"  {rel.n} dicas ({rel.n - rel.n_projeccoes} alertas · {rel.n_projeccoes} projecções)"
        f" · {rel.vitorias}✅ {rel.derrotas}❌"
        + (f" {rel.push}↩" if rel.push else "")
        + (f" · {rel.pendentes} por resolver" if rel.pendentes else ""))
    sinal = "+" if rel.lucro >= 0 else ""
    linhas.append(f"  Acerto {taxa} · lucro *{sinal}{rel.lucro:.2f} un*"
                  + (f" · ROI *{rel.roi:+.1f}%*" if rel.roi is not None else ""))
    for lm in rel.por_mercado[:10]:
        if lm.vitorias + lm.derrotas == 0:
            continue
        icone = "🟢" if lm.lucro > 0 else ("🔴" if lm.lucro < 0 else "⚪")
        linhas.append(f"  {icone} {lm.nome}: {lm.vitorias}/{lm.vitorias + lm.derrotas}"
                      f" · {lm.lucro:+.2f} un")
    linhas.append("  _1 un por dica, à odd indicada na mensagem._")
    return "\n".join(linhas)
