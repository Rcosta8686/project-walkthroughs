"""Grafico da curva de aprendizagem: as previsoes por equipa estao a ficar mais certas?

As linhas "Ate ao fim: Porto 10 (9-11) · Benfica 3 (2-4)" dos alertas sao
medidas todas as noites (precisao_equipas): em quantas equipas o numero final
ficou a 1 ou menos do previsto, nos momentos mais recentes de jogos acabados.

Um quadro por mercado, uma marca por noite:
  - linha cheia: o nosso acerto (dentro de ±1);
  - tracejado: o maximo possivel (mesmo sabendo o ritmo certo de cada equipa,
    o futebol tem acaso);
  - a faixa cinzenta entre as duas e o que ainda ha para aprender.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from apostas.modelos.precisao_equipas import MERCADOS, NOMES

CORES = {"golos": "#7b3fa0", "cantos": "#d9822b", "cartoes": "#c9a000",
         "remates": "#1f4e79", "remates_a_baliza": "#2e9e5b"}


@dataclass
class Ponto:
    data: date
    dentro1: float
    teto: float
    n: int


def series(historia: list[dict]) -> dict[str, list[Ponto]]:
    """historia (uma entrada por noite) → pontos por mercado, por ordem de data."""
    out: dict[str, list[Ponto]] = {}
    for h in historia:
        try:
            d = date.fromisoformat(str(h.get("data")))
        except ValueError:
            continue
        for m in MERCADOS:
            x = h.get(m)
            if not isinstance(x, dict) or not x.get("n"):
                continue
            out.setdefault(m, []).append(Ponto(d, float(x.get("dentro1") or 0),
                                               float(x.get("teto") or 0), int(x["n"])))
    for pts in out.values():
        pts.sort(key=lambda p: p.data)
    return out


def legenda(s: dict[str, list[Ponto]]) -> str:
    linhas = ["*📏 As previsoes por equipa estao a ficar mais certas?*",
              "_\"Ate ao fim: Porto 10, Benfica 3\": em quantas equipas o final ficou a ±1._"]
    if not s:
        return "\n".join(linhas + ["Ainda sem noites medidas."])
    todas = sorted({p.data for pts in s.values() for p in pts})
    linhas.append(f"_Noites de {todas[0]:%d/%m/%Y} a {todas[-1]:%d/%m/%Y}_")
    for m in MERCADOS:
        pts = s.get(m)
        if not pts:
            continue
        u = pts[-1]
        teto = f" (max. ~{u.teto * 100:.0f}%)" if u.teto else ""
        evol = ""
        if len(pts) >= 2:
            d = (u.dentro1 - pts[0].dentro1) * 100
            seta = "📈" if d >= 1 else ("📉" if d <= -1 else "➖")
            evol = f" · {seta} {d:+.0f}pp desde {pts[0].data:%d/%m}"
        linhas.append(f"{NOMES[m]}: *{u.dentro1 * 100:.0f}%*{teto}{evol}")
    return "\n".join(linhas)[:1020]


def texto(s: dict[str, list[Ponto]]) -> str:
    """Sem matplotlib: a legenda e as ultimas noites de cada mercado."""
    linhas = [legenda(s)]
    for m in MERCADOS:
        pts = s.get(m, [])[-7:]
        if pts:
            linhas.append(f"{NOMES[m]}: " + " · ".join(
                f"{p.data:%d/%m} {p.dentro1 * 100:.0f}%" for p in pts))
    return "\n".join(linhas)


def desenhar(s: dict[str, list[Ponto]]) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    fig, eixos = plt.subplots(3, 2, figsize=(10, 9.5), dpi=110, sharex=True)
    quadros = list(eixos.flat)
    todas = sorted({p.data for pts in s.values() for p in pts})
    for ax, m in zip(quadros, MERCADOS):
        pts = s.get(m, [])
        cor = CORES[m]
        ax.set_title(NOMES[m].capitalize() + (f" · {pts[-1].dentro1 * 100:.0f}%" if pts else ""),
                     fontsize=11, loc="left", color=cor, fontweight="bold")
        if pts:
            xs = [p.data for p in pts]
            acerto = [p.dentro1 * 100 for p in pts]
            teto = [p.teto * 100 if p.teto else None for p in pts]
            ax.plot(xs, acerto, color=cor, marker="o", markersize=4, linewidth=2,
                    label="nosso acerto (±1)")
            if any(t is not None for t in teto):
                tt = [t if t is not None else a for t, a in zip(teto, acerto)]
                ax.plot(xs, tt, color=cor, linestyle="--", linewidth=1.3, alpha=0.8,
                        label="maximo possivel")
                ax.fill_between(xs, acerto, tt, color="#bbbbbb", alpha=0.35,
                                label="falta aprender")
        else:
            ax.text(0.5, 0.5, "ainda sem dados", ha="center", va="center",
                    transform=ax.transAxes, color="#8a8a8a")
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(todas) // 7)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax.tick_params(axis="x", labelsize=8, rotation=45, labelbottom=True)
    # Ultimo quadro: a explicacao.
    info = quadros[-1]
    info.axis("off")
    h, l = quadros[0].get_legend_handles_labels()
    if h:
        info.legend(h, l, loc="upper left", frameon=False, fontsize=10)
    info.text(0.0, 0.35, "Previsao 'ate ao fim' de cada equipa\nnos alertas, medida todas as\n"
              "noites em jogos ja acabados.\nO objectivo: a linha cheia a\nencostar ao tracejado.",
              transform=info.transAxes, fontsize=9, color="#333333", va="top")
    titulo = "As previsoes por equipa estao a ficar mais certas?"
    fig.suptitle(titulo, fontsize=14, fontweight="bold", y=0.995)
    if todas:
        fig.text(0.5, 0.95, f"Uma marca por noite · {todas[0]:%d/%m/%Y} a {todas[-1]:%d/%m/%Y}",
                 ha="center", fontsize=10, color="#333333")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


@dataclass
class GraficoPrecisao:
    png: bytes | None
    legenda: str
    texto: str
    vazio: bool


def gerar(historia: list[dict] | None = None) -> GraficoPrecisao:
    from apostas.relatorios.grafico_acertos import tem_matplotlib
    if historia is None:
        from apostas.modelos import precisao_equipas
        historia = precisao_equipas.ler().historia
    s = series(historia)
    png = desenhar(s) if tem_matplotlib() else None
    return GraficoPrecisao(png, legenda(s), texto(s), not s)
