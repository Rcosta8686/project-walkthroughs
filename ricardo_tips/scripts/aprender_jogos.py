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


def _carregar_resolvidos(desde: str | None = None) -> list[dict]:
    """So entradas com resultado W/L/push gravado.

    Se ``desde`` passado (YYYY-MM-DD), filtra pelo ts_scan >= esse dia.
    Util para comparar antes/depois de ajustes ao modelo.
    """
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(encoding="utf-8") as fp:
        rows = [r for r in csv.DictReader(fp)
                if (r.get("resultado") or "").strip() in ("W", "L", "push")]
    if desde:
        rows = [r for r in rows if (r.get("ts_scan") or "")[:10] >= desde]
    return rows


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
    parser.add_argument("--desde", default=None,
                        help="So sinais a partir desta data (YYYY-MM-DD). "
                             "Util para avaliar o modelo apos um ajuste: "
                             "ex. --desde 2026-10-12 para ver so sinais "
                             "pos-calibracao.")
    args = parser.parse_args()

    rows = _carregar_resolvidos(desde=args.desde)
    if args.mercado:
        rows = [r for r in rows if r["mercado"] == args.mercado]

    if not rows:
        print(f"Sem dados resolvidos em {CSV_PATH}")
        print("Primeiro corre: python scripts/resolver_sugestoes_live.py")
        return

    filtro = ""
    if args.desde:
        filtro += f"  desde {args.desde}"
    if args.mercado:
        filtro += f"  mercado={args.mercado}"
    print(f"\n{'=' * 90}")
    print(f"  APRENDER DOS JOGOS  —  {len(rows)} sinais resolvidos{filtro}")
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

    # ─────────────── RESUMO DIARIO: forcas, fraquezas, trajectoria ──────────
    _resumo_diario(rows, por_mercado, por_liga, por_prob)


