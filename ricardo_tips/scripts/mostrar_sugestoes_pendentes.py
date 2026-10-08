"""Script: mostrar_sugestoes_pendentes.py

Imprime as sugestões pendentes (jogos ainda não jogados) formatadas
para apostar manualmente. Mostra:
  - Jogo + data + hora
  - Que aposta fazer (mercado, lado, odd)
  - Em que casa apostar
  - EV calculado
  - Stake sugerido (default €0.50)

Uso:
    python scripts/mostrar_sugestoes_pendentes.py
    python scripts/mostrar_sugestoes_pendentes.py --stake 1.0
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402

_URLS_CASAS = {
    "pinnacle":    "https://www.pinnacle.com/",
    "betano":      "https://www.betano.pt/",
    "bet365":      "https://www.bet365.com/",
    "unibet":      "https://www.unibet.com/",
    "williamhill": "https://sports.williamhill.com/",
    "1xbet":       "https://1xbet.com/",
    "marathonbet": "https://www.marathonbet.com/",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stake", type=float, default=0.50,
                        help="Stake por aposta (default: 0.50 €)")
    args = parser.parse_args()

    f = project_root() / "dados" / "tracking" / "sugestoes.csv"
    if not f.exists():
        print("Sem CSV de sugestoes. Corre primeiro:")
        print("  python scripts/atualizar_dados.py")
        print("  python scripts/trackear_sugestoes_softs.py --mercado 1x2 --ev-minimo 0.05")
        return

    with f.open(encoding="utf-8") as fp:
        linhas = list(csv.DictReader(fp))
    pendentes = [l for l in linhas if not l["resultado"]]

    if not pendentes:
        print("Zero sugestoes pendentes. Ou nao ha divergencias, ou todas")
        print("ja foram resolvidas. Corre `trackear_sugestoes_softs.py` para refrescar.")
        return

    # Ordena por data do jogo + EV
    pendentes.sort(key=lambda l: (l["data_jogo"], -float(l["ev"])))

    total_stake = args.stake * len(pendentes)
    total_ev = sum(float(l["ev"]) * args.stake for l in pendentes)

    print(f"\n{'=' * 90}")
    print(f"  APOSTAS A FAZER ESTE FIM-DE-SEMANA  ·  {len(pendentes)} sugestoes")
    print(f"{'=' * 90}")
    print(f"  Stake por aposta: {args.stake:.2f} EUR")
    print(f"  Stake total:      {total_stake:.2f} EUR")
    print(f"  EV total esperado: +{total_ev:.2f} EUR  (apenas teorico)")
    print(f"{'=' * 90}\n")

    for i, s in enumerate(pendentes, start=1):
        data = datetime.fromisoformat(s["data_jogo"]).strftime("%a %d/%m %H:%M")
        mercado = s["mercado"]
        if mercado == "golos":
            aposta_label = f"{s['lado'].upper()} {s['linha']} golos"
        elif mercado == "1x2":
            lado_pt = {"casa": "1 (casa)", "empate": "X (empate)",
                       "fora": "2 (fora)"}[s["lado"]]
            aposta_label = f"1X2 {lado_pt}"
        elif mercado == "btts":
            lado_pt = "SIM (ambas marcam)" if s["lado"] == "sim" else "NAO (uma nao marca)"
            aposta_label = f"BTTS {lado_pt}"
        elif mercado == "dupla":
            lado_pt = {"1x": "1X (casa/empate)", "x2": "X2 (empate/fora)",
                       "12": "12 (casa/fora)"}[s["lado"]]
            aposta_label = f"Dupla {lado_pt}"
        elif mercado == "handicap":
            lado_pt = s["lado"].upper()
            sinal = "+" if float(s["linha"]) >= 0 else ""
            aposta_label = f"Handicap {lado_pt} {sinal}{s['linha']}"
        else:
            aposta_label = f"{mercado} {s['lado']}"
        casa = s["casa_soft"]
        url = _URLS_CASAS.get(casa, "")
        prob = float(s["prob_fair"]) * 100
        odd = float(s["odd_soft"])
        ev = float(s["ev"]) * 100

        print(f"  [{i:>2}] {data}  {s['casa']} vs {s['fora']} ({s['liga']})")
        print(f"       Aposta:   {aposta_label}")
        print(f"       Casa:     {casa.upper()}   {url}")
        print(f"       Odd:      {odd:.2f}  (Pinnacle fair = {prob:.1f}%, EV +{ev:.1f}%)")
        print(f"       Stake:    {args.stake:.2f} EUR  (se ganhar: +{(odd - 1) * args.stake:.2f} EUR; se perder: -{args.stake:.2f})")
        print()

    print(f"{'=' * 90}")
    print(f"  Depois dos jogos (segunda), corre:")
    print(f"    python scripts/atualizar_dados.py")
    print(f"    python scripts/resolver_sugestoes.py")
    print(f"    python scripts/mostrar_pnl.py")
    print(f"{'=' * 90}\n")


if __name__ == "__main__":
    main()
