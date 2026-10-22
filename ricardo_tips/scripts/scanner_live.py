"""Scanner LIVE: durante os jogos, encontra divergencias Pinnacle→softs.

Corre em loop infinito (sai com Ctrl+C):

    python scripts/scanner_live.py --intervalo-s 90

Cada ciclo:
  1. Puxa /events de todas as ligas (grátis).
  2. Filtra jogos in-play (começaram ha <2h30, ainda não acabaram).
  3. Para cada jogo in-play, chama /events/{id}/odds com todos os
     mercados (h2h, totals, spreads, btts, double_chance).
  4. Corre o scanner Pinnacle→softs sobre jogos recentes (-2.5h a +0.5h).
  5. Imprime as novas sugestões (odds, EV, link de aposta).

CUSTO EM CREDITS:
  Com regions=eu,uk + 5 mercados = 10 credits/jogo/scan.
  5 jogos live × scan de 90s por 2 horas = ~4000 credits.
  Com plano pago (20k/mês), são 4 fins-de-semana de scanning intenso.
  Para ser conservador: scan de 180s, só 2 regions, menos mercados.

CAVEATS:
  - Softs frequentemente SUSPENDEM mercados in-play em momentos chave
    (penálti, cartão vermelho, golo). Antes de confirmar, verifica
    na casa se a aposta ainda está disponível.
  - Pinnacle acelera os ajustes; softs atrasam uns segundos a minutos.
    O edge aparece nessa janela e desaparece rápido. Não demores a
    confirmar.
  - EVs em jogos ao vivo tendem a ser menores que pre-match (os
    bookies afinam ao estado real). Ajusta ev_minimo para 3-5% se
    quiseres ver mais sugestões.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apostas.utils.tempo import agora_utc  # noqa: E402

from apostas.alertas import analise_softs  # noqa: E402
from apostas.ingestao import odds_api_live  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


CSV_COLUNAS_LIVE = [
    "timestamp_scan", "jogo_id", "data_jogo", "liga",
    "casa", "fora", "mercado", "linha", "lado",
    "casa_soft", "prob_fair", "odd_pinnacle", "odd_soft", "ev",
]


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes_live.csv"


def _carregar_vistas(f: Path) -> set[tuple]:
    """Idempotencia: (jogo_id, mercado, linha, lado, casa_soft).

    Mesma aposta aparecida em scans sucessivos nao se duplica no CSV.
    """
    if not f.exists():
        return set()
    vistas = set()
    with f.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for linha in reader:
            vistas.add((
                int(linha["jogo_id"]),
                linha["mercado"],
                linha["linha"] or "",
                linha["lado"],
                linha["casa_soft"],
            ))
    return vistas


def _gravar_sugestoes(sugestoes, f: Path) -> int:
    f.parent.mkdir(parents=True, exist_ok=True)
    vistas = _carregar_vistas(f)
    novo = not f.exists()
    adicionadas = 0
    with f.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUNAS_LIVE)
        if novo:
            writer.writeheader()
        ts = agora_utc().isoformat()
        for s in sugestoes:
            linha_str = (
                str(s.sugestao.linha) if s.sugestao.linha is not None else ""
            )
            key = (
                s.jogo.id, s.sugestao.mercado, linha_str,
                s.sugestao.lado, s.casa_soft,
            )
            if key in vistas:
                continue
            writer.writerow({
                "timestamp_scan": ts,
                "jogo_id": s.jogo.id,
                "data_jogo": s.jogo.data_utc.isoformat(),
                "liga": s.liga.nome,
                "casa": s.casa.nome,
                "fora": s.fora.nome,
                "mercado": s.sugestao.mercado,
                "linha": linha_str,
                "lado": s.sugestao.lado,
                "casa_soft": s.casa_soft,
                "prob_fair": round(s.prob_fair, 4),
                "odd_pinnacle": s.odd_pinnacle,
                "odd_soft": s.odd_soft,
                "ev": round(s.sugestao.ev, 4),
            })
            adicionadas += 1
    return adicionadas


def _imprimir_sugestoes(sugestoes) -> None:
    if not sugestoes:
        return
    print(f"\n  === {len(sugestoes)} nova(s) sugestao(oes) live ===")
    for s in sugestoes:
        linha = f" {s.sugestao.linha}" if s.sugestao.linha is not None else ""
        print(
            f"    [{s.liga.nome}] {s.casa.nome} vs {s.fora.nome}"
            f"  →  {s.sugestao.mercado}{linha} {s.sugestao.lado}"
            f"  @ {s.odd_soft:.2f} ({s.casa_soft})"
            f"  EV={s.sugestao.ev * 100:.1f}%"
            f"  (pin fair={1/s.prob_fair:.2f})"
        )


def _correr_ciclo(args) -> None:
    agora = agora_utc().strftime("%H:%M:%S")
    print(f"\n[{agora}] A puxar odds live...")
    r = odds_api_live.puxar_live(
        minutos_atras=args.minutos_atras,
        bookmakers=args.bookmakers,
        regions=args.regions,
        markets=args.markets,
    )
    print(
        f"    {r.jogos_live_vistos} jogos live, {r.chamadas_api} chamadas, "
        f"{r.odds_criadas} odds novas, {r.odds_atualizadas} actualizadas, "
        f"credits={r.credits_restantes}"
    )
    if r.erros:
        print(f"    AVISO: {len(r.erros)} erros ({r.erros[0]})")

    if r.jogos_live_vistos == 0:
        print("    (sem jogos in-play nesta janela — nada para scanner)")
        return

    casas_soft = tuple(c.strip() for c in args.casas_soft.split(","))
    mercados = tuple(args.mercados_scanner.split(","))
    sugestoes = analise_softs.identificar_sugestoes_softs(
        # Janela live: jogos entre -2.5h e +0.5h do agora
        janela_horas=(-args.minutos_atras / 60.0, 0.5),
        mercados=mercados,
        casas_soft=casas_soft,
        ev_minimo_override=args.ev_minimo,
    )
    _imprimir_sugestoes(sugestoes)
    if sugestoes:
        adicionadas = _gravar_sugestoes(sugestoes, _ficheiro_csv())
        print(f"    → {adicionadas} gravadas em sugestoes_live.csv "
              f"(repetidas de scans anteriores ignoradas)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scanner Pinnacle→softs em tempo real durante os jogos.",
    )
    parser.add_argument(
        "--intervalo-s", type=int, default=120,
        help="Segundos entre scans (default 120). Menos = mais fresh mas mais credits.",
    )
    parser.add_argument(
        "--ciclos", type=int, default=0,
        help="Numero de ciclos. 0 = infinito (sair com Ctrl+C).",
    )
    parser.add_argument(
        "--ev-minimo", type=float, default=0.03,
        help="EV minimo para sugerir (default 0.03 = 3 por cento, mais baixo que pre-match).",
    )
    parser.add_argument(
        "--minutos-atras", type=float, default=150.0,
        help="Jogos que começaram ha menos de X minutos (default 150 = 2h30).",
    )
    parser.add_argument(
        "--bookmakers",
        default=odds_api_live.BOOKMAKERS_DEFAULT,
        help="Keys exactas Odds API separadas por virgula.",
    )
    parser.add_argument(
        "--regions", default="eu,uk",
        help="Regions a pedir (default eu,uk — reduz custo vs eu,uk,us,au).",
    )
    parser.add_argument(
        "--markets",
        default=odds_api_live.MARKETS_LIVE_DEFAULT,
        help="Mercados do /events endpoint (default: h2h,totals,spreads,btts,double_chance).",
    )
    parser.add_argument(
        "--mercados-scanner",
        default="1x2,golos,btts,dupla,handicap",
        help="Mercados que o scanner analisa (nomes internos: 1x2,golos,btts,dupla,handicap).",
    )
    parser.add_argument(
        "--casas-soft",
        default="paddypower,skybet,boylesports,betway,virginbet,betvictor,"
                "betfred_uk,betano_uk,unibet_uk,coral,williamhill,sport888,"
                "onexbet",
        help="Casas soft para comparar (default: todas as 13 principais).",
    )
    args = parser.parse_args()

    print(f"Scanner LIVE iniciado — intervalo {args.intervalo_s}s, "
          f"EV min {args.ev_minimo * 100:.0f} por cento, "
          f"janela {args.minutos_atras:.0f}min atras.")
    print("Sai com Ctrl+C.\n")

    ciclo = 0
    try:
        while True:
            ciclo += 1
            try:
                _correr_ciclo(args)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                print(f"    ERRO no ciclo {ciclo}: {exc}")
                log.exception("Erro no ciclo live")
            if args.ciclos > 0 and ciclo >= args.ciclos:
                break
            time.sleep(args.intervalo_s)
    except KeyboardInterrupt:
        print("\nInterrompido pelo utilizador. Até à próxima.")


if __name__ == "__main__":
    main()