def _resumo_diario(rows, por_mercado, por_liga, por_prob) -> None:
    """Resumo humano: forcas, fraquezas, em observacao, trajectoria.

    Nao substitui a calibracao (que ja corre em paralelo). Da uma leitura
    pratica: onde apostar com confianca, onde desconfiar, o que esta
    verde demais para julgar.
    """
    N_MIN_FORTE = 15   # amostra minima para dar como "forte"
    N_MIN_FRACO = 10   # amostra minima para dar como "fraco"
    ROI_FORTE   = 3    # ROI >= +3% com n>=15 = ponto forte
    ROI_FRACO   = -5   # ROI <= -5% com n>=10 = ponto fraco

    def _categorize(items):
        """Classifica categorias em (fortes, fracas, observacao)."""
        fortes, fracas, obs = [], [], []
        for nome, lista in items:
            s = _hit_stats(lista)
            if s["n"] < 5:
                continue
            if s["n"] >= N_MIN_FORTE and s["roi"] >= ROI_FORTE:
                fortes.append((nome, s))
            elif s["n"] >= N_MIN_FRACO and s["roi"] <= ROI_FRACO:
                fracas.append((nome, s))
            else:
                obs.append((nome, s))
        fortes.sort(key=lambda x: -x[1]["roi"])
        fracas.sort(key=lambda x: x[1]["roi"])
        return fortes, fracas, obs

    print(f"\n{'=' * 90}")
    print("  RESUMO DIARIO  — onde estas forte, onde precisas melhorar")
    print("=" * 90)

    # Trajectoria ultimos 7 dias (se ts_scan existe)
    from datetime import datetime, timedelta
    def _data_do_row(r):
        ts = (r.get("ts_scan") or "")[:10]
        try:
            return datetime.strptime(ts, "%Y-%m-%d").date()
        except ValueError:
            return None
    datas = [d for d in (_data_do_row(r) for r in rows) if d is not None]
    if datas:
        hoje = max(datas)
        corte_7d = hoje - timedelta(days=7)
        rows_7d = [r for r in rows if (_data_do_row(r) or hoje) >= corte_7d]
        if len(rows_7d) >= 10:
            s7 = _hit_stats(rows_7d)
            s_total = _hit_stats(rows)
            delta_roi = s7["roi"] - s_total["roi"]
            tag = ("↑ a melhorar" if delta_roi > 3
                   else "↓ a piorar" if delta_roi < -3
                   else "→ estavel")
            print(f"\n  TRAJECTORIA (ultimos 7 dias vs total):  {tag}")
            print(f"    7d:    n={s7['n']:>3}  hit={s7['hit_rate']:>5.1f}%  ROI={s7['roi']:>+6.1f}%")
            print(f"    total: n={s_total['n']:>3}  hit={s_total['hit_rate']:>5.1f}%  ROI={s_total['roi']:>+6.1f}%")

    # ONDE ESTAS FORTE
    print(f"\n  ✓ PONTOS FORTES (n>={N_MIN_FORTE}, ROI>=+{ROI_FORTE}%)")
    print("  " + "─" * 86)
    fortes_m, _, _ = _categorize(por_mercado.items())
    fortes_l, _, _ = _categorize(por_liga.items())
    tudo_forte = ([("mercado", n, s) for n, s in fortes_m[:3]]
                  + [("liga", n, s) for n, s in fortes_l[:3]])
    if not tudo_forte:
        print(f"    (ainda nenhuma categoria com amostra suficiente E ROI positivo)")
    for cat, n, s in tudo_forte:
        print(f"    {cat:7s} {n:<30s}  n={s['n']:>3}  hit={s['hit_rate']:>5.1f}%  ROI={s['roi']:>+6.1f}%")

    # ONDE ESTAS FRACO
    print(f"\n  ✗ PRECISAS DOMINAR (n>={N_MIN_FRACO}, ROI<={ROI_FRACO}%)")
    print("  " + "─" * 86)
    _, fracas_m, _ = _categorize(por_mercado.items())
    _, fracas_l, _ = _categorize(por_liga.items())
    tudo_fraco = ([("mercado", n, s) for n, s in fracas_m[:3]]
                  + [("liga", n, s) for n, s in fracas_l[:3]])
    if not tudo_fraco:
        print(f"    (ainda nenhuma categoria sistematicamente deficitaria)")
    for cat, n, s in tudo_fraco:
        print(f"    {cat:7s} {n:<30s}  n={s['n']:>3}  hit={s['hit_rate']:>5.1f}%  ROI={s['roi']:>+6.1f}%")

    # EM OBSERVACAO (poucos dados ainda — nao julgar)
    print(f"\n  ⋯ EM OBSERVACAO (sem amostra suficiente para julgar)")
    print("  " + "─" * 86)
    _, _, obs_m = _categorize(por_mercado.items())
    _, _, obs_l = _categorize(por_liga.items())
    obs_pequenos = [(c, n, s) for c, lst in (("mercado", obs_m), ("liga", obs_l))
                    for n, s in lst if s["n"] < N_MIN_FRACO]
    if obs_pequenos:
        for cat, n, s in obs_pequenos[:5]:
            print(f"    {cat:7s} {n:<30s}  n={s['n']:>3}  (precisa mais {max(0, N_MIN_FRACO - s['n'])} sinais)")
    else:
        print(f"    (tudo com amostra >= {N_MIN_FRACO})")

    # Calibracao (persiste)
    print(f"\n  ⚙ CALIBRACAO POR BUCKET (modelo esta a dizer a verdade?)")
    print("  " + "─" * 86)
    for b in ("55-65%", "65-75%", "75-85%", "85%+"):
        if b in por_prob:
            s = _hit_stats(por_prob[b])
            exp = {"55-65%": 60, "65-75%": 70, "75-85%": 80, "85%+": 90}[b]
            gap = s["hit_rate"] - exp
            if s["n"] < 10:
                tag = "⋯ poucos dados"
            elif abs(gap) < 7:
                tag = "✓ calibrado"
            elif gap < 0:
                tag = f"↑ confiante demais ({gap:+.0f}pp)"
            else:
                tag = f"↓ conservador ({gap:+.0f}pp)"
            print(f"    bucket {b:<7s}  n={s['n']:>3}  previsto~{exp}%  real={s['hit_rate']:>5.1f}%  {tag}")

    print("")


if __name__ == "__main__":
    main()
