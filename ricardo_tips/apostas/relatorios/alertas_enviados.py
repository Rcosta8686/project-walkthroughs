"""Registo do que foi de facto enviado ao Telegram, para medir se ganha.

O sugestoes_live_v2.csv tem TODAS as sugestoes do scanner (centenas por dia,
a maioria filtrada e nunca enviada) e a BD guarda projeccoes que nunca sairam.
Aqui fica uma linha por mensagem enviada:

- tipo "alerta": sinal com alert_id; o resultado e o lucro vem do CSV de
  sugestoes (preenchido pelo resolver).
- tipo "projeccao": projeccao 40'/55'/70'; regista-se a linha e o lado da
  conclusao do mega-cerebro e a odd justa dessa linha. O resultado sai do
  total final das 2 equipas na BD (EstatisticaEquipaJogo).
- tipos "cartoes_fim" (cartao no fim) e "mais_golos" (vao sair mais golos):
  resolvidos da mesma maneira, pelo total final na BD.

Lucro sempre a 1 unidade, a odd indicada na mensagem.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from apostas.utils.config import project_root
from apostas.utils.tempo import agora_utc

# odd = odd indicada na dica (alvo/justa); odd_real = a que o mercado pagava
# no momento do envio (Sofascore), quando havia. O lucro mede-se a odd real.
# p_modelo: nos alertas que se corrigem sozinhos (cartao no fim, mais golos), a
# probabilidade antes da correccao - aprende-se sempre sobre ela.
CAMPOS = ["alert_id", "ts_envio", "tipo", "event_id", "mercado", "lado", "linha", "odd",
          "odd_real", "p_modelo"]
_NOME_MERCADO = {
    "remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza",
    "sot": "🎯 remates a baliza", "cantos": "🚩 cantos", "cartoes": "🟨 cartoes",
    "cards": "🟨 cartoes", "golos": "⚽ golos", "btts": "🔃 BTTS", "1x2": "🏆 1X2",
}


def caminho() -> Path:
    return project_root() / "dados" / "tracking" / "alertas_enviados.csv"


def _actualizar_cabecalho(p: Path) -> None:
    """Ficheiro com o cabecalho antigo (sem odd_real): reescreve com o actual,
    para as linhas novas nao ficarem desalinhadas."""
    with p.open(encoding="utf-8", newline="") as fp:
        r = csv.DictReader(fp)
        if list(r.fieldnames or []) == CAMPOS:
            return
        linhas = list(r)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=CAMPOS, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    tmp.replace(p)


def registar(alert_id=None, ts: datetime | None = None, path: Path | None = None, *,
             tipo: str = "alerta", event_id=None, mercado: str = "", lado: str = "",
             linha=None, odd=None, odd_real=None, p_modelo=None) -> None:
    if tipo == "alerta" and not alert_id:
        return
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    novo = not p.exists()
    if not novo:
        _actualizar_cabecalho(p)
    with p.open("a", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        if novo:
            w.writerow(CAMPOS)
        w.writerow([alert_id or "", (ts or agora_utc()).isoformat(timespec="seconds"),
                    tipo, event_id or "", mercado, lado,
                    "" if linha is None else linha,
                    "" if odd is None else round(float(odd), 3),
                    "" if odd_real is None else round(float(odd_real), 3),
                    "" if p_modelo is None else round(float(p_modelo), 4)])


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
            if r["tipo"] != "alerta" and inicio <= r["ts"] < fim
            and (eid := _int(r.get("event_id"))) is not None}


def totais_finais_bd(sess, chaves: set[tuple[int, str]]) -> dict[tuple[int, str], float]:
    """Total final (2 equipas) por (event_id, mercado). Jogos sem as 2 fichas ficam de fora."""
    from sqlalchemy import func, select

    from apostas.modelos.correcao_projecoes import _COLUNA
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ

    out: dict[tuple[int, str], float] = {}
    for mercado in {m for _, m in chaves}:
        coluna = EEJ.golos_marcados if mercado == "golos" else _COLUNA.get(mercado)
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
    sem_dados: int = 0     # Sofascore sem estatisticas do jogo (nem ganha nem perdida)
    lucro: float = 0.0
    n_com_lucro: int = 0
    n_odd_real: int = 0   # dicas resolvidas medidas a odd real do mercado
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
    odd = _float(r.get("odd_real")) or _float(r.get("odd"))
    if odd is None or odd <= 1.0:
        return ("W" if ganhou else "L"), None
    return ("W", odd - 1.0) if ganhou else ("L", -1.0)


@dataclass
class DicaEnviada:
    ts: datetime
    tipo: str               # "alerta" | "projeccao"
    event_id: int | None
    mercado: str            # nome com icone, ex "🚩 cantos"
    lado: str
    linha: str
    odd: float | None
    resultado: str          # W | L | push | pendente
    lucro: float | None     # a odd real quando havia, senao a odd indicada
    home: str = ""
    away: str = ""
    odd_real: float | None = None

    @property
    def grupo(self) -> str:
        sufixo = {"projeccao": " (projecção)", "cartoes_fim": " (cartão no fim)",
                  "mais_golos": " (mais golos)"}
        return f"{self.mercado} {self.lado}" + sufixo.get(self.tipo, "")


def listar(inicio: datetime, fim: datetime, csv_sugestoes: Path,
           csv_enviados: Path | None = None,
           totais_proj: dict[tuple[int, str], float] | None = None,
           enviados: list[dict] | None = None) -> list[DicaEnviada]:
    """Cada dica enviada em [inicio, fim) com o seu resultado, por ordem de hora."""
    if enviados is None:
        enviados = carregar(csv_enviados)
    no_periodo = [r for r in enviados if inicio <= r["ts"] < fim]

    # Alertas: resultado do CSV de sugestoes (1 linha por alert_id)
    ids = {(r.get("alert_id") or "").strip() for r in no_periodo if r["tipo"] == "alerta"}
    ids.discard("")
    linhas_csv: dict[str, dict] = {}
    if ids and csv_sugestoes.exists():
        with csv_sugestoes.open(encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                aid = (row.get("alert_id") or "").strip()
                if aid in ids and aid not in linhas_csv:
                    linhas_csv[aid] = row

    dicas: list[DicaEnviada] = []
    vistos: set[str] = set()
    for r in no_periodo:
        if r["tipo"] == "alerta":
            aid = (r.get("alert_id") or "").strip()
            if not aid or aid in vistos:
                continue
            vistos.add(aid)
            row = linhas_csv.get(aid)
            if row is None:
                dicas.append(DicaEnviada(ts=r["ts"], tipo="alerta", event_id=None, mercado="?",
                                         lado="", linha="", odd=None, resultado="pendente",
                                         lucro=None))
                continue
            m = (row.get("mercado") or "?").strip().lower()
            resultado = (row.get("resultado") or "").strip() or "pendente"
            odd_real = _float(r.get("odd_real"))
            lucro = _float(row.get("lucro"))
            if odd_real and resultado in ("W", "L", "push"):
                lucro = {"W": odd_real - 1.0, "L": -1.0, "push": 0.0}[resultado]
            dicas.append(DicaEnviada(
                ts=r["ts"], tipo="alerta", event_id=_int(row.get("event_id")),
                mercado=_NOME_MERCADO.get(m, m), lado=(row.get("lado") or "").strip().lower(),
                linha=(row.get("linha") or "").strip(), odd=odd_real or _float(row.get("odd")),
                resultado=resultado, lucro=lucro,
                home=row.get("home") or "", away=row.get("away") or "", odd_real=odd_real))
        else:  # projeccao, cartoes_fim, mais_golos: resolvidos pelo total final na BD
            m = (r.get("mercado") or "?").lower()
            res, lucro = _resultado_projeccao(r, totais_proj or {})
            dicas.append(DicaEnviada(
                odd_real=_float(r.get("odd_real")),
                ts=r["ts"], tipo=r["tipo"], event_id=_int(r.get("event_id")),
                mercado=_NOME_MERCADO.get(m, m), lado=(r.get("lado") or "").lower(),
                linha=(r.get("linha") or "").strip(), odd=_float(r.get("odd_real")) or _float(r.get("odd")),
                resultado=res, lucro=lucro))
    dicas.sort(key=lambda d: d.ts)
    return dicas


def resumir(inicio: datetime, fim: datetime, csv_sugestoes: Path,
            csv_enviados: Path | None = None,
            totais_proj: dict[tuple[int, str], float] | None = None) -> ResumoEnviados:
    enviados = carregar(csv_enviados)
    rel = ResumoEnviados(inicio=inicio, fim=fim,
                         registo_desde=min(r["ts"] for r in enviados) if enviados else None)
    mercados: dict[str, LinhaMercado] = {}
    for d in listar(inicio, fim, csv_sugestoes, totais_proj=totais_proj, enviados=enviados):
        rel.n += 1
        if d.tipo == "projeccao":
            rel.n_projeccoes += 1
        if d.odd_real and d.resultado in ("W", "L", "push"):
            rel.n_odd_real += 1
        lm = mercados.setdefault(d.grupo, LinhaMercado(nome=d.grupo))
        if d.resultado == "W":
            rel.vitorias += 1
            lm.vitorias += 1
        elif d.resultado == "L":
            rel.derrotas += 1
            lm.derrotas += 1
        elif d.resultado == "push":
            rel.push += 1
        elif d.resultado == "SD":
            rel.sem_dados += 1
            continue
        else:
            rel.pendentes += 1
            continue
        if d.lucro is not None:
            rel.lucro += d.lucro
            rel.n_com_lucro += 1
            lm.lucro += d.lucro
    rel.por_mercado = sorted(mercados.values(),
                             key=lambda l: (-(l.vitorias + l.derrotas), l.nome))
    return rel


_ICONE_RESULTADO = {"W": "✅", "L": "❌", "push": "↩", "pendente": "⏳", "SD": "∅"}


def formatar_lista(dicas: list[DicaEnviada],
                   nomes: dict[int, tuple[str, str]] | None = None) -> str:
    """Uma linha por dica: hora, jogo, aposta, resultado."""
    linhas = []
    for d in dicas:
        home, away = d.home, d.away
        if (not home or not away) and nomes and d.event_id in nomes:
            home, away = nomes[d.event_id]
        jogo = f"{home} - {away}" if home and away else f"jogo {d.event_id or '?'}"
        aposta = f"{d.mercado} {d.lado.upper()} {d.linha}".strip()
        if d.odd:
            aposta += f" @{d.odd:.2f}"
        if d.tipo == "projeccao":
            aposta += " (proj.)"
        elif d.tipo == "cartoes_fim":
            aposta += " (fim)"
        elif d.tipo == "mais_golos":
            aposta += " (mais golos)"
        fim = f" → {d.lucro:+.2f}" if d.lucro is not None and d.resultado != "pendente" else ""
        linhas.append(f"{_ICONE_RESULTADO.get(d.resultado, '⏳')} {d.ts:%H:%M} {jogo} · {aposta}{fim}")
    return "\n".join(linhas)


def formatar(rel: ResumoEnviados) -> str:
    linhas = ["*📲 Dicas enviadas ao Telegram*"]
    if rel.n == 0:
        if rel.registo_desde is None or rel.registo_desde >= rel.fim:
            linhas.append("  _O registo das dicas enviadas começou agora; "
                          "o próximo relatório já as conta._")
        else:
            linhas.append("  Nenhuma dica enviada neste período.")
        return "\n".join(linhas)
    if rel.registo_desde and rel.registo_desde > rel.inicio:
        linhas.append(f"  _(registo desde {rel.registo_desde:%d/%m %H:%M})_")
    decididas = rel.vitorias + rel.derrotas
    taxa = f"{rel.vitorias / decididas * 100:.0f}%" if decididas else "—"
    linhas.append(
        f"  {rel.n} dicas ({rel.n - rel.n_projeccoes} alertas · {rel.n_projeccoes} projecções)"
        f" · {rel.vitorias}✅ {rel.derrotas}❌"
        + (f" {rel.push}↩" if rel.push else "")
        + (f" · {rel.pendentes} por resolver" if rel.pendentes else "")
        + (f" · {rel.sem_dados} sem dados no Sofascore" if rel.sem_dados else ""))
    sinal = "+" if rel.lucro >= 0 else ""
    linhas.append(f"  Acerto {taxa} · lucro *{sinal}{rel.lucro:.2f} un*"
                  + (f" · ROI *{rel.roi:+.1f}%*" if rel.roi is not None else ""))
    for lm in rel.por_mercado[:10]:
        if lm.vitorias + lm.derrotas == 0:
            continue
        icone = "🟢" if lm.lucro > 0 else ("🔴" if lm.lucro < 0 else "⚪")
        linhas.append(f"  {icone} {lm.nome}: {lm.vitorias}/{lm.vitorias + lm.derrotas}"
                      f" · {lm.lucro:+.2f} un")
    if rel.n_odd_real:
        linhas.append(f"  _1 un por dica, à odd real do mercado em {rel.n_odd_real} de "
                      f"{rel.vitorias + rel.derrotas + rel.push} (as outras à odd indicada)._")
    else:
        linhas.append("  _1 un por dica, à odd indicada na mensagem._")
    return "\n".join(linhas)
