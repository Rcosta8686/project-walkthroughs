"""P&L live: ROI por mercado, EV bucket, minuto, liga, casa.

Le dados/tracking/sugestoes_live_v2.csv (ja resolvido por
resolver_sugestoes_live.py) e produz breakdown de performance.

Resposta a 2 perguntas chave:
  - O modelo acerta? Qual o ROI global e por mercado?
  - Em que condicoes acerta mais? (EV, minuto do jogo, liga)

Uso:
    python scripts/mostrar_pnl_live.py
    python scripts/mostrar_pnl_live.py --stake 0.50   # outra stake
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402

CSV_PATH = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _parse_rows() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def _agrupar(rows: list[dict], chave_fn) -> dict:
    """Agrupa linhas W/L por uma chave e devolve dict {chave: stats}."""
    stats = defaultdict(lambda: {"n": 0, "W": 0, "L": 0, "push": 0, "lucro": 0.0, "stake": 0.0})
    for r in rows:
        res = (r.get("resultado") or "").strip()
        if res not in ("W", "L", "push"):
            continue
        key = chave_fn(r)
        stats[key]["n"] += 1
        stats[key][res] += 1
        try:
            lucro = float(r.get("lucro") or 0)
        except ValueError:
            lucro = 0.0
        stats[key]["lucro"] += lucro
        if res != "push":
            stats[key]["stake"] += 1.0
    return stats


def _formatar(stats: dict, label: str, stake_mult: float = 1.0) -> None:
    print(f"\n  === {label} ===")
    print(f"  {'Chave':<30} {'N':>5} {'W':>5} {'L':>5} {'push':>5} "
          f"{'Hit%':>6} {'Lucro':>10} {'ROI%':>7}")
    print(f"  {'-'*30} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*6} {'-'*10} {'-'*7}")
    linhas_ord = sorted(stats.items(), key=lambda kv: -kv[1]["lucro"])
    for k, s in linhas_ord:
        hit = s["W"] / s["stake"] * 100 if s["stake"] > 0 else 0.0
        roi = (s["lucro"] / s["stake"]) * 100 if s["stake"] > 0 else 0.0
        lucro_eur = s["lucro"] * stake_mult
        print(f"  {str(k):<30} {s['n']:>5} {s['W']:>5} {s['L']:>5} {s['push']:>5} "
              f"{hit:>6.1f} {lucro_eur:>10.2f} {roi:>+7.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stake", type=float, default=0.50,
                        help="Stake real por aposta em EUR (default 0.50).")
    args = parser.parse_args()
    stake_mult = args.stake

    rows = _parse_rows()
    if not rows:
        print(f"Sem CSV live em {CSV_PATH}")
        return

    resolvidos = [r for r in rows if (r.get("resultado") or "").strip() in ("W", "L", "push")]
    pendentes = len(rows) - len(resolvidos)
    print(f"\n{'=' * 80}")
    print(f"  P&L LIVE  ·  {len(rows)} sugestoes totais, {len(resolvidos)} resolvidas, {pendentes} pendentes")
    print(f"  Stake: {args.stake:.2f} EUR por aposta")
    print(f"{'=' * 80}")

    if not resolvidos:
        print("Zero resolvidas ainda. Corre `python scripts/resolver_sugestoes_live.py` primeiro.")
        return

    # Totais globais
    tot_w = sum(1 for r in resolvidos if r.get("resultado") == "W")
    tot_l = sum(1 for r in resolvidos if r.get("resultado") == "L")
    tot_push = sum(1 for r in resolvidos if r.get("resultado") == "push")
    tot_lucro = sum(float(r.get("lucro") or 0) for r in resolvidos)
    tot_stake = tot_w + tot_l
    hit = tot_w / tot_stake * 100 if tot_stake > 0 else 0.0
    roi = (tot_lucro / tot_stake) * 100 if tot_stake > 0 else 0.0
    lucro_eur = tot_lucro * stake_mult
    print(f"\n  Global: W={tot_w} L={tot_l} push={tot_push}  "
          f"hit={hit:.1f}%  lucro={lucro_eur:+.2f} EUR  ROI={roi:+.1f}%")

    # Breakdown por mercado
    _formatar(_agrupar(resolvidos, lambda r: r.get("mercado", "?")),
              "ROI por mercado", stake_mult)

    # Breakdown por liga
    _formatar(_agrupar(resolvidos, lambda r: r.get("liga", "?")),
              "ROI por liga", stake_mult)

    # Breakdown por casa
    _formatar(_agrupar(resolvidos, lambda r: r.get("casa", "?")),
              "ROI por casa de apostas", stake_mult)

    # Breakdown por EV bucket
    def _ev_bucket(r):
        try:
            ev = float(r.get("ev") or 0)
        except ValueError:
            return "?"
        if ev < 0.05:
            return "0-5%"
        if ev < 0.10:
            return "5-10%"
        if ev < 0.15:
            return "10-15%"
        if ev < 0.20:
            return "15-20%"
        return "20%+"
    _formatar(_agrupar(resolvidos, _ev_bucket), "ROI por EV bucket", stake_mult)

    # Breakdown por minuto do jogo
    def _min_bucket(r):
        try:
            m = int(r.get("minuto") or 0)
        except ValueError:
            return "?"
        if m < 15:
            return "0-15'"
        if m < 30:
            return "15-30'"
        if m < 45:
            return "30-45'"
        if m < 60:
            return "45-60'"
        if m < 75:
            return "60-75'"
        return "75'+"
    _formatar(_agrupar(resolvidos, _min_bucket), "ROI por minuto do jogo", stake_mult)


if __name__ == "__main__":
    main()
