"""Script: diagnosticar_divergencias.py

Para cada jogo próximo, mostra:
  - Odds Pinnacle (over/under 2.5)
  - Fair prob após remoção de margem
  - Odds de cada soft book + EV calculado contra fair

Mostra TUDO (ignora filtro ev_minimo), para vermos onde estão as
divergências reais, se existem.

Uso:
    python scripts/diagnosticar_divergencias.py
    python scripts/diagnosticar_divergencias.py --horas-fim 168  # próxima semana
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from apostas.modelos.no_vig import prob_justa_binaria  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes  # noqa: E402

CASAS_SOFT = ("betano", "bet365", "williamhill", "unibet", "1xbet", "marathonbet")


def _odd(s, jogo_id, linha, lado, casa):
    o = s.scalar(
        select(OddsCorrentes).where(
            OddsCorrentes.jogo_id == jogo_id,
            OddsCorrentes.mercado == "golos",
            OddsCorrentes.linha == linha,
            OddsCorrentes.lado == lado,
            OddsCorrentes.casa_de_apostas == casa,
        ).order_by(OddsCorrentes.timestamp.desc())
    )
    return o.odd if o is not None and o.odd > 1.0 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas-fim", type=float, default=72.0)
    parser.add_argument("--linha", type=float, default=2.5)
    args = parser.parse_args()

    agora = datetime.utcnow()
    fim = agora + timedelta(hours=args.horas_fim)

    with abrir_sessao() as s:
        jogos = s.scalars(
            select(Jogo).where(
                Jogo.data_utc >= agora, Jogo.data_utc <= fim,
            ).order_by(Jogo.data_utc)
        ).all()

        # Primeiro: inventário de linhas Over/Under disponíveis
        linhas_disponiveis = s.execute(
            select(OddsCorrentes.linha, OddsCorrentes.casa_de_apostas).distinct()
            .where(OddsCorrentes.mercado == "golos")
        ).all()
        contagem = {}
        for linha_val, casa_val in linhas_disponiveis:
            contagem.setdefault(linha_val, set()).add(casa_val)

        print(f"\n{'=' * 110}")
        print(f"  INVENTÁRIO: linhas Over/Under na BD (jogos nos próximos {args.horas_fim:.0f}h)")
        print(f"{'=' * 110}")
        if not contagem:
            print("  ⚠ ZERO linhas Over/Under na BD. A Odds API não devolveu totals.")
            print("     Verifica se --markets=totals está activo e se Pinnacle oferece OU nestas ligas.")
            return
        for linha_val in sorted(contagem.keys(), key=lambda x: (x is None, x)):
            print(f"  Linha {linha_val}: casas = {sorted(contagem[linha_val])}")
        print(f"\n  → Vou procurar divergências na linha {args.linha}.")
        print(f"{'=' * 110}")

        jogos_com_pinnacle = 0
        divergencias_por_ev = {5: 0, 8: 0, 10: 0}  # contadores

        for jogo in jogos:
            od_pin_over = _odd(s, jogo.id, args.linha, "over", "pinnacle")
            od_pin_under = _odd(s, jogo.id, args.linha, "under", "pinnacle")
            if od_pin_over is None or od_pin_under is None:
                continue
            jogos_com_pinnacle += 1
            try:
                p_over, p_under = prob_justa_binaria(od_pin_over, od_pin_under)
            except ValueError:
                continue

            casa = s.get(Equipa, jogo.casa_id)
            fora = s.get(Equipa, jogo.fora_id)
            liga = s.get(Liga, jogo.liga_id)

            # Para cada soft book, calcular EV e mostrar se divergência >= 3%
            evs_jogo = []
            for casa_soft in CASAS_SOFT:
                for lado, p_fair in (("over", p_over), ("under", p_under)):
                    od_soft = _odd(s, jogo.id, args.linha, lado, casa_soft)
                    if od_soft is None:
                        continue
                    ev = p_fair * od_soft - 1
                    evs_jogo.append((casa_soft, lado, od_soft, ev))
                    if ev >= 0.05:
                        divergencias_por_ev[5] += 1
                    if ev >= 0.08:
                        divergencias_por_ev[8] += 1
                    if ev >= 0.10:
                        divergencias_por_ev[10] += 1

            # Imprime se há alguma divergência >= 3%
            max_ev = max((ev for _, _, _, ev in evs_jogo), default=-1)
            if max_ev >= 0.03:
                data = jogo.data_utc.strftime("%a %d/%m %H:%M")
                print(f"\n  {data}  {casa.nome} vs {fora.nome}  ({liga.nome})")
                print(f"    Pinnacle:  over @ {od_pin_over} · under @ {od_pin_under}")
                print(f"    Fair prob: over {p_over * 100:.1f}% · under {p_under * 100:.1f}%")
                for casa_soft, lado, odd, ev in evs_jogo:
                    marca = ""
                    if ev >= 0.10: marca = " 🔥🔥"
                    elif ev >= 0.08: marca = " 🔥"
                    elif ev >= 0.05: marca = " ✓"
                    elif ev >= 0.03: marca = " ·"
                    print(f"      {casa_soft:<14} {lado:<6} @ {odd:<6} EV {ev * 100:+6.2f}%{marca}")

        print(f"\n{'=' * 110}")
        print(f"  RESUMO")
        print(f"{'=' * 110}")
        print(f"  Jogos próximos (próximas {args.horas_fim:.0f}h):      {len(jogos)}")
        print(f"  Jogos com odds Pinnacle (over/under {args.linha}):   {jogos_com_pinnacle}")
        print(f"  Divergências com EV ≥ 5%:                     {divergencias_por_ev[5]}")
        print(f"  Divergências com EV ≥ 8%:                     {divergencias_por_ev[8]}")
        print(f"  Divergências com EV ≥ 10%:                    {divergencias_por_ev[10]}")
        print()
        if divergencias_por_ev[5] == 0:
            print("  ⚠ Nenhuma divergência ≥5%. Hipóteses:")
            print("    1. Pre-match early: todas as softs ainda ancoradas à Pinnacle.")
            print("       Edge só aparece horas antes do kick-off.")
            print("    2. Odds API 'bet365' pode usar feed diferente do CSV 'B365'.")
            print("    3. Dados football-data têm viés que não existe em pre-match real.")
        print(f"{'=' * 110}\n")


if __name__ == "__main__":
    main()
