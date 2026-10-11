"""Scanner LIVE v2: modelo preditivo + odds live.

Fluxo por ciclo:
  1. Puxa jogos ao vivo da Sofascore (via flaresolverr).
  2. Filtra os que pertencem às ligas que seguimos.
  3. Para cada jogo in-play:
     a. Obtém estado completo (xG, shots, cantos, cards, posse).
     b. Puxa odds live Over/Under + BTTS + 1X2 da Odds API via
        /sports/{sport}/events/{id}/odds.
     c. Corre o modelo (prever).
     d. Para cada mercado, calcula EV = P_modelo × (odd-1) - (1-P_modelo).
     e. Imprime (e grava em CSV) todas as oportunidades com EV ≥ ev_min.

CRÍTICO vs scanner_live v1:
  v1 usava Pinnacle como fair. Assume que Pinnacle já incorporou o
  estado do jogo. É verdade em grande medida, mas Pinnacle só é
  pedido no mesmo ciclo e a janela temporal pode mentir.
  v2 usa modelo matemático directamente sobre os dados do jogo. É
  independente da Pinnacle. Podes comparar com qualquer casa, incluindo
  a própria Pinnacle (serve para auditar o modelo contra ela).

Setup necessário no PC do utilizador:
  - Docker Desktop ligado
  - `docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest`
  - ODDS_API_KEY no .env (ja tens)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from sqlalchemy import select  # noqa: E402

from apostas.alertas import telegram_live  # noqa: E402
from apostas.ingestao import _flaresolverr, sofascore_live  # noqa: E402
from apostas.ingestao.odds_api import LIGAS_ODDS_API, _ALIAS_ODDS_API, _parse_commence_time  # noqa: E402
from apostas.modelos import live_predictor  # noqa: E402
from apostas.modelos.form_ratings import RatingsForma, expectativa, shrinkage  # noqa: E402
from apostas.modelos.live_predictor import ev_contra_odd  # noqa: E402
from apostas.utils.config import get_env, project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import Equipa, Liga  # noqa: E402

log = get_logger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Ligas: usamos IDs internos Sofascore para precisao (ha "Premier League"
# em Uganda, Russia, Burundi... o filtro por nome apanhava-as).
# Para ver os IDs ou adicionar mais ligas, le o LIGAS_SOFASCORE_IDS.
IDS_LIGAS_DEFAULT = tuple(sofascore_live.LIGAS_SOFASCORE_IDS.values()) if hasattr(sofascore_live, "LIGAS_SOFASCORE_IDS") else ()

CSV_COLUNAS = [
    "ts_scan", "liga", "home", "away", "minuto", "marcador",
    "mercado", "lado", "linha", "casa", "odd",
    "prob_modelo", "ev", "xg_casa", "xg_fora",
    "shots_on_target_c", "shots_on_target_f",
    "corners_c", "corners_f", "yellows_c", "yellows_f",
]


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _carregar_vistas(f: Path) -> set[tuple]:
    """Idempotencia: 1 sugestao por (event_id, mercado, lado, linha, casa, minuto)."""
    if not f.exists():
        return set()
    vistas = set()
    with f.open(encoding="utf-8") as fp:
        for linha in csv.DictReader(fp):
            vistas.add((
                linha["home"] + "|" + linha["away"],
                linha["mercado"], linha["lado"], linha["linha"],
                linha["casa"], linha["minuto"],
            ))
    return vistas


def _gravar_novas(sugestoes_dicts, f: Path) -> int:
    f.parent.mkdir(parents=True, exist_ok=True)
    vistas = _carregar_vistas(f)
    novo = not f.exists()
    adicionadas = 0
    with f.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUNAS)
        if novo:
            writer.writeheader()
        for s in sugestoes_dicts:
            key = (
                s["home"] + "|" + s["away"], s["mercado"], s["lado"],
                str(s["linha"]), s["casa"], str(s["minuto"]),
            )
            if key in vistas:
                continue
            writer.writerow(s)
            adicionadas += 1
    return adicionadas


# ───────────── Puxar odds live via Odds API /events ─────────────

def _sport_da_liga(liga_nome: str) -> str | None:
    """Mapeia nome Sofascore → sport_key Odds API."""
    mapa = {
        "Premier League": "soccer_epl",
        "LaLiga": "soccer_spain_la_liga",
        "La Liga": "soccer_spain_la_liga",
        "Serie A": "soccer_italy_serie_a",
        "Bundesliga": "soccer_germany_bundesliga",
        "Ligue 1": "soccer_france_ligue_one",
        "Primeira Liga": "soccer_portugal_primeira_liga",
        "Liga Portugal": "soccer_portugal_primeira_liga",
    }
    return mapa.get(liga_nome)


def _odds_api_live_evento(sport: str, home: str, away: str) -> dict | None:
    """Tenta encontrar o evento Odds API para home/away via /events,
    depois puxa /events/{id}/odds com os mercados live.
    """
    api_key = get_env("ODDS_API_KEY", required=True)
    url = f"{_ODDS_API_BASE}/sports/{sport}/events"
    r = requests.get(url, params={"apiKey": api_key}, timeout=20)
    if not r.ok:
        return None
    agora = datetime.utcnow()
    for ev in r.json() if isinstance(r.json(), list) else []:
        h = (ev.get("home_team") or "").lower()
        a = (ev.get("away_team") or "").lower()
        home_l = home.lower()
        away_l = away.lower()
        # Match flexivel: substring ou substring reverso
        if (home_l in h or h in home_l) and (away_l in a or a in away_l):
            event_id = ev.get("id")
            if event_id is None:
                return None
            # Verifica que o jogo esta mesmo in-play (comecou nas ultimas 2.5h)
            ts = _parse_commence_time((ev.get("commence_time") or "").replace("+00:00", "Z"))
            if ts is None or (agora - ts).total_seconds() > 150 * 60:
                return None
            odds_url = f"{_ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds"
            params = {
                "apiKey": api_key,
                "regions": "eu,uk",
                "markets": "h2h,totals,btts,double_chance",
                "oddsFormat": "decimal",
            }
            r2 = requests.get(odds_url, params=params, timeout=20)
            if r2.status_code == 404:
                return None
            if not r2.ok:
                return None
            return r2.json()
    return None


def _nome_db(sofa_nome: str) -> str:
    """Aplica o alias Odds API-like para converter nomes Sofascore em nomes DB."""
    return _ALIAS_ODDS_API.get(sofa_nome, sofa_nome)


def _puxar_expectativa(estado) -> "object | None":
    """Procura ratings historicos para as 2 equipas; devolve ExpectativaJogo."""
    with abrir_sessao() as s:
        # Procura liga pela heuristica do nome
        liga_nome_candidatos = [
            estado.liga,
            estado.liga.replace("LaLiga", "La Liga"),
            estado.liga.replace("Primeira Liga", "Liga Portugal"),
        ]
        liga = None
        for cand in liga_nome_candidatos:
            liga = s.scalar(select(Liga).where(Liga.nome == cand))
            if liga:
                break
        if liga is None:
            return None
        nome_c = _nome_db(estado.home)
        nome_f = _nome_db(estado.away)
        casa = s.scalar(select(Equipa).where(
            Equipa.liga_id == liga.id, Equipa.nome == nome_c,
        ))
        fora = s.scalar(select(Equipa).where(
            Equipa.liga_id == liga.id, Equipa.nome == nome_f,
        ))
        if casa is None or fora is None:
            return None
        casa_id, fora_id = casa.id, fora.id
    r = RatingsForma(max_jogos=20)
    rc = shrinkage(r.rating(casa_id, "casa"))
    rf = shrinkage(r.rating(fora_id, "fora"))
    return expectativa(rc, rf)


def _analisar_jogo(estado, odds_json: dict, ev_min: float, filtro_casas: tuple[str, ...]) -> list[dict]:
    """Devolve lista de sugestoes (dicts) com EV >= ev_min."""
    sugestoes = []
    exp_pre = _puxar_expectativa(estado)
    prev = live_predictor.prever(estado, expectativa_pre=exp_pre)

    def _emit(mercado, lado, linha, casa, odd, prob):
        ev = ev_contra_odd(prob, odd)
        if ev < ev_min:
            return
        sugestoes.append({
            "ts_scan": datetime.utcnow().isoformat(timespec="seconds"),
            "liga": estado.liga,
            "home": estado.home,
            "away": estado.away,
            "minuto": estado.minuto,
            "marcador": f"{estado.marcador_casa}-{estado.marcador_fora}",
            "mercado": mercado,
            "lado": lado,
            "linha": "" if linha is None else str(linha),
            "casa": casa,
            "odd": round(odd, 2),
            "prob_modelo": round(prob, 4),
            "ev": round(ev, 4),
            "xg_casa": estado.xg_casa if estado.xg_casa is not None else "",
            "xg_fora": estado.xg_fora if estado.xg_fora is not None else "",
            "shots_on_target_c": estado.shots_on_target_casa or "",
            "shots_on_target_f": estado.shots_on_target_fora or "",
            "corners_c": estado.corners_casa or "",
            "corners_f": estado.corners_fora or "",
            "yellows_c": estado.yellow_cards_casa,
            "yellows_f": estado.yellow_cards_fora,
        })

    for bm in odds_json.get("bookmakers", []):
        casa = bm.get("key", "?")
        if filtro_casas and casa not in filtro_casas:
            continue
        for mkt in bm.get("markets", []):
            key = mkt.get("key")
            if key == "totals":
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("over", "under"):
                        continue
                    linha = float(o.get("point", 2.5))
                    odd = float(o.get("price", 0))
                    p_over = prev.p_over_total(linha)
                    p = p_over if nome == "over" else (1 - p_over)
                    _emit("golos", nome, linha, casa, odd, p)
            elif key == "btts":
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("yes", "no"):
                        continue
                    odd = float(o.get("price", 0))
                    p = prev.p_btts_final if nome == "yes" else (1 - prev.p_btts_final)
                    _emit("btts", "sim" if nome == "yes" else "nao", None, casa, odd, p)
            elif key == "h2h":
                # 1X2: so interessa se o jogo ainda nao esta definido
                # (empate 90min aos 85' = model deve estar OK)
                # Suprimimos por agora: modelo 1X2 live precisa de rate por equipa
                continue
    return sugestoes


def _imprimir_estado(estado, prev) -> None:
    xg_str = ""
    if estado.xg_casa is not None:
        xg_str = f" | xG {estado.xg_casa:.1f}-{estado.xg_fora:.1f}"
    sot = ""
    if estado.shots_on_target_casa is not None:
        sot = f" | SoT {estado.shots_on_target_casa}-{estado.shots_on_target_fora}"
    cards = ""
    if estado.yellow_cards_casa or estado.yellow_cards_fora:
        cards = f" | Y {estado.yellow_cards_casa}-{estado.yellow_cards_fora}"
    print(
        f"  {estado.home} vs {estado.away} ({estado.liga}) "
        f"[{estado.minuto}'] {estado.marcador_casa}-{estado.marcador_fora}"
        f"{xg_str}{sot}{cards}"
    )
    print(
        f"    → model: P(+1 gol)={prev.p_mais_1_golo*100:.1f}%  "
        f"P(Over 2.5)={prev.p_over_total(2.5)*100:.1f}%  "
        f"P(BTTS)={prev.p_btts_final*100:.1f}%  "
        f"P(+2 cantos)={prev.p_mais_cantos(2)*100:.1f}%  "
        f"P(+1 card)={prev.p_mais_cartoes_amarelos(1)*100:.1f}%"
    )


def _imprimir_sugestoes(sugs) -> None:
    if not sugs:
        return
    print(f"    ✦ {len(sugs)} oportunidade(s):")
    for s in sorted(sugs, key=lambda x: -x["ev"]):
        print(
            f"      [{s['mercado']}] {s['lado']} {s['linha']:>5} @ {s['odd']}  "
            f"({s['casa']})  prob_mod={s['prob_modelo']*100:.1f}%  "
            f"EV={s['ev']*100:+.1f}%"
        )


def _correr_ciclo(args) -> None:
    agora = datetime.utcnow().strftime("%H:%M:%S")
    print(f"\n[{agora}] A listar jogos live...")
    jogos = sofascore_live.listar_jogos_live(
        ligas_filter=tuple(args.ligas) if args.ligas else None,
        ids_ligas=IDS_LIGAS_DEFAULT if not args.ligas else None,
    )
    if not jogos:
        print("    (sem jogos live nas ligas que seguimos)")
        return
    print(f"    {len(jogos)} jogos encontrados.")

    todas_sugestoes = []
    for ev in jogos:
        event_id = ev.get("id")
        if not event_id:
            continue
        try:
            estado = sofascore_live.obter_estado_live(event_id)
        except _flaresolverr.FlaresolverrIndisponivel as exc:
            print(f"    FLARESOLVERR OFF: {exc}")
            return
        if estado is None:
            continue
        # Puxa odds da Odds API
        sport = _sport_da_liga(estado.liga)
        if sport is None:
            continue
        try:
            odds_json = _odds_api_live_evento(sport, estado.home, estado.away)
        except Exception as exc:  # noqa: BLE001
            log.warning("Odds API erro para %s vs %s: %s", estado.home, estado.away, exc)
            odds_json = None
        prev = live_predictor.prever(estado)
        _imprimir_estado(estado, prev)
        if odds_json is None:
            print(f"    (sem odds live Odds API para este jogo)")
            continue
        sugs = _analisar_jogo(estado, odds_json, args.ev_minimo, tuple(args.casas) if args.casas else ())
        _imprimir_sugestoes(sugs)
        todas_sugestoes.extend(sugs)

    if todas_sugestoes:
        adic = _gravar_novas(todas_sugestoes, _ficheiro_csv())
        print(f"  → {adic} novas gravadas (de {len(todas_sugestoes)} totais)")
        if adic > 0 and args.telegram:
            # Envia so as NOVAS (nao as repetidas de scans anteriores)
            # Reload do CSV? Simples: enviamos as de agora acima do limiar.
            ev_min_telegram = max(args.ev_minimo, args.ev_minimo_telegram)
            notificar = [s for s in todas_sugestoes if s["ev"] >= ev_min_telegram]
            # Deduplica em memoria: 1 notificacao por (jogo+mercado+lado) por scan
            vistas = set()
            for s in sorted(notificar, key=lambda x: -x["ev"]):
                key = (s["home"], s["away"], s["mercado"], s["lado"], s["linha"])
                if key in vistas:
                    continue
                vistas.add(key)
                telegram_live.enviar_sugestao_live(s)
            if notificar:
                print(f"  → {len(vistas)} notificacoes Telegram enviadas")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scanner LIVE v2 com modelo preditivo + odds live.",
    )
    parser.add_argument("--intervalo-s", type=int, default=180,
                        help="Segundos entre ciclos (default 180 = 3min).")
    parser.add_argument("--ciclos", type=int, default=0,
                        help="0 = infinito; sai com Ctrl+C.")
    parser.add_argument("--ev-minimo", type=float, default=0.05,
                        help="EV minimo para emitir sugestao (default 0.05 = 5 por cento).")
    parser.add_argument("--ligas", nargs="*", default=None,
                        help="Ligas (substring do tournament.name Sofascore). "
                             "Default: Big-5 + Liga Portugal.")
    parser.add_argument("--casas", nargs="*", default=None,
                        help="Keys Odds API de casas a considerar. Vazio = todas.")
    parser.add_argument("--telegram", action="store_true",
                        help="Envia notificacoes Telegram para sugestoes >= --ev-minimo-telegram.")
    parser.add_argument("--ev-minimo-telegram", type=float, default=0.07,
                        help="EV min para notificar via Telegram (default 0.07 = 7 por cento). "
                             "So notifica as de topo para evitar spam.")
    args = parser.parse_args()

    print("Scanner LIVE v2 iniciado.")
    print(f"  Intervalo: {args.intervalo_s}s  EV min: {args.ev_minimo*100:.0f} por cento")
    print(f"  Flaresolverr: {_flaresolverr._url_flaresolverr()}")
    print("Sai com Ctrl+C.\n")

    try:
        ciclo = 0
        while True:
            ciclo += 1
            try:
                _correr_ciclo(args)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro ciclo %d", ciclo)
                print(f"    ERRO ciclo {ciclo}: {exc}")
            if args.ciclos and ciclo >= args.ciclos:
                break
            time.sleep(args.intervalo_s)
    except KeyboardInterrupt:
        print("\nInterrompido. A fechar sessao flaresolverr...")
    finally:
        _flaresolverr.encerrar_sessao()


if __name__ == "__main__":
    main()
