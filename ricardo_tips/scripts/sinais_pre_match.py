"""Envia sinais pre-match com EV (modelo Pinnacle→softs).

Quando o Sofascore live esta bloqueado pelo Cloudflare, o scanner nao
produz sinais ao vivo. Este script e o complemento: corre periodicamente
(ex: de 2 em 2 horas) e envia alertas sobre jogos pre-match que tem edge
versus softs (Betano/bet365/etc), usando a Odds API — que **nao depende**
do Sofascore.

Pipeline:
  1. Puxa odds frescas da Odds API (/sports/{sport}/odds — barato: 500
     credits/mes = ~41 puxadas/mes, suficiente para 2x por dia)
  2. Grava Jogo + OddsCorrentes na BD
  3. Chama analise_softs.identificar_sugestoes_softs para a proxima janela
  4. Deduplica contra alertas ja enviados (CSV)
  5. Envia novos ao Telegram com odd target e stake

Argumentos:
  --janela-horas N      Jogos nas proximas N horas (default 24)
  --ev-min E            EV minimo (ex 0.07, default do config)
  --telegram            Envia; sem a flag, so imprime
  --mercados m1,m2      golos,1x2,btts,dupla,handicap (default: golos,1x2)
  --sem-sincronizar     Nao chama odds_api.sincronizar (usa odds ja na BD)

Design: idempotente — correr 2x seguido nao duplica alertas.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
except NameError:
    pass

from apostas.alertas import analise_softs  # noqa: E402
from apostas.utils.config import load_config, project_root  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402

log = get_logger(__name__)


_CSV_ENVIADOS = "pre_match_enviados.csv"


def _chave_sugestao(s: analise_softs.SugestaoSoft) -> str:
    """Chave unica por (jogo, mercado, lado, linha, casa). Determinista."""
    linha = ""
    if hasattr(s.sugestao, "linha") and s.sugestao.linha is not None:
        linha = str(s.sugestao.linha)
    base = f"{s.jogo.id}|{s.sugestao.mercado}|{s.sugestao.lado}|{linha}|{s.casa_soft}"
    return hashlib.md5(base.encode()).hexdigest()[:16]


def _carregar_enviados(path: Path) -> set[str]:
    if not path.exists():
        return set()
    enviados: set[str] = set()
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                k = row.get("chave")
                if k:
                    enviados.add(k)
    except Exception as exc:  # noqa: BLE001
        log.warning("Erro a ler %s: %s", path, exc)
    return enviados


def _gravar_enviado(path: Path, chave: str, s: analise_softs.SugestaoSoft) -> None:
    cabecalho = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "chave", "ts_envio", "jogo_id",
            "casa", "fora", "liga",
            "mercado", "lado", "linha",
            "casa_soft", "odd_soft", "ev",
        ])
        if cabecalho:
            w.writeheader()
        w.writerow({
            "chave": chave,
            "ts_envio": agora_utc().isoformat(timespec="seconds"),
            "jogo_id": s.jogo.id,
            "casa": s.casa.nome, "fora": s.fora.nome, "liga": s.liga.nome,
            "mercado": s.sugestao.mercado,
            "lado": s.sugestao.lado,
            "linha": getattr(s.sugestao, "linha", "") or "",
            "casa_soft": s.casa_soft,
            "odd_soft": round(s.odd_soft, 2),
            "ev": round(s.sugestao.ev, 4),
        })


def _formatar_telegram(s: analise_softs.SugestaoSoft) -> str:
    """Mensagem Telegram por sugestao individual."""
    data = s.jogo.data_utc.strftime("%a %d/%m %H:%M")
    mercado = s.sugestao.mercado.split("@")[0]
    if mercado == "golos":
        aposta_txt = f"{s.sugestao.lado.upper()} {s.sugestao.linha} golos"
        emoji = "⚽"
    elif mercado == "1x2":
        rot = {"casa": "1 (casa)", "empate": "X (empate)", "fora": "2 (fora)"}
        aposta_txt = f"1X2 {rot.get(s.sugestao.lado, s.sugestao.lado)}"
        emoji = "🏆"
    elif mercado == "btts":
        aposta_txt = f"BTTS {s.sugestao.lado.upper()}"
        emoji = "🔃"
    elif mercado == "dupla":
        aposta_txt = f"Dupla hipotese {s.sugestao.lado}"
        emoji = "⚡"
    elif mercado == "handicap":
        aposta_txt = f"Handicap {s.sugestao.lado} {s.sugestao.linha}"
        emoji = "⚖"
    else:
        aposta_txt = f"{s.sugestao.lado} {getattr(s.sugestao, 'linha', '') or ''}"
        emoji = "📈"

    ev_pct = s.sugestao.ev * 100
    prob = s.prob_fair * 100
    odd_target = s.odd_soft
    odd_min = round(1.0 / max(s.prob_fair, 0.01), 2)

    estrelas = "⭐" * min(5, max(1, int(ev_pct / 3)))
    linhas = [
        f"{estrelas} {emoji} *PRE-MATCH* · {aposta_txt}  ·  {prob:.0f}%",
        f"⚽ {s.casa.nome} vs {s.fora.nome}",
        f"🏆 {s.liga.nome}  ·  🕒 {data}",
        "",
        f"📊 Pinnacle fair: *{odd_min}*  |  {s.casa_soft}: *{odd_target:.2f}*  → EV *+{ev_pct:.1f}%*",
        "_Odd min: paga mais que o fair._",
        f"Analista Pre-Match · {emoji}",
    ]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--janela-horas", type=int, default=24,
                        help="Jogos nas proximas N horas (default 24)")
    parser.add_argument("--ev-min", type=float, default=None,
                        help="EV minimo (default do config.yaml)")
    parser.add_argument("--telegram", action="store_true",
                        help="Envia; sem a flag, so imprime.")
    parser.add_argument("--mercados", default="golos,1x2",
                        help="Virgulas: golos,1x2,btts,dupla,handicap")
    parser.add_argument("--sem-sincronizar", action="store_true",
                        help="Nao chama odds_api.sincronizar; usa BD actual.")
    args = parser.parse_args()

    if not args.sem_sincronizar:
        try:
            from apostas.ingestao import odds_api
            cfg = load_config()
            bookies = cfg.get("odds_api", {}).get(
                "bookmakers", "pinnacle,betano,bet365,williamhill,unibet",
            )
            regions = cfg.get("odds_api", {}).get("regions", "eu")
            log.info("Puxando odds frescas da Odds API...")
            res = odds_api.sincronizar(
                bookmakers=bookies, regions=regions, modo="live",
            )
            log.info("Odds API: %s", res)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Odds API sync falhou (%s). Continuando com odds em BD...",
                exc,
            )

    mercados = tuple(m.strip() for m in args.mercados.split(",") if m.strip())
    log.info("Analisar janela %dh, mercados=%s, ev_min=%s",
             args.janela_horas, mercados, args.ev_min)

    sugestoes = analise_softs.identificar_sugestoes_softs(
        referencia=agora_utc(),
        janela_horas=(0.0, float(args.janela_horas)),
        mercados=mercados,
        ev_minimo_override=args.ev_min,
    )

    if not sugestoes:
        print("Sem sugestoes pre-match com EV.")
        return 0

    csv_path = project_root() / "dados" / "tracking" / _CSV_ENVIADOS
    enviados = _carregar_enviados(csv_path)
    novas = [s for s in sugestoes if _chave_sugestao(s) not in enviados]

    print(f"\n{len(sugestoes)} sugestoes total | {len(novas)} novas (ainda nao enviadas)")

    cliente = None
    if args.telegram and novas:
        try:
            from apostas.alertas.telegram_cliente import cliente as _c
            cliente = _c()
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram desactivado: %s", exc)
            cliente = None

    enviadas = 0
    for s in novas:
        msg = _formatar_telegram(s)
        print("\n" + "-" * 60)
        print(msg)
        if cliente is not None:
            try:
                cliente.enviar(msg)
                enviadas += 1
                _gravar_enviado(csv_path, _chave_sugestao(s), s)
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha envio Telegram para %s: %s", s.jogo.id, exc)
        else:
            # Mesmo sem enviar, gravar chave para evitar reenvio futuro
            _gravar_enviado(csv_path, _chave_sugestao(s), s)

    print(f"\n{enviadas} alertas enviadas via Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
