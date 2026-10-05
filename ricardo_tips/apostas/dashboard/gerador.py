"""Gerador do dashboard HTML a partir de RelatorioBacktest.

Produz um ficheiro ``index.html`` autocontido (um só HTML, uma só CSS
embutida, Plotly carregado via CDN). O utilizador abre com duplo-clique.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from apostas.backtest.walk_forward import ApostaSimulada, RelatorioBacktest
from apostas.utils.config import project_root
from apostas.utils.logger import get_logger

log = get_logger(__name__)

# Cores por relatório (até 4 modelos lado-a-lado)
_CORES_LINHA = ["#2563eb", "#059669", "#dc2626", "#d97706"]
_CORES_FILL = [
    "rgba(37,99,235,0.12)",
    "rgba(5,150,105,0.12)",
    "rgba(220,38,38,0.12)",
    "rgba(217,119,6,0.12)",
]


@dataclass
class _ResumoLiga:
    n: int
    roi: float


def _enriquecer(rel: RelatorioBacktest) -> dict:
    """Converte um RelatorioBacktest + apostas numa estrutura amiga do template."""
    bankroll_x = [p[0].strftime("%Y-%m-%d") for p in rel.bankroll]
    bankroll_y = [round(p[1], 2) for p in rel.bankroll]

    por_liga: dict[int, _ResumoLiga] = {}
    agrupadas: dict[int, list[ApostaSimulada]] = defaultdict(list)
    for a in rel.apostas:
        agrupadas[a.liga_id].append(a)
    for lid, apostas in sorted(agrupadas.items()):
        stake = sum(a.stake for a in apostas)
        lucro = sum(a.lucro for a in apostas)
        por_liga[lid] = _ResumoLiga(n=len(apostas), roi=lucro / stake if stake else 0)

    return {
        "modelo": rel.modelo,
        "linha": rel.linha,
        "epoca_teste": rel.epoca_teste,
        "casa_de_apostas_ref": rel.casa_de_apostas_ref,
        "ev_minimo": rel.ev_minimo,
        "n_apostas": rel.n_apostas,
        "hit_rate": rel.hit_rate,
        "lucro": rel.lucro,
        "roi": rel.roi,
        "max_drawdown": rel.max_drawdown,
        "bankroll_x": bankroll_x,
        "bankroll_y": bankroll_y,
        "por_liga": por_liga,
        "apostas": sorted(rel.apostas, key=lambda a: a.data),
    }


def gerar(
    relatorios: Iterable[RelatorioBacktest],
    destino: Path | None = None,
) -> Path:
    """Renderiza o dashboard HTML. Devolve o caminho do ficheiro gerado."""
    rels = list(relatorios)
    if not rels:
        raise ValueError("Preciso de pelo menos 1 relatório para gerar o dashboard.")

    pasta_templates = Path(__file__).resolve().parent / "templates"
    env = Environment(
        loader=FileSystemLoader(pasta_templates),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("index.html.j2")

    css = (pasta_templates / "estilo.css").read_text(encoding="utf-8")

    html = template.render(
        relatorios=[_enriquecer(r) for r in rels],
        data_gerado=datetime.now().strftime("%Y-%m-%d %H:%M"),
        css=css,
        cores=_CORES_LINHA[: len(rels)],
        cores_fill=_CORES_FILL[: len(rels)],
    )

    destino = destino or (project_root() / "apostas" / "dashboard" / "output" / "index.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    log.info("Dashboard gerado em %s (%d modelo(s))", destino, len(rels))
    return destino
