"""Calibracao dos alertas live: o modelo acerta tanto quanto promete?

Compara a probabilidade prevista (`prob_modelo` no CSV) com a taxa real de
acerto dos alertas ja resolvidos (W/L; push ignorado). Um modelo calibrado
que diz 70% acerta ~70% das vezes. Se diz 75% e acerta 58%, sobrestima e
as odds "justas" que calcula sao enganadoras.

O veredicto usa margem estatistica (1.96 desvios padrao binomiais), por isso
com poucos dados nao acusa nada: diz "poucos dados".
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FAIXAS_DEFAULT: tuple[tuple[float, float], ...] = (
    (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01),
)
MIN_N_VEREDICTO = 30

_NOME_MERCADO = {
    "golos": "⚽ golos", "cartoes": "🟨 cartoes", "cards": "🟨 cartoes",
    "cantos": "🚩 cantos", "remates": "💥 remates",
    "remates_a_baliza": "🎯 remates a baliza", "sot": "🎯 remates a baliza",
    "btts": "🔃 BTTS", "1x2": "🏆 1X2",
}
_MERCADO_CANONICO = {"cards": "cartoes", "sot": "remates_a_baliza"}


@dataclass
class LinhaCalibracao:
    grupo: str
    n: int
    prob_media: float      # 0-1, o que o modelo previu em media
    taxa_real: float       # 0-1, fraccao de W em W+L
    roi: float | None      # lucro medio por aposta de stake 1 (0.08 = +8%)

    @property
    def desvio(self) -> float:
        """Positivo = acerta mais do que promete; negativo = sobrestima."""
        return self.taxa_real - self.prob_media

    @property
    def margem(self) -> float:
        p = min(max(self.prob_media, 0.01), 0.99)
        return 1.96 * math.sqrt(p * (1 - p) / self.n)

    @property
    def veredicto(self) -> str:
        if self.n < MIN_N_VEREDICTO:
            return "poucos dados"
        if self.desvio < -self.margem:
            return "sobrestima"
        if self.desvio > self.margem:
            return "subestima"
        return "calibrado"


def _float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN


def carregar_resolvidos(csv_path: Path, desde: datetime | None = None) -> list[dict]:
    """Linhas W/L com prob_modelo valida. Push e pendentes ficam de fora."""
    if not csv_path.exists():
        return []
    out: list[dict] = []
    with csv_path.open(encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            res = (row.get("resultado") or "").strip()
            if res not in ("W", "L"):
                continue
            prob = _float(row.get("prob_modelo"))
            if prob is None or not 0.0 < prob < 1.0:
                continue
            ts_str = (row.get("ts_scan") or "")[:19]
            if desde is not None:
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts < desde:
                    continue
            mercado = (row.get("mercado") or "").strip().lower()
            casa = (row.get("casa") or "").strip().upper()
            out.append({
                "mercado": _MERCADO_CANONICO.get(mercado, mercado),
                "lado": (row.get("lado") or "").strip().lower(),
                "prob": prob,
                "ganhou": res == "W",
                "lucro": _float(row.get("lucro")),
                "fonte": "analista" if casa == "ANALISTA" else "odds",
                "ts": ts_str,
                "liga": (row.get("liga") or "").strip(),
            })
    return out


def _linha(grupo: str, rows: list[dict]) -> LinhaCalibracao:
    lucros = [r["lucro"] for r in rows if r["lucro"] is not None]
    return LinhaCalibracao(
        grupo=grupo,
        n=len(rows),
        prob_media=sum(r["prob"] for r in rows) / len(rows),
        taxa_real=sum(1 for r in rows if r["ganhou"]) / len(rows),
        roi=sum(lucros) / len(lucros) if lucros else None,
    )


def calibrar_por_faixa(
    rows: list[dict], faixas: tuple[tuple[float, float], ...] = FAIXAS_DEFAULT,
) -> list[LinhaCalibracao]:
    out = []
    for ini, fim in faixas:
        grupo = [r for r in rows if ini <= r["prob"] < fim]
        if grupo:
            out.append(_linha(f"{ini*100:.0f}-{min(fim, 1.0)*100:.0f}%", grupo))
    return out


def calibrar_por_mercado(rows: list[dict], min_n: int = 10) -> list[LinhaCalibracao]:
    grupos: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        grupos.setdefault((r["mercado"], r["lado"]), []).append(r)
    out = [
        _linha(f"{_NOME_MERCADO.get(m, m)} {lado}".strip(), g)
        for (m, lado), g in grupos.items() if len(g) >= min_n
    ]
    out.sort(key=lambda l: -l.n)
    return out


def brier(rows: list[dict]) -> float | None:
    """Erro quadratico medio da probabilidade. 0 = perfeito; ~0.25 = moeda ao ar."""
    if not rows:
        return None
    return sum((r["prob"] - (1.0 if r["ganhou"] else 0.0)) ** 2 for r in rows) / len(rows)


_ICONE = {"calibrado": "✓", "subestima": "✓+", "sobrestima": "✗", "poucos dados": "·"}


def _fmt_linha(l: LinhaCalibracao) -> str:
    roi = "" if l.roi is None else f" · ROI {l.roi*100:+.0f}%"
    extra = ""
    if l.veredicto == "sobrestima":
        extra = f" — promete {abs(l.desvio)*100:.0f}pp a mais"
    elif l.veredicto == "subestima":
        extra = f" — acerta {l.desvio*100:.0f}pp acima"
    return (f"  {_ICONE[l.veredicto]} {l.grupo}: prevê {l.prob_media*100:.0f}% · "
            f"acertou {l.taxa_real*100:.0f}%{roi} (n={l.n}){extra}")


def formatar_relatorio(rows: list[dict], min_n_mercado: int = 10) -> str:
    if not rows:
        return "📐 *Calibracao do modelo*\nAinda nao ha alertas resolvidos (W/L) no CSV."

    b = brier(rows)
    linhas = [
        f"📐 *Calibracao do modelo* — {len(rows)} alertas resolvidos",
        f"Brier {b:.3f} (0 = perfeito, 0.25 = moeda ao ar)",
        "",
        "*Por confianca prevista:*",
        *(_fmt_linha(l) for l in calibrar_por_faixa(rows)),
    ]

    mercados = calibrar_por_mercado(rows, min_n=min_n_mercado)
    if mercados:
        linhas += ["", f"*Por mercado* (min {min_n_mercado} alertas):",
                   *(_fmt_linha(l) for l in mercados)]

    confiar = [l.grupo for l in mercados
               if l.veredicto in ("calibrado", "subestima") and (l.roi or 0) > 0]
    # ROI <= -5% alinha com a blacklist do scanner; -2% em 45 alertas e ruido.
    cortar = [l.grupo for l in mercados
              if l.veredicto == "sobrestima"
              or (l.n >= MIN_N_VEREDICTO and l.roi is not None and l.roi <= -0.05)]
    linhas += ["", "*Conclusao:*"]
    linhas.append(f"  Confiar: {', '.join(confiar)}" if confiar
                  else "  Confiar: nenhum mercado ainda com prova estatistica")
    if cortar:
        linhas.append(f"  Cortar: {', '.join(cortar)}")

    from apostas.modelos.calibrador_mercado import avaliar_fora_da_amostra
    av = avaliar_fora_da_amostra(rows)
    if av is not None:
        linhas += [
            "",
            f"*Correccao automatica* (testada em {av['n_teste']} alertas recentes "
            "que o calibrador nao viu):",
            f"  Brier {av['brier_bruto']:.3f} → *{av['brier_cal']:.3f}*",
        ]
        for g in av["por_grupo"][:8]:
            nome = f"{_NOME_MERCADO.get(g['mercado'], g['mercado'])} {g['lado']}"
            linhas.append(
                f"  {nome}: prometia {g['prob_bruta']*100:.0f}% → corrigido "
                f"{g['prob_cal']*100:.0f}% · real {g['taxa_real']*100:.0f}% (n={g['n']})")

    from apostas.alertas import corte_mercados
    linhas += ["", corte_mercados.formatar(corte_mercados.calcular(rows))]

    linhas.append("_✓ calibrado · ✓+ acerta acima do prometido · ✗ sobrestima · · poucos dados_")
    return "\n".join(linhas)
