"""Script: prever_fim_de_semana.py

Imprime no terminal todas as sugestões (EV positivo) para os jogos entre
sexta-feira e domingo (23:59 UTC) incluídos na janela.

Pré-requisitos:
  - BD inicializada e dados ingeridos: python scripts/atualizar_dados.py
  - OddsAPI com odds correntes dos próximos jogos (idealmente)

Uso:
  python scripts/prever_fim_de_semana.py
  python scripts/prever_fim_de_semana.py --modelo gap --metrica xg
  python scripts/prever_fim_de_semana.py --referencia 2026-10-10
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas import analise, analise_softs  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def _janela_fim_de_semana(ref: datetime) -> tuple[float, float]:
    """Devolve (horas_inicio, horas_fim) relativas a `ref` cobrindo sex-dom.

    Se `ref` for sexta, sábado ou domingo, começa agora. Caso contrário,
    começa na próxima sexta às 00:00 UTC. Termina sempre no domingo 23:59.
    """
    weekday = ref.weekday()  # 0=segunda ... 6=domingo
    if weekday >= 4:  # sex/sab/dom
        inicio_dt = ref
        dias_ate_dom = 6 - weekday
    else:
        dias_ate_sex = 4 - weekday
        inicio_dt = datetime.combine(ref.date() + timedelta(days=dias_ate_sex), time.min)
        dias_ate_dom = 6 - 4  # 2 dias a partir de sexta → domingo
    fim_dt = datetime.combine(inicio_dt.date() + timedelta(days=dias_ate_dom), time(23, 59))
    horas_inicio = (inicio_dt - ref).total_seconds() / 3600.0
    horas_fim = (fim_dt - ref).total_seconds() / 3600.0
    return horas_inicio, horas_fim


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--referencia", help="Data de referência (YYYY-MM-DD). Default: agora.")
    parser.add_argument("--modelo", default="gap", choices=("gap", "poisson"))
    parser.add_argument(
        "--mercado", default="golos",
        choices=("golos", "1x2", "todos"),
        help="Mercado a analisar. Default 'golos' (over/under).",
    )
    parser.add_argument(
        "--modo", default="soft-books",
        choices=("modelo", "soft-books"),
        help="'modelo' (GAP vs Pinnacle, provou ter -3%% ROI) ou "
             "'soft-books' (Pinnacle como fair price vs Betano/bet365/etc, "
             "DEFAULT — estratégia com edge real contra casas menos eficientes).",
    )
    args = parser.parse_args()

    ref = datetime.fromisoformat(args.referencia) if args.referencia else datetime.utcnow()
    horas_inicio, horas_fim = _janela_fim_de_semana(ref)

    mercados = ("golos", "1x2") if args.mercado == "todos" else (args.mercado,)

    log.info(
        "Previsões para fim-de-semana a partir de %s "
        "(janela: +%.1fh a +%.1fh, modo=%s, mercados=%s)",
        ref, horas_inicio, horas_fim, args.modo, mercados,
    )

    if args.modo == "soft-books":
        sugestoes_softs = analise_softs.identificar_sugestoes_softs(
            referencia=ref,
            janela_horas=(horas_inicio, horas_fim),
            mercados=mercados,
        )
        # Converter para o mesmo formato esperado pelo print
        sugestoes = sugestoes_softs
    else:
        sugestoes = analise.identificar_sugestoes(
            referencia=ref,
            janela_horas=(horas_inicio, horas_fim),
            modelo=args.modelo,
            mercados=mercados,
        )

    if not sugestoes:
        print("\nSem sugestões de valor para este fim-de-semana.")
        print("Possíveis causas:")
        print("  - Nenhum jogo próximo tem odds correntes na BD (correr `atualizar_dados.py`).")
        print("  - EV abaixo do limiar configurado (ver `config.yaml` → value_betting.ev_minimo).")
        print("  - Equipas sem histórico suficiente (<10 jogos).")
        return

    # Agrupar por liga para ler melhor
    por_liga: dict[str, list] = {}
    for s in sugestoes:
        por_liga.setdefault(s.liga.nome, []).append(s)

    cabecalho = (
        f"{args.modo.upper()} ({'Pinnacle→softs' if args.modo == 'soft-books' else args.modelo.upper()})"
    )
    print(f"\n{'='*78}")
    print(f"  {len(sugestoes)} SUGESTÕES DE VALOR PARA O FIM-DE-SEMANA · {cabecalho}")
    print(f"{'='*78}\n")

    for liga_nome, items in sorted(por_liga.items()):
        print(f"── {liga_nome} ──────────────────────────────────────────")
        for s in sorted(items, key=lambda x: (x.jogo.data_utc, -x.sugestao.ev)):
            data = s.jogo.data_utc.strftime("%a %d/%m %H:%M")
            prob = s.sugestao.prob_modelo * 100
            odd = s.sugestao.odd_referencia
            ev = s.sugestao.ev * 100
            mercado_raw = s.sugestao.mercado.split("@")[0]  # remove @casa_soft
            if mercado_raw == "golos":
                aposta = f"{s.sugestao.lado.upper()} {s.sugestao.linha}"
            else:  # 1x2
                etiqueta = {"casa": "1 (casa)", "empate": "X (empate)",
                            "fora": "2 (fora)"}[s.sugestao.lado]
                aposta = f"1X2 {etiqueta}"
            # No modo soft-books, mostra onde apostar
            if hasattr(s, "casa_soft"):
                onde = f" @ {s.casa_soft}"
            else:
                onde = ""
            print(
                f"  {data}  {s.casa.nome:<22} vs {s.fora.nome:<22}  "
                f"{aposta:<16}{onde:<12} {odd:.2f}  "
                f"(prob={prob:.1f}%, EV=+{ev:.1f}%)"
            )
        print()

    print(f"{'='*78}")
    print(f"  Total: {len(sugestoes)} sugestões. Stake sugerido (flat): €{len(sugestoes)}.")
    print(f"{'='*78}\n")


if __name__ == "__main__":
    main()
