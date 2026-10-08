"""Script: debug_1x2.py

Para cada jogo próximo nas top-6 ligas, mostra:
  - Odds Pinnacle 1X2 + fair probs (apos remocao de margem)
  - Odds de cada soft book 1X2 + EV calculado
  - Marca divergencias com asteriscos

Mostra TUDO (sem filtro ev_minimo) para ver se existem divergencias
pequenas que o filtro corta.

Uso:
    python scripts/debug_1x2.py
    python scripts/debug_1x2.py --horas-fim 168
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from apostas.modelos.no_vig import prob_justa_1x2  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes  # noqa: E402

CASAS_SOFT = (
    "paddypower", "skybet", "boylesports", "betway", "virginbet",
    "betvictor", "betfred_uk", "betano_uk", "unibet_uk", "coral",
    "williamhill", "sport888", "onexbet",
)


def _odd(s, jogo_id, lado, casa):
    o = s.scalar(
        select(OddsCorrentes).where(
            OddsCorrentes.jogo_id == jogo_id,
            OddsCorrentes.mercado == "1x2",
            OddsCorrentes.linha.is_(None),
            OddsCorrentes.lado == lado,
            OddsCorrentes.casa_de_apostas == casa,
        ).order_by(OddsCorrentes.timestamp.desc())
    )
    return o.odd if o is not None and o.odd > 1.0 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas-fim", type=float, default=72.0)
    args = parser.parse_args()

    agora = datetime.utcnow()
    fim = agora + timedelta(hours=args.horas_fim)

    with abrir_sessao() as s:
        jogos = s.scalars(
            select(Jogo).where(
                Jogo.data_utc >= agora, Jogo.data_utc <= fim,
            ).order_by(Jogo.data_utc)
        ).all()

        print(f"\n{'=' * 110}")
        print(f"  DEBUG 1X2 — {len(jogos)} jogos proximos")
        print(f"{'=' * 110}")

        jogos_com_pin = 0
        total_divergencias = {3: 0, 5: 0, 8: 0}

        for jogo in jogos:
            od_pin_c = _odd(s, jogo.id, "casa", "pinnacle")
            od_pin_x = _odd(s, jogo.id, "empate", "pinnacle")
            od_pin_f = _odd(s, jogo.id, "fora", "pinnacle")
            if not (od_pin_c and od_pin_x and od_pin_f):
                continue
            jogos_com_pin += 1
            try:
                p_c, p_x, p_f = prob_justa_1x2(od_pin_c, od_pin_x, od_pin_f)
            except ValueError:
                continue

            casa_eq = s.get(Equipa, jogo.casa_id)
            fora_eq = s.get(Equipa, jogo.fora_id)
            liga = s.get(Liga, jogo.liga_id)

            # Calcula EVs e deteta divergencias
            evs = []
            for casa_soft in CASAS_SOFT:
                for lado, p_fair in (("casa", p_c), ("empate", p_x), ("fora", p_f)):
                    od = _odd(s, jogo.id, lado, casa_soft)
                    if od is None:
                        continue
                    ev = p_fair * od - 1
                    evs.append((casa_soft, lado, od, ev))
                    if ev >= 0.03: total_divergencias[3] += 1
                    if ev >= 0.05: total_divergencias[5] += 1
                    if ev >= 0.08: total_divergencias[8] += 1

            max_ev = max((e for _,_,_,e in evs), default=-1)
            if max_ev >= 0.03:
                data = jogo.data_utc.strftime("%a %d/%m %H:%M")
                print(f"\n  {data}  {casa_eq.nome} vs {fora_eq.nome}  ({liga.nome})")
                print(f"    Pinnacle:  casa @ {od_pin_c}  empate @ {od_pin_x}  fora @ {od_pin_f}")
                print(f"    Fair prob: casa {p_c*100:.1f}%  empate {p_x*100:.1f}%  fora {p_f*100:.1f}%")
                # Mostrar apenas as com EV >= 2%
                for cs, lado, od, ev in sorted(evs, key=lambda x: -x[3]):
                    if ev < 0.02:
                        continue
                    marca = ""
                    if ev >= 0.10: marca = " ***"
                    elif ev >= 0.08: marca = " **"
                    elif ev >= 0.05: marca = " *"
                    elif ev >= 0.03: marca = " ."
                    print(f"      {cs:<14} {lado:<8} @ {od:<6} EV {ev*100:+6.2f}%{marca}")

        print(f"\n{'=' * 110}")
        print(f"  RESUMO")
        print(f"{'=' * 110}")
        print(f"  Jogos proximos:                    {len(jogos)}")
        print(f"  Jogos com Pinnacle 1X2 completo:   {jogos_com_pin}")
        print(f"  Divergencias com EV >= 3%:         {total_divergencias[3]}")
        print(f"  Divergencias com EV >= 5%:         {total_divergencias[5]}")
        print(f"  Divergencias com EV >= 8%:         {total_divergencias[8]}")
        print(f"{'=' * 110}\n")


if __name__ == "__main__":
    main()
