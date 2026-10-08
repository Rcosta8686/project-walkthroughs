"""Script: trackear_sugestoes_softs.py

Snapshot das sugestões Pinnacle→softs para live tracking (sem dinheiro
real). Grava em `dados/tracking/sugestoes.csv` cada aposta sugerida
com timestamp, odds, EV. Depois dos jogos, `resolver_sugestoes.py`
preenche o resultado (W/L) e calcula o P&L simulado.

Correr semanalmente antes dos jogos:
    python scripts/trackear_sugestoes_softs.py
    python scripts/trackear_sugestoes_softs.py --horas-fim 72
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas import analise_softs  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)

CSV_COLUNAS = [
    "timestamp_tracking", "jogo_id", "data_jogo", "liga",
    "casa", "fora", "mercado", "linha", "lado",
    "casa_soft", "prob_fair", "odd_pinnacle", "odd_soft",
    "ev", "stake", "resultado", "lucro",
]


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes.csv"


def _carregar_existentes() -> set[tuple]:
    """Chave única por (jogo_id, mercado, lado, casa_soft) para idempotência."""
    f = _ficheiro_csv()
    if not f.exists():
        return set()
    existentes = set()
    with f.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for linha in reader:
            existentes.add((
                int(linha["jogo_id"]),
                linha["mercado"],
                linha["lado"],
                linha["casa_soft"],
            ))
    return existentes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horas-fim", type=float, default=72.0,
                        help="Fim da janela em horas (default 72h = ~3 dias).")
    parser.add_argument("--stake", type=float, default=1.0,
                        help="Stake simulado por aposta (default 1 unidade).")
    parser.add_argument(
        "--mercado", default="1x2",
        choices=("golos", "1x2", "ambos"),
        help="Mercado a trackear. Default '1x2' (todas as softs via Odds API "
             "devolvem h2h; só algumas devolvem totals).",
    )
    parser.add_argument(
        "--casas-soft", default="unibet,williamhill,bet365,betano",
        help="Casas soft a comparar com Pinnacle (lowercase, keys Odds API).",
    )
    parser.add_argument(
        "--ev-minimo", type=float, default=None,
        help="EV minimo (ex: 0.05 = 5 por cento). Default: value_betting.ev_minimo do config.yaml.",
    )
    args = parser.parse_args()

    mercados = ("golos", "1x2") if args.mercado == "ambos" else (args.mercado,)
    casas_soft = tuple(c.strip() for c in args.casas_soft.split(","))

    sugestoes = analise_softs.identificar_sugestoes_softs(
        janela_horas=(0.0, args.horas_fim),
        mercados=mercados,
        casas_soft=casas_soft,
        ev_minimo_override=args.ev_minimo,
    )

    if not sugestoes:
        print("Nenhuma divergência encontrada. Não é escrito nada no CSV.")
        return

    f = _ficheiro_csv()
    f.parent.mkdir(parents=True, exist_ok=True)
    existentes = _carregar_existentes()
    novo = not f.exists()

    adicionadas = 0
    with f.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUNAS)
        if novo:
            writer.writeheader()
        ts = datetime.utcnow().isoformat()
        for s in sugestoes:
            key = (s.jogo.id, s.sugestao.mercado, s.sugestao.lado, s.casa_soft)
            if key in existentes:
                continue
            writer.writerow({
                "timestamp_tracking": ts,
                "jogo_id": s.jogo.id,
                "data_jogo": s.jogo.data_utc.isoformat(),
                "liga": s.liga.nome,
                "casa": s.casa.nome,
                "fora": s.fora.nome,
                "mercado": s.sugestao.mercado.split("@")[0],
                "linha": s.sugestao.linha if s.sugestao.linha is not None else "",
                "lado": s.sugestao.lado,
                "casa_soft": s.casa_soft,
                "prob_fair": round(s.prob_fair, 4),
                "odd_pinnacle": s.odd_pinnacle,
                "odd_soft": s.odd_soft,
                "ev": round(s.sugestao.ev, 4),
                "stake": args.stake,
                "resultado": "",  # preenchido por resolver_sugestoes.py
                "lucro": "",
            })
            adicionadas += 1

    log.info("Tracking: +%d novas sugestões em %s", adicionadas, f)
    print(f"✓ {adicionadas} novas sugestões gravadas em {f}")
    print(f"  (total varrido: {len(sugestoes)}, já existentes: {len(sugestoes) - adicionadas})")


if __name__ == "__main__":
    main()
