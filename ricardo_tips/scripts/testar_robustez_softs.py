"""Script: testar_robustez_softs.py

Corre múltiplos cenários do backtest Pinnacle→softs em dados reais
(2022, 2023, 2024) e produz uma tabela comparativa. Se o edge
persistir em TODAS as variantes, é real. Se desaparecer numa, suspeita.

Cenários testados:
  1. Base: todas as 6 ligas, Over/Under 2.5, EV ≥ 5%
  2. Só Premier League
  3. Só La Liga
  4. Só Serie A
  5. Só Ligue 1
  6. Só Bundesliga
  7. Só Liga Portugal
  8. Linha 3.5 (em vez de 2.5)
  9. EV ≥ 7% (mais restritivo)
 10. EV ≥ 10% (muito restritivo)
 11. Só over
 12. Só under

Uso:
    python scripts/testar_robustez_softs.py
    python scripts/testar_robustez_softs.py --epocas 2023,2024
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.backtest import backtest_softs  # noqa: E402


def _agregar(relatorios: list) -> tuple[int, int, float, float]:
    """Devolve (n_apostas, n_vitorias, stake_total, lucro_total)."""
    n = sum(r.n_apostas for r in relatorios)
    v = sum(r.n_vitorias for r in relatorios)
    s = sum(r.stake_total for r in relatorios)
    l = sum(r.lucro for r in relatorios)
    return n, v, s, l


def _correr_cenario(
    nome: str, epocas: list[int],
    ligas: list[str] | None = None,
    linha: float = 2.5,
    ev_minimo: float = 0.05,
    lado_filtro: str | None = None,
) -> tuple[str, int, int, float, float]:
    """Corre o backtest em várias épocas, aplica filtro de lado, devolve nome + agregado."""
    relatorios = []
    for epoca in epocas:
        rel = backtest_softs.correr(
            epoca_teste=epoca, linha=linha, ev_minimo=ev_minimo,
            ligas_nomes=ligas, casas_soft=("B365",),
        )
        if lado_filtro:
            rel.apostas = [a for a in rel.apostas if a.lado == lado_filtro]
        relatorios.append(rel)
    n, v, s, l = _agregar(relatorios)
    return nome, n, v, s, l


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--epocas", default="2022,2023,2024",
        help="Épocas a usar, separadas por vírgula.",
    )
    args = parser.parse_args()

    epocas = [int(e.strip()) for e in args.epocas.split(",")]

    print(f"\n{'═' * 92}")
    print(f"  TESTES DE ROBUSTEZ — Pinnacle→B365 em épocas {epocas}")
    print(f"{'═' * 92}")
    print(f"  {'Cenário':<35} | {'N':>5} | {'Hit':>6} | {'ROI':>8} | {'Lucro':>8}")
    print(f"  {'-' * 35}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 8}-+-{'-' * 8}")

    cenarios = [
        # (nome, ligas, linha, ev_minimo, lado_filtro)
        ("1. Base (todas ligas, 2.5, EV≥5%)", None, 2.5, 0.05, None),
        ("2. Só Premier League", ["Premier League"], 2.5, 0.05, None),
        ("3. Só La Liga", ["La Liga"], 2.5, 0.05, None),
        ("4. Só Serie A", ["Serie A"], 2.5, 0.05, None),
        ("5. Só Ligue 1", ["Ligue 1"], 2.5, 0.05, None),
        ("6. Só Bundesliga", ["Bundesliga"], 2.5, 0.05, None),
        ("7. Só Liga Portugal", ["Liga Portugal"], 2.5, 0.05, None),
        ("8. Linha 3.5 (em vez de 2.5)", None, 3.5, 0.05, None),
        ("9. EV ≥ 7%", None, 2.5, 0.07, None),
        ("10. EV ≥ 10%", None, 2.5, 0.10, None),
        ("11. Só over", None, 2.5, 0.05, "over"),
        ("12. Só under", None, 2.5, 0.05, "under"),
    ]

    resultados = []
    for nome, ligas, linha, ev, lado_filtro in cenarios:
        try:
            r = _correr_cenario(nome, epocas, ligas, linha, ev, lado_filtro)
            resultados.append(r)
        except Exception as exc:
            print(f"  {nome:<35} | ERRO: {exc}")
            continue

    for nome, n, v, s, l in resultados:
        if n == 0:
            print(f"  {nome:<35} | {n:>5} | {'—':>6} | {'—':>8} | {'—':>8}")
            continue
        hit = v / n * 100
        roi = (l / s * 100) if s else 0
        print(
            f"  {nome:<35} | {n:>5} | {hit:>5.1f}% | {roi:>+7.2f}% | {l:>+7.2f}"
        )
    print(f"{'═' * 92}")

    # Interpretação
    base = next((r for r in resultados if r[0].startswith("1.")), None)
    if base and base[1] > 0:
        _, n_base, _, s_base, l_base = base
        roi_base = l_base / s_base * 100
        print(f"\n  📊 Base: ROI {roi_base:+.2f}% em {n_base} apostas.")
        print(f"\n  Se TODAS as linhas 2-12 têm ROI positivo → edge robusto.")
        print(f"  Se algumas são negativas ou inconclusivas → edge instável.")
        print(f"  Divergências grandes entre over/under indicam ineficiência")
        print(f"  especificamente num dos lados do mercado (típico em softs).")


if __name__ == "__main__":
    main()
