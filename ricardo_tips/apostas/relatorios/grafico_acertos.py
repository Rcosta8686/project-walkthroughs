"""Grafico do que as dicas enviadas ao Telegram acertam e falham: por dia,
por semana e por mes.

Em cima, uma barra por dia (ou semana, ou mes): verde = acertou, vermelho =
falhou, e a percentagem de acerto por cima; a linha azul e o lucro acumulado
(1 unidade por dica, a odd real quando havia, como no relatorio do mes). Em
baixo, o acerto de cada mercado no periodo todo: o que esta a dar e o que
esta a falhar.

As dicas sao as mesmas do relatorio do dia/mes (alertas_enviados): alertas,
projeccoes e o cartao no fim. Sem estatisticas no Sofascore (SD) nao contam.
Horas e dias em UTC, como nos outros relatorios.

Sem o matplotlib instalado faz o mesmo em texto, com quadrados.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from apostas.relatorios import alertas_enviados
from apostas.relatorios.alertas_enviados import DicaEnviada
from apostas.utils.config import project_root
from apostas.utils.tempo import agora_utc

PERIODOS = {"dia": 30, "semana": 12, "mes": 12}   # quantas barras
_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_MESES_LONGOS = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro"]
_NOME_PERIODO = {"dia": "por dia", "semana": "por semana", "mes": "por mes"}
VERDE, VERMELHO, AZUL, CINZA = "#2e9e5b", "#d64545", "#1f4e79", "#8a8a8a"


@dataclass
class Balde:
    inicio: datetime
    fim: datetime
    rotulo: str
    acertos: int = 0
    falhas: int = 0
    nulas: int = 0
    pendentes: int = 0
    lucro: float = 0.0
    primeira: datetime | None = None   # hora da primeira dica nesta barra

    @property
    def decididas(self) -> int:
        return self.acertos + self.falhas

    @property
    def taxa(self) -> float | None:
        return self.acertos / self.decididas if self.decididas else None


@dataclass
class Mercado:
    nome: str
    acertos: int = 0
    falhas: int = 0
    lucro: float = 0.0

    @property
    def decididas(self) -> int:
        return self.acertos + self.falhas


def inicio_do_balde(ts: datetime, periodo: str) -> datetime:
    d = datetime(ts.year, ts.month, ts.day)
    if periodo == "semana":
        return d - timedelta(days=d.weekday())          # segunda-feira
    if periodo == "mes":
        return datetime(ts.year, ts.month, 1)
    return d


def _seguinte(inicio: datetime, periodo: str) -> datetime:
    if periodo == "semana":
        return inicio + timedelta(days=7)
    if periodo == "mes":
        return datetime(inicio.year + inicio.month // 12, inicio.month % 12 + 1, 1)
    return inicio + timedelta(days=1)


def _anterior(inicio: datetime, periodo: str) -> datetime:
    if periodo == "mes":
        return datetime(inicio.year - (inicio.month == 1), (inicio.month - 2) % 12 + 1, 1)
    return inicio - (timedelta(days=7) if periodo == "semana" else timedelta(days=1))


def _rotulo(inicio: datetime, periodo: str) -> str:
    """Sem ambiguidades: 'nov 2026' (nao 'nov 26', que parece 26 de novembro)."""
    if periodo == "mes":
        return f"{_MESES[inicio.month - 1]} {inicio:%Y}"
    if periodo == "semana":
        return f"{inicio:%d/%m}–{inicio + timedelta(days=6):%d/%m}"
    return f"{inicio:%d/%m}"


def baldes_vazios(periodo: str, ate: datetime, n: int | None = None) -> list[Balde]:
    """As n barras que acabam antes de `ate` (a ultima e a que contem ate - 1s)."""
    n = n or PERIODOS[periodo]
    inicios = [inicio_do_balde(ate - timedelta(seconds=1), periodo)]
    while len(inicios) < n:
        inicios.insert(0, _anterior(inicios[0], periodo))
    return [Balde(i, _seguinte(i, periodo), _rotulo(i, periodo)) for i in inicios]


_EMOJI = re.compile("[☀-➿\U0001f000-\U0001ffff️‍]")


def nome_limpo(grupo: str) -> str:
    """'🚩 cantos over (projecção)' → 'Cantos over (projecção)' (o grafico nao tem emojis)."""
    s = _EMOJI.sub("", grupo).replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def agrupar(dicas: list[DicaEnviada], periodo: str, ate: datetime,
            n: int | None = None) -> tuple[list[Balde], list[Mercado]]:
    baldes = baldes_vazios(periodo, ate, n)
    por_inicio = {b.inicio: b for b in baldes}
    primeiro = baldes[0].inicio
    mercados: dict[str, Mercado] = {}
    for d in dicas:
        if not primeiro <= d.ts < ate:
            continue
        b = por_inicio.get(inicio_do_balde(d.ts, periodo))
        if b is None or d.resultado not in ("W", "L", "push", "pendente"):
            continue                                  # SD: sem estatisticas
        if b.primeira is None or d.ts < b.primeira:
            b.primeira = d.ts
        if d.resultado == "pendente":
            b.pendentes += 1
            continue
        if d.resultado == "push":
            b.nulas += 1
            continue
        m = mercados.setdefault(d.grupo, Mercado(nome_limpo(d.grupo)))
        if d.resultado == "W":
            b.acertos += 1
            m.acertos += 1
        else:
            b.falhas += 1
            m.falhas += 1
        if d.lucro is not None:
            b.lucro += d.lucro
            m.lucro += d.lucro
    ordem = sorted(mercados.values(), key=lambda m: (-m.decididas, m.nome))
    # Antes da primeira dica registada nao ha nada a mostrar: o grafico comeca ai.
    while len(baldes) > 1 and not (baldes[0].decididas or baldes[0].nulas
                                   or baldes[0].pendentes):
        baldes.pop(0)
    return baldes, ordem


def dicas_do_periodo(inicio: datetime, fim: datetime, csv_sugestoes: Path | None = None,
                     csv_enviados: Path | None = None) -> list[DicaEnviada]:
    """Dicas enviadas em [inicio, fim) com o resultado (projeccoes pela BD)."""
    csv_sug = csv_sugestoes or project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    chaves = alertas_enviados.projeccoes_no_periodo(inicio, fim, csv_enviados)
    totais: dict = {}
    if chaves:
        try:
            from apostas.utils.db import abrir_sessao
            with abrir_sessao() as s:
                totais = alertas_enviados.totais_finais_bd(s, chaves)
        except Exception:  # noqa: BLE001 — sem BD as projeccoes ficam por resolver
            totais = {}
    return alertas_enviados.listar(inicio, fim, csv_sug, csv_enviados, totais_proj=totais)


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _nome_ultimo(b: Balde, periodo: str, agora: datetime) -> str:
    em_curso = b.inicio <= agora < b.fim
    if periodo == "mes":
        nome = f"{_MESES_LONGOS[b.inicio.month - 1]} {b.inicio:%Y}"
        return f"{nome} (ate agora)" if em_curso else nome
    if periodo == "semana":
        datas = f"{b.inicio:%d/%m}–{b.fim - timedelta(days=1):%d/%m/%Y}"
        return f"esta semana ({datas}, ate agora)" if em_curso else f"semana {datas}"
    if em_curso:
        return f"hoje ({b.inicio:%d/%m/%Y}, ate agora)"
    if b.fim <= agora < b.fim + timedelta(days=1):
        return f"ontem ({b.inicio:%d/%m/%Y})"
    return f"dia {b.inicio:%d/%m/%Y}"


def datas(baldes: list[Balde], agora: datetime) -> str:
    """'09/10/2026 a 07/11/2026': do primeiro dia do grafico ao ultimo ja vivido."""
    inicio = baldes[0].primeira or baldes[0].inicio      # a primeira dica, nao o dia 1
    ultimo = min(baldes[-1].fim - timedelta(days=1), datetime(agora.year, agora.month, agora.day))
    return f"{inicio:%d/%m/%Y} a {max(ultimo, inicio):%d/%m/%Y}"


def _soma(baldes: list[Balde]) -> Balde:
    t = Balde(baldes[0].inicio, baldes[-1].fim, "total")
    for b in baldes:
        t.acertos += b.acertos
        t.falhas += b.falhas
        t.nulas += b.nulas
        t.pendentes += b.pendentes
        t.lucro += b.lucro
    return t


def _linha_resumo(b: Balde) -> str:
    extra = f" · {b.pendentes} por resolver" if b.pendentes else ""
    return (f"{b.acertos}✅ {b.falhas}❌ · acerto {_pct(b.taxa)} · "
            f"lucro {b.lucro:+.2f} un{extra}")


def legenda(baldes: list[Balde], mercados: list[Mercado], periodo: str,
            agora: datetime | None = None) -> str:
    """Texto curto que vai com o grafico (Telegram: max. 1024 letras)."""
    agora = agora or agora_utc()
    ultimo, total = baldes[-1], _soma(baldes)
    nome = _nome_ultimo(ultimo, periodo, agora)
    linhas = [f"*📊 Dicas enviadas {_NOME_PERIODO[periodo]}*",
              f"*{nome[:1].upper() + nome[1:]}:* {_linha_resumo(ultimo)}",
              f"*{datas(baldes, agora)}:* {_linha_resumo(total)}"]
    com_volume = [m for m in mercados if m.decididas >= 5]
    if com_volume:
        melhor = max(com_volume, key=lambda m: (m.lucro, m.acertos / m.decididas))
        pior = min(com_volume, key=lambda m: (m.lucro, m.acertos / m.decididas))
        linhas.append(f"🟢 Melhor: {melhor.nome} {melhor.acertos}/{melhor.decididas} · "
                      f"{melhor.lucro:+.2f} un")
        if pior is not melhor and pior.lucro < 0:
            linhas.append(f"🔴 Pior: {pior.nome} {pior.acertos}/{pior.decididas} · "
                          f"{pior.lucro:+.2f} un")
    texto = "\n".join(linhas)
    return texto[:1020]


def desenhar(baldes: list[Balde], mercados: list[Mercado], periodo: str,
             max_mercados: int = 10, agora: datetime | None = None) -> bytes:
    """PNG com as barras (acertos/falhas + lucro acumulado) e os mercados."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    mercados = mercados[:max_mercados]
    altura_m = max(1.8, 0.42 * len(mercados) + 0.8)
    fig = plt.figure(figsize=(10, 4.6 + altura_m), dpi=110)
    gs = fig.add_gridspec(2, 1, height_ratios=[4.6, altura_m], hspace=0.55, top=0.88)
    ax = fig.add_subplot(gs[0])
    x = list(range(len(baldes)))
    ac = [b.acertos for b in baldes]
    fa = [b.falhas for b in baldes]
    ax.bar(x, ac, color=VERDE, label="acertou", width=0.75)
    ax.bar(x, fa, bottom=ac, color=VERMELHO, label="falhou", width=0.75)
    topo = max([b.decididas for b in baldes] + [1])
    for i, b in enumerate(baldes):
        if b.taxa is not None:
            ax.text(i, b.decididas + topo * 0.02, _pct(b.taxa), ha="center", va="bottom",
                    fontsize=6 if len(baldes) > 16 else 9, color="#333333",
                    rotation=90 if len(baldes) > 16 else 0)
    ax.set_ylim(0, topo * (1.28 if len(baldes) > 16 else 1.18))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xticks(x)
    inclinar = len(baldes) > 16 or (periodo == "semana" and len(baldes) > 6)
    ax.set_xticklabels([b.rotulo for b in baldes], rotation=60 if inclinar else 0,
                       ha="right" if inclinar else "center",
                       fontsize=7 if len(baldes) > 16 else 9)
    ax.set_ylabel("dicas decididas")
    ax.spines[["top"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)

    ax2 = ax.twinx()
    acumulado, soma = [], 0.0
    for b in baldes:
        soma += b.lucro
        acumulado.append(soma)
    ax2.plot(x, acumulado, color=AZUL, marker="o", markersize=3, linewidth=2,
             label="lucro acumulado (un)")
    ax2.axhline(0, color=AZUL, alpha=0.3, linewidth=1, linestyle="--")
    ax2.set_ylabel("lucro acumulado (1 un por dica)", color=AZUL)
    ax2.tick_params(axis="y", colors=AZUL)
    ax2.spines[["top"]].set_visible(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8,
              frameon=False, ncol=3)

    total = _soma(baldes)
    fig.suptitle(f"Dicas enviadas ao Telegram {_NOME_PERIODO[periodo]}", fontsize=15,
                 fontweight="bold", y=0.99)
    fig.text(0.5, 0.945,
             f"{datas(baldes, agora or agora_utc())}: {total.acertos} acertos, "
             f"{total.falhas} falhas · acerto {_pct(total.taxa)} · lucro {total.lucro:+.2f} un"
             + (f" · {total.pendentes} por resolver" if total.pendentes else ""),
             ha="center", fontsize=10, color="#333333")

    axm = fig.add_subplot(gs[1])
    if mercados:
        y = list(range(len(mercados)))
        mac = [m.acertos for m in mercados]
        mfa = [m.falhas for m in mercados]
        axm.barh(y, mac, color=VERDE, height=0.65)
        axm.barh(y, mfa, left=mac, color=VERMELHO, height=0.65)
        largura = max(m.decididas for m in mercados)
        for i, m in enumerate(mercados):
            axm.text(m.decididas + largura * 0.01, i,
                     f" {m.acertos}/{m.decididas} ({_pct(m.acertos / m.decididas)}) · "
                     f"{m.lucro:+.2f} un", va="center", fontsize=8,
                     color=VERDE if m.lucro > 0 else (VERMELHO if m.lucro < 0 else CINZA))
        axm.set_yticks(y)
        axm.set_yticklabels([m.nome for m in mercados], fontsize=9)
        axm.invert_yaxis()
        axm.xaxis.set_major_locator(MaxNLocator(integer=True))
        axm.set_xlim(0, largura * 1.45)
    else:
        axm.text(0.5, 0.5, "Ainda sem dicas decididas neste periodo", ha="center",
                 va="center", transform=axm.transAxes, color=CINZA)
        axm.set_yticks([])
    axm.set_title("Por mercado (periodo todo)", fontsize=11, loc="left")
    axm.spines[["top", "right"]].set_visible(False)
    axm.set_xlabel("dicas decididas")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def texto(baldes: list[Balde], mercados: list[Mercado], periodo: str,
          agora: datetime | None = None) -> str:
    """O mesmo grafico em texto (sem matplotlib): 5 quadrados por barra."""
    linhas = [legenda(baldes, mercados, periodo, agora), ""]
    for b in baldes:
        if not b.decididas:
            continue
        verdes = round((b.taxa or 0) * 5)
        linhas.append(f"`{b.rotulo:>6}` {'🟩' * verdes}{'🟥' * (5 - verdes)} "
                      f"{b.acertos}/{b.decididas} · {b.lucro:+.2f}")
    if mercados:
        linhas.append("")
        for m in mercados[:10]:
            linhas.append(f"{'🟢' if m.lucro > 0 else ('🔴' if m.lucro < 0 else '⚪')} "
                          f"{m.nome}: {m.acertos}/{m.decididas} · {m.lucro:+.2f} un")
    return "\n".join(linhas)


def tem_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class Grafico:
    periodo: str
    baldes: list[Balde]
    mercados: list[Mercado]
    png: bytes | None        # None sem matplotlib
    legenda: str
    texto: str

    @property
    def vazio(self) -> bool:
        return _soma(self.baldes).decididas == 0


def gerar(periodo: str, ate: datetime, n: int | None = None,
          dicas: list[DicaEnviada] | None = None, agora: datetime | None = None,
          **kw) -> Grafico:
    agora = agora or agora_utc()
    vazios = baldes_vazios(periodo, ate, n)
    if dicas is None:
        dicas = dicas_do_periodo(vazios[0].inicio, ate, **kw)
    baldes, mercados = agrupar(dicas, periodo, ate, n)
    png = desenhar(baldes, mercados, periodo, agora=agora) if tem_matplotlib() else None
    return Grafico(periodo, baldes, mercados, png, legenda(baldes, mercados, periodo, agora),
                   texto(baldes, mercados, periodo, agora))


def periodos_da_noite(hoje: datetime) -> list[str]:
    """Todas as noites o dia; a segunda a semana que acabou; dia 1 o mes que acabou."""
    out = ["dia"]
    if hoje.weekday() == 0:
        out.append("semana")
    if hoje.day == 1:
        out.append("mes")
    return out


def caminho_png(periodo: str) -> Path:
    return project_root() / "dados" / "graficos" / f"acertos_{periodo}.png"


def gravar_png(png: bytes | None, nome: str) -> Path | None:
    if png is None:
        return None
    p = caminho_png(nome)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(png)
    return p


def gravar(g: Grafico) -> Path | None:
    return gravar_png(g.png, g.periodo)


def enviar(g) -> None:
    """Envia um grafico (este ou o da precisao: png, legenda, texto)."""
    from apostas.alertas.telegram_cliente import cliente
    c = cliente()
    if g.png is not None:
        c.enviar_foto(g.png, g.legenda)
    else:
        c.enviar(g.texto + "\n\n_(para ver o grafico: venv\\Scripts\\pip install matplotlib)_")


def _marca() -> Path:
    return project_root() / "dados" / "tracking" / ".graficos_enviados"


def ja_enviado(periodo: str, ate: datetime) -> bool:
    p = _marca()
    chave = f"{periodo} {ate:%Y-%m-%d}"
    try:
        return chave in p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False


def marcar_enviado(periodo: str, ate: datetime) -> None:
    p = _marca()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fp:
        fp.write(f"{periodo} {ate:%Y-%m-%d}\n")
