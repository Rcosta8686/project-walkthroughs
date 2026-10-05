"""Cross-validation / grid search para hiperparâmetros do modelo GAP.

Corre o backtest walk-forward sobre a época de validação para cada
combinação de ``peso_cantos`` e ``rho_dixon_coles``, e devolve as
métricas-chave (ROI, log-loss, Brier) para cada combo.
"""

from __future__ import annotations

from dataclasses import dataclass

from apostas.backtest import walk_forward
from apostas.backtest.calibracao import brier_score, log_loss
from apostas.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ResultadoCV:
    peso_cantos: float
    rho_dixon_coles: float
    n_apostas: int
    roi: float
    lucro: float
    brier: float
    log_loss: float
    max_drawdown: float


def grid_search(
    epoca_teste: int = 2024,
    pesos_cantos: list[float] | None = None,
    rhos: list[float] | None = None,
    linha: float = 2.5,
    ev_minimo: float = 0.03,
    meia_vida_dias: float = 365.0,
) -> list[ResultadoCV]:
    """Testa cada (peso_cantos, rho) na época e devolve resultados ordenados por ROI."""
    pesos = pesos_cantos or [0.1, 0.3, 0.5, 0.7, 1.0]
    rhos_l = rhos or [0.0, 0.05, 0.10, 0.15]

    resultados: list[ResultadoCV] = []
    for peso in pesos:
        for rho in rhos_l:
            rel = walk_forward.correr(
                epoca_teste=epoca_teste,
                linha=linha,
                ev_minimo=ev_minimo,
                meia_vida_dias=meia_vida_dias,
                modelo="gap",
                peso_cantos=peso,
                rho_dixon_coles=rho,
            )
            resultados.append(ResultadoCV(
                peso_cantos=peso,
                rho_dixon_coles=rho,
                n_apostas=rel.n_apostas,
                roi=rel.roi,
                lucro=rel.lucro,
                brier=brier_score(rel.apostas),
                log_loss=log_loss(rel.apostas),
                max_drawdown=rel.max_drawdown,
            ))
            log.info(
                "CV: peso=%.2f rho=%.2f → n=%d, ROI=%+.2f%%, log-loss=%.3f",
                peso, rho, rel.n_apostas, rel.roi * 100, resultados[-1].log_loss,
            )

    resultados.sort(key=lambda r: r.roi, reverse=True)
    return resultados


def formatar_tabela(resultados: list[ResultadoCV]) -> str:
    """Imprime tabela ordenada por ROI para o terminal."""
    linhas = [
        "═" * 80,
        "  CROSS-VALIDATION — GAP",
        "═" * 80,
        "",
        f"  {'peso':>6} {'rho':>6} {'n':>5} {'ROI':>8} {'lucro':>8} {'brier':>7} {'logloss':>8} {'maxDD':>7}",
        "  " + "─" * 60,
    ]
    for r in resultados:
        linhas.append(
            f"  {r.peso_cantos:6.2f} {r.rho_dixon_coles:6.2f} "
            f"{r.n_apostas:5d} {r.roi * 100:+7.2f}% "
            f"{r.lucro:+8.2f} {r.brier:7.4f} {r.log_loss:8.4f} "
            f"{r.max_drawdown:7.2f}"
        )
    linhas.append("")
    if resultados:
        best = resultados[0]
        linhas.append(
            f"  Melhor combo: peso_cantos={best.peso_cantos:.2f}, "
            f"rho={best.rho_dixon_coles:.2f} → ROI={best.roi * 100:+.2f}%"
        )
    linhas.append("═" * 80)
    return "\n".join(linhas)
