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

from apostas.alertas import analista, telegram_live  # noqa: E402
from apostas.ingestao import _flaresolverr, sofascore_live  # noqa: E402
from apostas.ingestao.odds_api import LIGAS_ODDS_API, _ALIAS_ODDS_API, _parse_commence_time  # noqa: E402
from apostas.modelos import live_predictor  # noqa: E402
from apostas.modelos.form_ratings import RatingsForma, expectativa, shrinkage  # noqa: E402
from apostas.modelos.live_predictor import ev_contra_odd  # noqa: E402
from apostas.utils.config import get_env, project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import Equipa, EstadoLiveSnapshot, Liga  # noqa: E402

log = get_logger(__name__)

# Dedup entre ciclos para notificacoes Telegram.
# chave = (event_id, mercado, lado, linha) → datetime do ultimo envio.
# Impede re-notificar o mesmo sinal dentro de COOLDOWN_MIN minutos.
_cooldown: dict[tuple, datetime] = {}
COOLDOWN_MIN = 15


def _purgar_cooldown() -> None:
    """Remove entradas antigas (>COOLDOWN_MIN) do dict de dedup."""
    agora = datetime.utcnow()
    limite = COOLDOWN_MIN * 60
    expiradas = [k for k, ts in _cooldown.items()
                 if (agora - ts).total_seconds() > limite]
    for k in expiradas:
        _cooldown.pop(k, None)


def _em_cooldown(event_id, mercado, lado, linha) -> bool:
    """True se ja mandamos este sinal nos ultimos COOLDOWN_MIN minutos."""
    key = (str(event_id), mercado, lado, str(linha))
    ts = _cooldown.get(key)
    if ts is None:
        return False
    return (datetime.utcnow() - ts).total_seconds() < COOLDOWN_MIN * 60


