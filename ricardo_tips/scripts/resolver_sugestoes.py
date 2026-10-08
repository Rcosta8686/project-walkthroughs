"""Script: resolver_sugestoes.py

Para cada linha em `sugestoes.csv` sem resultado:
  - Procura o jogo na BD (via jogo_id).
  - Se tem golos preenchidos (jogo terminado), computa W/L e lucro.
  - Grava de volta no CSV.

Correr depois dos jogos:
    python scripts/resolver_sugestoes.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import Jogo  # noqa: E402


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes.csv"


def main() -> None:
    f = _ficheiro_csv()
    if not f.exists():
        print(f"Sem CSV em {f}. Corre primeiro `trackear_sugestoes_softs.py`.")
        return

    with f.open(encoding="utf-8") as fp:
        linhas = list(csv.DictReader(fp))

    if not linhas:
        print("CSV vazio.")
        return

    nao_resolvidas = [l for l in linhas if not l["resultado"]]
    print(f"Linhas totais: {len(linhas)} | pendentes: {len(nao_resolvidas)}")

    if not nao_resolvidas:
        print("Nada a resolver.")
        return

    resolvidas = 0
    with abrir_sessao() as s:
        for linha in nao_resolvidas:
            jogo = s.get(Jogo, int(linha["jogo_id"]))
            if jogo is None:
                continue
            if jogo.golos_casa is None or jogo.golos_fora is None:
                continue  # ainda não terminado
            gc = jogo.golos_casa
            gf = jogo.golos_fora
            lado = linha["lado"]
            mercado = linha["mercado"]
            if mercado == "golos":
                lim = float(linha["linha"])
                foi_over = (gc + gf) > lim
                if lado == "over":
                    ganhou = foi_over
                elif lado == "under":
                    ganhou = not foi_over
                else:
                    continue
            elif mercado == "1x2":
                if gc > gf:
                    resultado_real = "casa"
                elif gc < gf:
                    resultado_real = "fora"
                else:
                    resultado_real = "empate"
                ganhou = (lado == resultado_real)
            else:
                continue  # mercado desconhecido
            odd = float(linha["odd_soft"])
            stake = float(linha["stake"])
            lucro = stake * (odd - 1) if ganhou else -stake
            linha["resultado"] = "ganho" if ganhou else "perdido"
            linha["lucro"] = round(lucro, 2)
            resolvidas += 1

    with f.open("w", encoding="utf-8", newline="") as fp:
        # Reutiliza ordem de colunas do CSV original
        writer = csv.DictWriter(fp, fieldnames=linhas[0].keys())
        writer.writeheader()
        writer.writerows(linhas)

    print(f"✓ {resolvidas} sugestões resolvidas. CSV actualizado em {f}")


if __name__ == "__main__":
    main()
