"""Aprender dos jogos: le todos os sinais emitidos + resultados reais,
calcula taxa de acerto, identifica padroes.

Fluxo:
  1. Le dados/tracking/sugestoes_live_v2.csv (ja resolvido)
  2. Para cada sinal resolvido (W/L), extrai:
     - mercado + lado + prob_modelo prevista
     - racionais que suportaram
     - minuto e marcador quando emitido
     - resultado real (W/L/push)
  3. Agrupa por padroes e calcula hit rate:
     - Por mercado
     - Por prob bucket (55-65%, 65-75%, 75-85%, 85%+)
     - Por minuto (30-45, 45-60, 60-75, 75+)
     - Por racional (sinais com 'momentum' ganharam X%, com 'defesa fraca' Y%)
  4. Guarda insights em dados/calibracao_live.json para o modelo usar
  5. Imprime relatorio para humano ler

Objectivo: identificar onde o modelo e BOM e onde falha. Afinar coefs.

Uso:
    python scripts/aprender_jogos.py
    python scripts/aprender_jogos.py --mercado golos
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402

CSV_PATH = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
CALIBRACAO_PATH = project_root() / "dados" / "calibracao_live.json"


def _carregar_resolvidos() -> list[dict]:
    """So entradas com resultado W/L/push gravado."""
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(encoding="utf-8") as fp:
        return [r for r in csv.DictReader(fp)
                if (r.get("resultado") or "").strip() in ("W", "L", "push")]


def _hit_stats(rows: list[dict]) -> dict:
    n_total = len(rows)
    n_w = sum(1 for r in rows if r["resultado"] == "W")
    n_l = sum(1 for r in rows if r["resultado"] == "L")
    n_push = n_total - n_w - n_l
    stake = n_w + n_l
    hit_rate = (n_w / stake * 100) if stake > 0 else 0.0
    lucro_total = sum(float(r.get("lucro") or 0) for r in rows)
    roi = (lucro_total / stake * 100) if stake > 0 else 0.0
    return {
        "n": n_total, "W": n_w, "L": n_l, "push": n_push,
        "hit_rate": hit_rate, "lucro": lucro_total, "roi": roi,
    }


def _formatar(stats: dict, label: str) -> str:
    return (f"  {label:<35}  n={stats['n']:>4}  "
            f"W={stats['W']:>3}  L={stats['L']:>3}  "
            f"hit={stats['hit_rate']:>5.1f}%  "
            f"ROI={stats['roi']:>+6.1f}%  "
            f"lucro={stats['lucro']:>+6.2f}u")


def _prob_bucket(r: dict) -> str:
    try:
        p = float(r.get("prob_modelo") or 0) * 100
    except ValueError:
        return "?"
    if p < 55: return "<55%"
    if p < 65: return "55-65%"
    if p < 75: return "65-75%"
    if p < 85: return "75-85%"
    return "85%+"


def _minuto_bucket(r: dict) -> str:
    try:
        m = int(r.get("minuto") or 0)
    except ValueError:
        return "?"
    if m < 30: return "0-30'"
    if m < 45: return "30-45'"
    if m < 60: return "45-60'"
    if m < 75: return "60-75'"
    return "75'+"


# Palavras-chave que podemos procurar nos racionais (quando guardarmos)
PADROES = (
    "momentum", "dominante", "em grande", "defesa a ceder", "big chances",
    "chasing", "pressiona", "derby", "severo", "vermelho", "a atacar",
    "jogo decidido", "a jogar melhor",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mercado", default=None,
                        help="Filtrar por mercado (golos, cantos, 1x2, etc)")
    args = parser.parse_args()

    rows = _carregar_resolvidos()
    if args.mercado:
        rows = [r for r in rows if r["mercado"] == args.mercado]

    if not rows:
        print(f"Sem dados resolvidos em {CSV_PATH}")
        print("Primeiro corre: python scripts/resolver_sugestoes_live.py")
        return

    print(f"\n{'=' * 90}")
    print(f"  APRENDER DOS JOGOS  —  {len(rows)} sinais resolvidos")
    print("=" * 90)

    global_s = _hit_stats(rows)
    print(_formatar(global_s, "GLOBAL"))
    print()

    # Por mercado
    por_mercado = defaultdict(list)
    for r in rows:
        por_mercado[r["mercado"]].append(r)
    print(f"\n{'─' * 90}")
    print("  POR MERCADO")
    print("─" * 90)
    for m, lista in sorted(por_mercado.items(), key=lambda x: -_hit_stats(x[1])["roi"]):
        print(_formatar(_hit_stats(lista), m))

    # Por prob bucket
    por_prob = defaultdict(list)
    for r in rows:
        por_prob[_prob_bucket(r)].append(r)
    print(f"\n{'─' * 90}")
    print("  POR PROB BUCKET  (confianca do modelo)")
    print("─" * 90)
    for b in ("<55%", "55-65%", "65-75%", "75-85%", "85%+"):
        if b in por_prob:
            s = _hit_stats(por_prob[b])
            exp_prob = {"<55%": 50, "55-65%": 60, "65-75%": 70, "75-85%": 80, "85%+": 90}[b]
            calibracao = s["hit_rate"] - exp_prob
            tag = "✓ calibrado" if abs(calibracao) < 7 else ("↑ confiante demais" if calibracao < 0 else "↓ conservador")
            print(_formatar(s, f"{b:<10} esperado ~{exp_prob}%  {tag}"))

    # Por minuto bucket
    por_min = defaultdict(list)
    for r in rows:
        por_min[_minuto_bucket(r)].append(r)
    print(f"\n{'─' * 90}")
    print("  POR MINUTO (quando emitido)")
    print("─" * 90)
    for b in ("0-30'", "30-45'", "45-60'", "60-75'", "75'+"):
        if b in por_min:
            print(_formatar(_hit_stats(por_min[b]), b))

    # Por liga
    por_liga = defaultdict(list)
    for r in rows:
        por_liga[r["liga"]].append(r)
    print(f"\n{'─' * 90}")
    print("  POR LIGA")
    print("─" * 90)
    for liga, lista in sorted(por_liga.items(), key=lambda x: -_hit_stats(x[1])["roi"]):
        print(_formatar(_hit_stats(lista), liga))

    # Gravar calibracao json para o modelo usar
    calibracao = {
        "n_amostras": len(rows),
        "por_mercado": {m: _hit_stats(lista) for m, lista in por_mercado.items()},
        "por_prob_bucket": {b: _hit_stats(lista) for b, lista in por_prob.items()},
        "roi_global": global_s["roi"],
    }
    CALIBRACAO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CALIBRACAO_PATH.open("w", encoding="utf-8") as fp:
        json.dump(calibracao, fp, indent=2, ensure_ascii=False)
    print(f"\n{'─' * 90}")
    print(f"  Calibracao guardada em {CALIBRACAO_PATH}")
    print(f"  Modelo lera deste ficheiro para afinar previsoes.")
    print("─" * 90)

    # Learnings textuais
    print(f"\n{'=' * 90}")
    print("  LEARNINGS")
    print("=" * 90)
    melhores_mercados = sorted(
        por_mercado.items(),
        key=lambda x: -_hit_stats(x[1])["roi"]
    )
    piores_mercados = sorted(
        por_mercado.items(),
        key=lambda x: _hit_stats(x[1])["roi"]
    )
    if melhores_mercados:
        m, lista = melhores_mercados[0]
        s = _hit_stats(lista)
        if s["n"] >= 5:
            print(f"  ✓ MELHOR mercado: {m}  (ROI {s['roi']:+.1f}%, hit {s['hit_rate']:.1f}%)")
    if piores_mercados and piores_mercados[0][0] != (melhores_mercados[0][0] if melhores_mercados else None):
        m, lista = piores_mercados[0]
        s = _hit_stats(lista)
        if s["n"] >= 5 and s["roi"] < -5:
            print(f"  ✗ PIOR mercado: {m}  (ROI {s['roi']:+.1f}%, hit {s['hit_rate']:.1f}%)")

    # Calibracao: identifica buckets sobre-confiantes
    for b in ("55-65%", "65-75%", "75-85%", "85%+"):
        if b in por_prob:
            s = _hit_stats(por_prob[b])
            exp = {"55-65%": 60, "65-75%": 70, "75-85%": 80, "85%+": 90}[b]
            if s["n"] >= 10 and s["hit_rate"] < exp - 8:
                print(f"  ⚠ Bucket {b}: modelo CONFIANTE DEMAIS "
                      f"(previa ~{exp}% mas acerta {s['hit_rate']:.0f}%)")

    print("")


if __name__ == "__main__":
    main()