def _marcar_enviado(event_id, mercado, lado, linha) -> None:
    key = (str(event_id), mercado, lado, str(linha))
    _cooldown[key] = datetime.utcnow()

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Ligas: usamos IDs internos Sofascore para precisao (ha "Premier League"
# em Uganda, Russia, Burundi... o filtro por nome apanhava-as).
# Para ver os IDs ou adicionar mais ligas, le o LIGAS_SOFASCORE_IDS.
IDS_LIGAS_DEFAULT = tuple(sofascore_live.LIGAS_SOFASCORE_IDS.values()) if hasattr(sofascore_live, "LIGAS_SOFASCORE_IDS") else ()

CSV_COLUNAS = [
    "ts_scan", "event_id", "liga", "home", "away", "minuto", "marcador",
    "mercado", "lado", "linha", "casa", "odd",
    "prob_modelo", "ev", "xg_casa", "xg_fora",
    "shots_on_target_c", "shots_on_target_f",
    "corners_c", "corners_f", "yellows_c", "yellows_f",
    # Preenchidos por resolver_sugestoes_live.py:
    "resultado", "lucro", "resolvido_em",
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
        # extrasaction="ignore" permite dicts com campos extra (titulo,
        # racional_str) que sao usados em memoria mas nao vao para CSV.
        writer = csv.DictWriter(fp, fieldnames=CSV_COLUNAS, extrasaction="ignore")
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
    """Mapeia nome Sofascore → sport_key Odds API.

    Match substring (lowercase) para apanhar variacoes como:
      "LaLiga EA SPORTS"  → soccer_spain_la_liga
      "Liga Portugal Betclic" → soccer_portugal_primeira_liga
    """
    if not liga_nome:
        return None
    n = liga_nome.lower()
    pares = [
        ("premier league", "soccer_epl"),
        ("laliga", "soccer_spain_la_liga"),      # cobre "LaLiga EA Sports"
        ("la liga", "soccer_spain_la_liga"),
        ("serie a", "soccer_italy_serie_a"),
        ("bundesliga", "soccer_germany_bundesliga"),
        ("ligue 1", "soccer_france_ligue_one"),
        ("liga portugal", "soccer_portugal_primeira_liga"),  # cobre "Liga Portugal Betclic"
        ("primeira liga", "soccer_portugal_primeira_liga"),
    ]
    for sub, sport in pares:
        if sub in n:
            return sport
    return None


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
                # Mercados cobertos no modelo: golos (totals), BTTS, dupla,
                # cantos (totals_corners), cartoes (totals_cards), remates
                # (totals_shots). Nem todas as softs oferecem todos — Odds API
                # devolve so os disponiveis sem erro.
                "markets": (
                    "h2h,totals,btts,double_chance,"
                    "totals_corners,alternate_corners_totals,"
                    "totals_cards,alternate_cards_totals,"
                    "totals_shots,alternate_shots_totals,"
                    "totals_shots_on_target"
                ),
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


def _gravar_snapshot(estado) -> None:
    """Grava um snapshot do estado live para calibracao futura.

    Idempotente por (event_id, minuto) — mesmo scanning dois ciclos no
    mesmo minuto nao cria dois snapshots. Minuto 0 e snapshots a cada 2+
    minutos tipicamente.
    """
    from datetime import datetime as _dt
    with abrir_sessao() as s:
        # Dedupe por event+minuto
        existe = s.scalar(select(EstadoLiveSnapshot).where(
            EstadoLiveSnapshot.event_id == int(estado.event_id),
            EstadoLiveSnapshot.minuto == estado.minuto,
        ))
        if existe is not None:
            return
        snap = EstadoLiveSnapshot(
            event_id=int(estado.event_id),
            ts=_dt.utcnow(),
            liga=estado.liga,
            home=estado.home,
            away=estado.away,
            minuto=estado.minuto,
            estado_jogo=str(estado.estado) if estado.estado is not None else None,
            marcador_casa=estado.marcador_casa,
            marcador_fora=estado.marcador_fora,
            xg_casa=estado.xg_casa,
            xg_fora=estado.xg_fora,
            shots_total_casa=estado.shots_total_casa,
            shots_total_fora=estado.shots_total_fora,
            shots_on_target_casa=estado.shots_on_target_casa,
            shots_on_target_fora=estado.shots_on_target_fora,
            shots_blocked_casa=estado.shots_blocked_casa,
            shots_blocked_fora=estado.shots_blocked_fora,
            big_chances_casa=estado.big_chances_casa,
            big_chances_fora=estado.big_chances_fora,
            posse_casa=estado.possession_casa,
            corners_casa=estado.corners_casa,
            corners_fora=estado.corners_fora,
            yellow_casa=estado.yellow_cards_casa,
            yellow_fora=estado.yellow_cards_fora,
            red_casa=estado.red_cards_casa,
            red_fora=estado.red_cards_fora,
            fouls_casa=estado.fouls_casa,
            fouls_fora=estado.fouls_fora,
            arbitro=estado.arbitro,
        )
        s.add(snap)


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


def _linha_live(mercado: str, lado: str, linha: float | None, estado) -> bool:
    """True se a linha ainda pode ir para qualquer lado.

    Filtra: Over 2.5 golos quando jogo esta 4-2 (ja consumado, inutil).
    Over 9.5 cantos quando ja tem 10 (ja ganhou, inutil).
    etc.
    """
    if linha is None:
        return True
    try:
        linha_f = float(linha)
    except (ValueError, TypeError):
        return True

    atuais = None
    if mercado == "golos":
        atuais = estado.marcador_casa + estado.marcador_fora
    elif mercado == "cantos":
        atuais = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    elif mercado == "cartoes":
        atuais = estado.yellow_cards_casa + estado.yellow_cards_fora
    elif mercado == "remates":
        atuais = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    elif mercado == "remates_a_baliza":
        atuais = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)
    elif mercado == "btts":
        # BTTS sim decidido quando ambas ja marcaram
        if lado == "sim" and estado.marcador_casa > 0 and estado.marcador_fora > 0:
            return False
        # BTTS nao decidido quando ambas ja marcaram (perdeu)
        if lado == "nao" and estado.marcador_casa > 0 and estado.marcador_fora > 0:
            return False
        return True

    if atuais is None:
        return True
    # Linhas tipo 2.5, 9.5 — comparacao directa
    if lado == "over" and atuais > linha_f:
        return False  # over ja ganhou
    if lado == "under" and atuais > linha_f:
        return False  # under ja perdeu
    return True


def _analisar_jogo(estado, odds_json: dict, ev_min: float, filtro_casas: tuple[str, ...]) -> list[dict]:
    """Devolve lista de sugestoes (dicts) com EV >= ev_min."""
    sugestoes = []
    exp_pre = _puxar_expectativa(estado)
    prev = live_predictor.prever(estado, expectativa_pre=exp_pre)

    def _emit(mercado, lado, linha, casa, odd, prob):
        # NOVO: skip linhas ja decididas (Over 2.5 num 4-2 = inutil)
        if not _linha_live(mercado, lado, linha, estado):
            return
        ev = ev_contra_odd(prob, odd)
        if ev < ev_min:
            return
        sugestoes.append({
            "ts_scan": datetime.utcnow().isoformat(timespec="seconds"),
            "event_id": estado.event_id,
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
            "resultado": "",
            "lucro": "",
            "resolvido_em": "",
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
                # 1X2: modelo precisa de rate por equipa ainda nao implementado
                continue
            elif key in ("totals_corners", "corner_totals", "alternate_corners_totals"):
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("over", "under"):
                        continue
                    linha = float(o.get("point", 9.5))
                    odd = float(o.get("price", 0))
                    p_over = prev.p_over_cantos_total(linha)
                    p = p_over if nome == "over" else (1 - p_over)
                    _emit("cantos", nome, linha, casa, odd, p)
            elif key in ("totals_cards", "card_totals", "alternate_cards_totals"):
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("over", "under"):
                        continue
                    linha = float(o.get("point", 4.5))
                    odd = float(o.get("price", 0))
                    p_over = prev.p_over_cartoes_total(linha)
                    p = p_over if nome == "over" else (1 - p_over)
                    _emit("cartoes", nome, linha, casa, odd, p)
            elif key in ("totals_shots", "shots_totals", "alternate_shots_totals"):
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("over", "under"):
                        continue
                    linha = float(o.get("point", 24.5))
                    odd = float(o.get("price", 0))
                    p_over = prev.p_over_remates_total(linha)
                    p = p_over if nome == "over" else (1 - p_over)
                    _emit("remates", nome, linha, casa, odd, p)
            elif key in ("totals_shots_on_target", "shots_on_target_totals"):
                for o in mkt.get("outcomes", []):
                    nome = (o.get("name") or "").lower()
                    if nome not in ("over", "under"):
                        continue
                    linha = float(o.get("point", 8.5))
                    odd = float(o.get("price", 0))
                    p_over = prev.p_over_remates_a_baliza_total(linha)
                    p = p_over if nome == "over" else (1 - p_over)
                    _emit("remates_a_baliza", nome, linha, casa, odd, p)
    return sugestoes


def _imprimir_estado(estado, prev) -> None:
    xg_str = ""
    if estado.xg_casa is not None:
        xg_str = f" | xG {estado.xg_casa:.1f}-{estado.xg_fora:.1f}"
    sot = ""
    if estado.shots_on_target_casa is not None:
        sot = f" | SoT {estado.shots_on_target_casa}-{estado.shots_on_target_fora}"
    tot = ""
    if estado.shots_total_casa is not None:
        tot = f" | Tot {estado.shots_total_casa}-{estado.shots_total_fora}"
    cards_s = ""
    if estado.yellow_cards_casa or estado.yellow_cards_fora:
        cards_s = f" | Y {estado.yellow_cards_casa}-{estado.yellow_cards_fora}"
    reds = ""
    if estado.red_cards_casa or estado.red_cards_fora:
        reds = f" R {estado.red_cards_casa}-{estado.red_cards_fora}"
    corners_s = ""
    if estado.corners_casa is not None:
        corners_s = f" | C {estado.corners_casa}-{estado.corners_fora}"
    arb = f" | arb: {estado.arbitro}" if estado.arbitro else ""
    try:
        from apostas.modelos.referencias import eh_derby, fator_arbitro
        derby = " ⚡DERBY" if eh_derby(estado.home, estado.away) else ""
        arb_mult = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
        arb_tag = f" (x{arb_mult:.2f})" if abs(arb_mult - 1.0) > 0.05 else ""
    except Exception:  # noqa: BLE001
        derby = ""
        arb_tag = ""
    print(
        f"  {estado.home} vs {estado.away} ({estado.liga}){derby} "
        f"[{estado.minuto}'] {estado.marcador_casa}-{estado.marcador_fora}"
        f"{xg_str}{sot}{tot}{corners_s}{cards_s}{reds}{arb}{arb_tag}"
    )
    # Modelo mostra probabilidade de total final bater as linhas
    # tipicas de mercado (nao "mais +N nos restantes", que ao min 3' seria
    # sempre quase 100% e confunde). As linhas sao as mais liquidas no
    # pre-match / early live.
    print(
        f"    → model: "
        f"P(O2.5 gol)={prev.p_over_total(2.5)*100:.1f}%  "
        f"P(BTTS)={prev.p_btts_final*100:.1f}%  "
        f"P(O9.5 cnt)={prev.p_over_cantos_total(9.5)*100:.1f}%  "
        f"P(O4.5 crd)={prev.p_over_cartoes_total(4.5)*100:.1f}%  "
        f"P(O24.5 rem)={prev.p_over_remates_total(24.5)*100:.1f}%  "
        f"P(O8.5 SoT)={prev.p_over_remates_a_baliza_total(8.5)*100:.1f}%"
    )


def _imprimir_sinais(sinais) -> None:
    """Imprime sinais do analista (sem odds)."""
    print(f"    🎯 {len(sinais)} sinal(ais) do analista:")
    for s in sorted(sinais, key=lambda x: -x.prob_modelo):
        tag = {"forte": "★★★", "normal": "★★", "fraco": "★"}.get(s.forca, "")
        aposta = _nome_aposta(s.mercado, s.lado, s.linha)
        print(f"      {tag} [{s.tipo}] {aposta}")
        print(f"          prob={s.prob_modelo*100:.1f}%  "
              f"odd_min={s.odd_minima}  odd_valor(+5% EV)={s.odd_valor}")
        for r in s.racional[:3]:  # top 3 razoes
            print(f"          · {r}")


def _nome_aposta(mercado: str, lado: str, linha: float | None) -> str:
    linha_str = f" {linha}" if linha is not None else ""
    if mercado == "golos":
        return f"{lado.upper()}{linha_str} golos"
    if mercado == "cantos":
        return f"{lado.upper()}{linha_str} cantos"
    if mercado == "cartoes":
        return f"{lado.upper()}{linha_str} cartoes amarelos"
    if mercado == "btts":
        return f"BTTS {'SIM' if lado == 'sim' else 'NAO'}"
    if mercado == "remates":
        return f"{lado.upper()}{linha_str} remates"
    if mercado == "remates_a_baliza":
        return f"{lado.upper()}{linha_str} remates a baliza"
    return f"{mercado} {lado}{linha_str}"


def _sinal_para_sugestao(estado, sinal) -> dict:
    """Converte Sinal numa linha de sugestao para o CSV."""
    racional_str = "\n".join(f"· {r}" for r in sinal.racional) if sinal.racional else ""
    return {
        "ts_scan": datetime.utcnow().isoformat(timespec="seconds"),
        "event_id": estado.event_id,
        "liga": estado.liga,
        "home": estado.home,
        "away": estado.away,
        "minuto": estado.minuto,
        "marcador": f"{estado.marcador_casa}-{estado.marcador_fora}",
        "mercado": sinal.mercado,
        "lado": sinal.lado,
        "linha": "" if sinal.linha is None else str(sinal.linha),
        "casa": "ANALISTA",
        "odd": sinal.odd_valor,
        "prob_modelo": round(sinal.prob_modelo, 4),
        "ev": "",
        "xg_casa": estado.xg_casa or "",
        "xg_fora": estado.xg_fora or "",
        "shots_on_target_c": estado.shots_on_target_casa or "",
        "shots_on_target_f": estado.shots_on_target_fora or "",
        "corners_c": estado.corners_casa or "",
        "corners_f": estado.corners_fora or "",
        "yellows_c": estado.yellow_cards_casa,
        "yellows_f": estado.yellow_cards_fora,
        "resultado": "", "lucro": "", "resolvido_em": "",
        # Nao vai para CSV mas e passado ao Telegram em memoria:
        "racional_str": racional_str,
        "titulo": sinal.titulo,
    }


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
        # Grava snapshot do estado para historico/calibracao futura (Nivel 2)
        try:
            _gravar_snapshot(estado)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha gravar snapshot %s: %s", estado.event_id, exc)
        # Modelo corre SEMPRE, nao depende de ter odds da Odds API.
        prev = live_predictor.prever(estado)
        _imprimir_estado(estado, prev)

        # Odds API: so para computar EV (classico) — opcional.
        sport = _sport_da_liga(estado.liga)
        if sport is None:
            print(f"    (liga '{estado.liga}' sem mapeamento Odds API — so modo analista)")
            odds_json = None
        else:
            try:
                odds_json = _odds_api_live_evento(sport, estado.home, estado.away)
            except Exception as exc:  # noqa: BLE001
                log.warning("Odds API erro para %s vs %s: %s", estado.home, estado.away, exc)
                odds_json = None

        # MODO ANALISTA: gera sinais do modelo independentes de haver odds.
        # Serve dois casos:
        # (1) Odds API nao devolve odds live para este jogo
        # (2) Mesmo com odds, sinal do modelo ja tem valor como analise
        sinais = analista.gerar_sinais(estado, prev, prob_minima=args.prob_minima)
        if sinais:
            _imprimir_sinais(sinais)
            # Converte sinais em dicts (sem EV porque sem odds)
            for sinal in sinais:
                sug = _sinal_para_sugestao(estado, sinal)
                todas_sugestoes.append(sug)

        if odds_json is None:
            if not sinais:
                print(f"    (sem odds live Odds API e sem sinais do modelo)")
            continue

        # Com odds disponiveis: analise classica EV-based
        sugs = _analisar_jogo(estado, odds_json, args.ev_minimo, tuple(args.casas) if args.casas else ())
        _imprimir_sugestoes(sugs)
        todas_sugestoes.extend(sugs)

    if todas_sugestoes:
        adic = _gravar_novas(todas_sugestoes, _ficheiro_csv())
        print(f"  → {adic} novas gravadas (de {len(todas_sugestoes)} totais)")
        # DEDUP entre ciclos: evita re-enviar notificacao do mesmo sinal
        # dentro de 15 minutos (user ja viu, nao precisa de repeticao).
        _purgar_cooldown()
        if args.telegram:
            # Dois tipos de notificacao:
            # 1. Sugestoes com odds reais (coluna 'ev' numerica): filtra por EV
            # 2. Sinais puros do analista (ev='' e casa='ANALISTA'): filtra por prob_modelo
            ev_min_telegram = max(args.ev_minimo, args.ev_minimo_telegram)
            prob_min_telegram = args.prob_minima
            notificar = []
            for s in todas_sugestoes:
                if s.get("casa") == "ANALISTA":
                    # Sinal puro: prob_modelo >= limiar
                    if float(s.get("prob_modelo", 0)) >= prob_min_telegram:
                        notificar.append(s)
                else:
                    # Sugestao com EV real
                    try:
                        ev_val = float(s.get("ev", 0))
                    except (ValueError, TypeError):
                        continue
                    if ev_val >= ev_min_telegram:
                        notificar.append(s)
            # Deduplica entre ciclos: 1 notificacao por (event+mercado+lado+linha)
            # a cada COOLDOWN_MIN min. Alem disso, limite de 1 sinal por jogo
            # por ciclo (so o mais forte).
            def _sort_key(s):
                try:
                    return -float(s.get("ev") or s.get("prob_modelo") or 0)
                except (ValueError, TypeError):
                    return 0
            enviadas = 0
            jogos_notificados_este_ciclo = set()
            for s in sorted(notificar, key=_sort_key):
                event_id = s.get("event_id")
                mercado = s["mercado"]
                lado = s["lado"]
                linha = s.get("linha", "")
                jogo_key = (s["home"], s["away"])
                # Cooldown dedup entre ciclos
                if _em_cooldown(event_id, mercado, lado, linha):
                    continue
                # Max 1 notificacao por jogo por ciclo (sinal mais forte)
                if jogo_key in jogos_notificados_este_ciclo:
                    continue
                telegram_live.enviar_sugestao_live(s)
                _marcar_enviado(event_id, mercado, lado, linha)
                jogos_notificados_este_ciclo.add(jogo_key)
                enviadas += 1
            if enviadas:
                print(f"  → {enviadas} notificacoes Telegram enviadas "
                      f"({len(notificar) - enviadas} em cooldown ou duplicados)")


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
    parser.add_argument("--prob-minima", type=float, default=0.70,
                        help="Prob minima para o analista emitir sinal (default 0.70 = 70 por cento). "
                             "Sinais do analista aparecem mesmo sem odds da Odds API. "
                             "Baixa para 0.55 para veres mais fluxo.")
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
