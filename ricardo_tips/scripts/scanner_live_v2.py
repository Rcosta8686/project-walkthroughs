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

# Forca UTF-8 (Windows cp1252 nao aceita emojis quando stdout e redirecionado)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apostas.utils.tempo import agora_utc  # noqa: E402

import requests  # noqa: E402

from sqlalchemy import select  # noqa: E402

from apostas.alertas import (  # noqa: E402
    analista, cerebro_live, dual_sinal, filtro_desempenho, filtros_mercado,
    paises_banidos, telegram_live,
)
from apostas.alertas.telegram_cliente import cliente as telegram_cliente  # noqa: E402
from apostas.ingestao import _flaresolverr, sofascore_live  # noqa: E402
from apostas.ingestao.odds_api import LIGAS_ODDS_API, _ALIAS_ODDS_API, _parse_commence_time  # noqa: E402
from apostas.modelos import live_predictor, projecao_live  # noqa: E402
from apostas.modelos.form_ratings import RatingsForma, expectativa, shrinkage  # noqa: E402
from apostas.modelos.live_predictor import ev_contra_odd  # noqa: E402
from apostas.utils.config import get_env, project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import Equipa, EstadoLiveSnapshot, Liga, ProjecaoLive  # noqa: E402
from apostas.modelos.calibrador_mercado import Calibrador, treinar  # noqa: E402

log = get_logger(__name__)

# Dedup entre ciclos para notificacoes Telegram.
# chave = (event_id, mercado, lado) → datetime do ultimo envio.
# NOTA: a linha NAO entra na chave. Caso contrario, OVER 6.5 remates e
# OVER 8.5 remates do mesmo jogo seriam contadas como alertas distintos
# — o utilizador recebia 3-4 alertas do mesmo mercado em 10 min a medida
# que o modelo re-projecta. Agora: 1 alerta por (jogo, mercado, lado)
# dentro da janela de cooldown.
# Impede re-notificar o mesmo sinal dentro de COOLDOWN_MIN minutos.
_cooldown: dict[tuple, datetime] = {}
COOLDOWN_MIN = 30

# Projeccoes ja enviadas: event_id -> set(minutos_target: {40, 55, 70})
# Impede enviar duas vezes a projeccao do mesmo target no mesmo jogo.
_projecoes_enviadas: dict[int, set[int]] = {}

# Primeiro minuto em que vimos cada jogo. Se > 15, o scanner apanhou-o
# tarde (restart ou arrancou pelo meio) — adicionamos aviso na projecao.
_primeiro_minuto_visto: dict[int, int] = {}

# Limiares do modo exigente
_EV_MIN_EXIGENTE = 0.10
_CONFIANCA_MEGA_MIN = 2
_JOGOS_FICHA_MIN = 5


def _passa_modo_exigente(s: dict) -> bool:
    """Filtro multi-camada para o modo exigente.

    Exige em simultaneo:
      - minuto < 88 (90'+ nao da tempo util para a aposta)
      - EV >= 0.10
      - mega-cerebro emite sintese com confianca >= 2
      - ambas as fichas das equipas com >= 5 jogos

    Devolve True se o sinal passa todos os filtros.
    """
    # 0. Minuto — jogos nos descontos nao oferecem edge real
    try:
        minuto_sinal = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        minuto_sinal = 0
    if minuto_sinal >= 88:
        return False

    # 1. EV
    try:
        ev = float(s.get("ev", 0) or 0)
    except (TypeError, ValueError):
        return False
    if ev < _EV_MIN_EXIGENTE:
        return False

    # 2. IDs necessarios
    home_id = s.get("home_id")
    away_id = s.get("away_id")
    if not home_id or not away_id:
        return False

    # 3. Fichas + mega-cerebro — reutiliza confronto + mega.sintetizar_a_partir_confronto
    try:
        from apostas.alertas.confronto import analisar_confronto
        from apostas.alertas import mega_cerebro
    except Exception:  # noqa: BLE001
        return False
    try:
        _mc, _mf = str(s.get("marcador") or "0-0").split("-", 1)
        sc_c, sc_f = int(_mc.strip()), int(_mf.strip())
    except (ValueError, AttributeError):
        sc_c = sc_f = 0
    try:
        minuto = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        minuto = 0
    try:
        res = analisar_confronto(
            int(home_id), int(away_id),
            minuto=minuto, score_casa=sc_c, score_fora=sc_f,
            liga_nome=s.get("liga"),
        )
    except Exception:  # noqa: BLE001
        return False
    if res is None:
        return False
    # Fichas com dados minimos
    try:
        if (res.ficha_casa.jogos_lidos < _JOGOS_FICHA_MIN
                or res.ficha_fora.jogos_lidos < _JOGOS_FICHA_MIN):
            return False
    except AttributeError:
        return False

    # Mega-cerebro
    try:
        sint = mega_cerebro.sintetizar_a_partir_confronto(
            minuto=minuto, score_casa=sc_c, score_fora=sc_f,
            sot_c=s.get("shots_on_target_c"), sot_f=s.get("shots_on_target_f"),
            cantos_c=s.get("corners_c"), cantos_f=s.get("corners_f"),
            cards_c=s.get("yellows_c"), cards_f=s.get("yellows_f"),
            ficha_casa=res.ficha_casa, ficha_fora=res.ficha_fora,
        )
    except Exception:  # noqa: BLE001
        return False
    if sint is None or sint.confianca < _CONFIANCA_MEGA_MIN:
        return False

    return True

# Mercados cujo sinal pontual nao vai a Telegram por *default*. O filtro
# de desempenho (M23) pode manter aqui so o que nao bateu edge consistente.
# Dados reais (2026-10): remates e remates_a_baliza mostram ROI +25% a
# +48% em multiplas ligas, portanto saem desta lista. Cantos continuam
# aqui porque a maior parte dos L sao cantos (ver relatorio semanal).
_MERCADOS_SO_PROJECAO = {"cantos", "sot"}


def _purgar_cooldown() -> None:
    """Remove entradas antigas (>COOLDOWN_MIN) do dict de dedup."""
    agora = agora_utc()
    limite = COOLDOWN_MIN * 60
    expiradas = [k for k, ts in _cooldown.items()
                 if (agora - ts).total_seconds() > limite]
    for k in expiradas:
        _cooldown.pop(k, None)


def _em_cooldown(event_id, mercado, lado, linha=None) -> bool:
    """True se ja mandamos sinal do mesmo (jogo, mercado, lado) recente.

    A linha e ignorada: OVER 6.5 e OVER 8.5 do mesmo jogo+mercado+lado
    sao o mesmo "sinal" para o utilizador — nao enviar outra vez por 30 min.
    """
    key = (str(event_id), mercado, lado)
    ts = _cooldown.get(key)
    if ts is None:
        return False
    return (agora_utc() - ts).total_seconds() < COOLDOWN_MIN * 60


def _marcar_enviado(event_id, mercado, lado, linha=None) -> None:
    """Guarda o envio por (event_id, mercado, lado) — linha ignorada."""
    key = (str(event_id), mercado, lado)
    _cooldown[key] = agora_utc()

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Ligas: usamos IDs internos Sofascore para precisao (ha "Premier League"
# em Uganda, Russia, Burundi... o filtro por nome apanhava-as).
# Para ver os IDs ou adicionar mais ligas, le o LIGAS_SOFASCORE_IDS.
IDS_LIGAS_DEFAULT = tuple(sofascore_live.LIGAS_SOFASCORE_IDS.values()) if hasattr(sofascore_live, "LIGAS_SOFASCORE_IDS") else ()

CSV_COLUNAS = [
    "alert_id",
    "ts_scan", "event_id", "liga", "home", "away", "minuto", "marcador",
    "mercado", "lado", "linha", "casa", "odd",
    "prob_modelo", "p_mercado", "p_final", "verificar",
    "ev", "estrelas", "xg_casa", "xg_fora",
    "shots_on_target_c", "shots_on_target_f",
    "corners_c", "corners_f", "yellows_c", "yellows_f",
    # Preenchidos por resolver_sugestoes_live.py:
    "resultado", "lucro", "resolvido_em",
]


def _ficheiro_csv() -> Path:
    return project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _carregar_vistas(f: Path) -> dict[tuple, str]:
    """Idempotencia: 1 sugestao por (event_id, mercado, lado, linha, casa, minuto).

    Devolve dict {chave: alert_id} para permitir re-anexar alert_id aos
    dicts em memoria quando o sinal ja foi gravado em ciclos anteriores.
    """
    if not f.exists():
        return {}
    vistas: dict[tuple, str] = {}
    with f.open(encoding="utf-8") as fp:
        for linha in csv.DictReader(fp):
            vistas[(
                linha["home"] + "|" + linha["away"],
                linha["mercado"], linha["lado"], linha["linha"],
                linha["casa"], linha["minuto"],
            )] = linha.get("alert_id", "")
    return vistas


def _proximo_alert_id(f: Path) -> int:
    """Varre o CSV uma vez e devolve max(alert_id)+1 (ou 1 em CSV vazio)."""
    if not f.exists():
        return 1
    maior = 0
    with f.open(encoding="utf-8") as fp:
        for linha in csv.DictReader(fp):
            try:
                v = int(linha.get("alert_id") or 0)
                if v > maior:
                    maior = v
            except (TypeError, ValueError):
                continue
    return maior + 1


def _gravar_novas(sugestoes_dicts, f: Path) -> int:
    """Grava sugestoes novas no CSV e carimba alert_id em memoria.

    Novo sinal: alert_id sequencial atribuido em memoria + gravado em CSV.
    Sinal ja visto: alert_id vem do CSV, anexado ao dict em memoria para
    o Telegram incluir no texto (`/apostei`) mesmo em re-notificacoes.
    """
    f.parent.mkdir(parents=True, exist_ok=True)
    vistas = _carregar_vistas(f)
    proximo_id = _proximo_alert_id(f)
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
                # Ja gravado — mas enriquece o dict em memoria para o Telegram
                aid_existente = vistas[key]
                if aid_existente and not s.get("alert_id"):
                    s["alert_id"] = aid_existente
                continue
            s["alert_id"] = proximo_id
            proximo_id += 1
            # No CSV vai a prob bruta: e dela que o calibrador aprende. Em
            # memoria (Telegram, filtros) fica a calibrada.
            writer.writerow({**s, "prob_modelo": s["prob_bruta"]} if "prob_bruta" in s else s)
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
        # Big-5 principais
        ("premier league", "soccer_epl"),
        ("laliga", "soccer_spain_la_liga"),      # cobre "LaLiga EA Sports"
        ("la liga", "soccer_spain_la_liga"),
        ("serie a", "soccer_italy_serie_a"),
        ("bundesliga", "soccer_germany_bundesliga"),
        ("ligue 1", "soccer_france_ligue_one"),
        ("liga portugal", "soccer_portugal_primeira_liga"),  # cobre "Liga Portugal Betclic"
        ("primeira liga", "soccer_portugal_primeira_liga"),
        # Competicoes europeias (UCL/UEL/UECL)
        ("champions league", "soccer_uefa_champs_league"),
        ("europa league", "soccer_uefa_europa_league"),
        ("conference league", "soccer_uefa_europa_conference_league"),
        # Segundas divisoes e outras
        ("championship", "soccer_efl_champ"),
        ("hypermotion", "soccer_spain_segunda_division"),
        ("serie b", "soccer_italy_serie_b"),
        ("ligue 2", "soccer_france_ligue_two"),
        ("bundesliga 2", "soccer_germany_bundesliga2"),
        ("liga portugal 2", "soccer_portugal_segunda_liga"),
        ("mls", "soccer_usa_mls"),
        ("brasileirão", "soccer_brazil_campeonato"),
        ("brasileirao", "soccer_brazil_campeonato"),
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
    agora = agora_utc()
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


def _gravar_projecao_db(
    event_id: int,
    minuto: int,
    estado,
    proj: "projecao_live.ProjecaoResumo",
) -> None:
    """Grava uma projeccao enviada em projecao_live para medir calibracao."""
    import json as _json
    odds_json = _json.dumps([
        {"linha": lo.linha, "over": lo.over_justa, "under": lo.under_justa,
         "p_over": lo.prob_over}
        for lo in proj.linhas_total
    ])
    with abrir_sessao() as s:
        existe = s.scalar(select(ProjecaoLive).where(
            ProjecaoLive.sofascore_event_id == event_id,
            ProjecaoLive.minuto == minuto,
            ProjecaoLive.mercado == proj.mercado,
        ))
        if existe:
            return
        s.add(ProjecaoLive(
            sofascore_event_id=event_id,
            minuto=minuto,
            score_casa=estado.marcador_casa,
            score_fora=estado.marcador_fora,
            nome_casa=estado.home,
            nome_fora=estado.away,
            liga=estado.liga,
            mercado=proj.mercado,
            valor_atual_total=proj.valor_atual_total,
            projecao_final_total=proj.projecao_final_total,
            valor_atual_casa=proj.valor_atual_casa,
            valor_atual_fora=proj.valor_atual_fora,
            projecao_final_casa=proj.projecao_final_casa,
            projecao_final_fora=proj.projecao_final_fora,
            odds_justas_json=odds_json,
        ))


def _enviar_projecoes_jogo(estado, prev, enviar_telegram: bool,
                           min_estrelas: int = 4) -> int:
    """Se minuto >= target (40/55/70) e ainda nao enviado para este jogo,
    envia projeccao de SOT + cantos ao Telegram e grava na DB.

    Filtros aplicados antes de enviar:
      - minuto >= 85 → skip (jogo quase a acabar, projeccao irrelevante)
      - liga em blacklist (regionais alemas, Oberliga, Regionalliga) → skip
      - valor_actual == 0 E projeccao < 3.0 → skip (jogo "morto")
      - projeccao < 1.0 → skip (odds degenerativas 100.00/1.01)
    """
    event_id = estado.event_id
    minuto = int(estado.minuto or 0)
    # Guarda o primeiro minuto em que vimos este jogo
    if event_id not in _primeiro_minuto_visto:
        _primeiro_minuto_visto[event_id] = minuto
    if minuto < projecao_live.MINUTOS_PROJECAO[0]:
        return 0
    # Filtro 1: muito perto do fim — projeccao perde sentido
    # Alem dos 75' faltam <15 min e a variacao do mercado e minima.
    if minuto >= 75:
        return 0
    # Filtro 1b: jogo apanhado tarde demais (restart do scanner / Cloudflare).
    # Se so comecamos a ver o jogo apos os 60', faltam dados dos primeiros
    # 60 min e as projecoes nao sao credíveis.
    primeiro_min_visto = _primeiro_minuto_visto.get(event_id, minuto)
    if primeiro_min_visto > 60:
        log.debug(
            "Projecao %s: skip — jogo apanhado tarde (ao %d')",
            event_id, primeiro_min_visto,
        )
        return 0

    # Filtro 2: blacklist de ligas regionais / sem interesse
    liga_lower = (estado.liga or "").lower()
    blacklist_projecao = (
        "regionalliga", "oberliga", "landesliga", "verbandsliga",
        "reserve league", " u23", " u19", " u17",
    )
    if any(b in liga_lower for b in blacklist_projecao):
        return 0

    # Filtro 2b: equipas B / reservas (ex: "Real Sociedad B", "Barcelona B").
    # Essas equipas rodam totalmente e o perfil habitual nao se aplica.
    def _e_equipa_b(nome: str) -> bool:
        n = (nome or "").strip().lower()
        if not n:
            return False
        # Ends with " b" or " ii" (equipa reserva)
        return n.endswith(" b") or n.endswith(" ii") or " b " in n or " ii " in n
    if _e_equipa_b(estado.home) or _e_equipa_b(estado.away):
        log.debug(
            "Projecao %s: skip — equipa B/reservas (%s vs %s)",
            event_id, estado.home, estado.away,
        )
        return 0

    ja = _projecoes_enviadas.setdefault(event_id, set())
    target = projecao_live.deve_projetar(minuto, ja)
    if target is None:
        return 0

    # Fichas fracas (<5 jogos): nao projetamos. O modelo nao tem base
    # suficiente — projecao e lotaria. Caso real: 61% das equipas na BD
    # tem <5 jogos. Isto apanha-as antes de usar atencao do utilizador.
    if not _ambas_fichas_fortes(estado.home_id, estado.away_id):
        log.info(
            "Projecao %s %s vs %s: skip — fichas fracas "
            "(home=%d jogos, away=%d jogos, min %d)",
            event_id, estado.home, estado.away,
            _n_jogos_ficha(estado.home_id or 0),
            _n_jogos_ficha(estado.away_id or 0),
            _MIN_JOGOS_FICHA,
        )
        return 0

    # Atributos correctos do EstadoLive: shots_on_target_casa, corners_casa
    sot_c = int(getattr(estado, "shots_on_target_casa", 0) or 0)
    sot_f = int(getattr(estado, "shots_on_target_fora", 0) or 0)
    can_c = int(getattr(estado, "corners_casa", 0) or 0)
    can_f = int(getattr(estado, "corners_fora", 0) or 0)

    mercados_para_projetar: list[tuple[str, float, float, int, int]] = []
    if hasattr(prev, "projeccao_sot_casa"):
        # Reconstroi lam por equipa a partir da projeccao final - valor actual
        mercados_para_projetar.append((
            "sot",
            max(0.0, prev.projeccao_sot_casa - sot_c),
            max(0.0, prev.projeccao_sot_fora - sot_f),
            sot_c, sot_f,
        ))
        mercados_para_projetar.append((
            "cantos",
            max(0.0, prev.projeccao_cantos_casa - can_c),
            max(0.0, prev.projeccao_cantos_fora - can_f),
            can_c, can_f,
        ))
    else:
        # Fallback aproximado se o predictor nao tem projeccao por equipa
        mercados_para_projetar.append((
            "sot",
            prev.lam_shots_on_target_restantes / 2.0,
            prev.lam_shots_on_target_restantes / 2.0,
            sot_c, sot_f,
        ))
        mercados_para_projetar.append((
            "cantos",
            prev.lam_cantos_restantes / 2.0,
            prev.lam_cantos_restantes / 2.0,
            can_c, can_f,
        ))

    enviadas = 0
    for mercado, lam_c, lam_f, val_c, val_f in mercados_para_projetar:
        def _proj(mult: float, mercado=mercado, lam_c=lam_c, lam_f=lam_f,
                  val_c=val_c, val_f=val_f):
            # aplicar_contexto=False: o live_predictor ja aplica fator_m +
            # fator_chasing + +15% losing. NAO duplicar os ajustes aqui.
            # Fadiga NAO depende do predictor, por isso continua a ser
            # aplicada (quando houver dias_descanso).
            return projecao_live.projetar(
                mercado=mercado,
                minuto=minuto,
                score_casa=estado.marcador_casa,
                score_fora=estado.marcador_fora,
                valor_atual_casa=float(val_c),
                valor_atual_fora=float(val_f),
                lam_rest_casa=float(lam_c) * mult,
                lam_rest_fora=float(lam_f) * mult,
                nome_casa=estado.home,
                nome_fora=estado.away,
                aplicar_contexto=False,
            )
        try:
            # Bruta vai para a BD (e dela que a correccao aprende); a
            # corrigida pelo historico projectado-vs-real e a que se usa.
            proj_bruta = _proj(1.0)
            mult = _mult_projecao(mercado, target)
            proj = _proj(mult) if mult != 1.0 else proj_bruta
        except Exception as exc:  # noqa: BLE001
            log.warning("Projecao %s falhou %s: %s", mercado, event_id, exc)
            continue

        # Filtro 3+4: skip projeccoes degeneradas
        if proj.projecao_final_total < 1.0:
            continue
        if proj.valor_atual_total == 0 and proj.projecao_final_total < 3.0:
            continue
        # Filtro 5: skip projeccoes "sem valor" (todas as linhas em quase-certo)
        if not projecao_live.tem_valor_actionavel(proj):
            continue
        # Filtro 6: projeccao total irrealistica para o mercado — ficha das
        # equipas vazia produz projecoes absurdas (ex: cantos 1.5 total).
        # Mesmos limites do _escolher_aposta do preview.
        _MIN_PROJ_POR_MERCADO = {
            "cantos": 6.0,   # <6 cantos no jogo todo = ficha vazia
            "sot": 5.0,      # <5 SoT total no jogo todo = ficha vazia
            "remates": 8.0,  # <8 remates total = ficha vazia
        }
        min_proj = _MIN_PROJ_POR_MERCADO.get(mercado)
        if min_proj is not None and proj.projecao_final_total < min_proj:
            log.debug(
                "Projecao %s %s: skip — projecao total %.1f < min %.1f "
                "(ficha vazia)",
                event_id, mercado, proj.projecao_final_total, min_proj,
            )
            continue

        try:
            _gravar_projecao_db(event_id, target, estado, proj_bruta)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gravar projecao %s falhou: %s", mercado, exc)

        if enviar_telegram:
            # Voz dos treinadores + sintese do mega-cerebro.
            extras = ""
            yel_c_live = int(getattr(estado, "yellow_cards_casa", 0) or 0)
            yel_f_live = int(getattr(estado, "yellow_cards_fora", 0) or 0)
            try:
                from apostas.alertas.analise_treinador import (
                    linhas_formatadas_para_telegram,
                )
                if estado.home_id and estado.away_id:
                    extras = linhas_formatadas_para_telegram(
                        int(estado.home_id), int(estado.away_id),
                        minuto=minuto,
                        score_casa=int(estado.marcador_casa or 0),
                        score_fora=int(estado.marcador_fora or 0),
                        sot_c=sot_c, sot_f=sot_f,
                        cantos_c=can_c, cantos_f=can_f,
                        cards_c=yel_c_live, cards_f=yel_f_live,
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("Treinador na projecao falhou: %s", exc)
            # Aviso se apanhamos o jogo tarde — projecao pode ser menos
            # fiavel se perdemos os primeiros N minutos
            primeiro_min = _primeiro_minuto_visto.get(event_id, minuto)
            if primeiro_min > 15:
                extras += f"\n⚠ Jogo apanhado ao {primeiro_min}' — projecao pode subestimar ritmo inicial"

            # Deteccao de rotacao (Camada 1 + Camada 2). Flag _rotacao_detectada
            # usada depois para silenciar o mega-cerebro (perfis nao se aplicam).
            _rotacao_detectada = False
            try:
                from apostas.alertas import rotacao as _rotacao
                # Camada 1 — competicao e taca?
                if _rotacao.is_competicao_rotacao_alta(estado.liga):
                    extras += "\n⚠ Taca — rotacao provavel: projecoes reduzidas 30%"
                    _rotacao_detectada = True
                # Camada 2 — comparar lineup actual
                for lineup, team_id, nome_eq in (
                    (estado.lineup_titulares_casa, estado.home_id, estado.home),
                    (estado.lineup_titulares_fora, estado.away_id, estado.away),
                ):
                    if not lineup or not team_id:
                        continue
                    ids_actuais = [pid for pid, _ in lineup]
                    nomes_actuais = {pid: nome for pid, nome in lineup}
                    rel = _rotacao.analisar_lineup_actual(
                        int(team_id), nome_eq,
                        ids_actuais, nomes_actuais,
                    )
                    if rel and rel.tem_rotacao(limiar=3):
                        extras += "\n" + rel.mensagem_curta()
                        _rotacao_detectada = True
            except Exception as exc:  # noqa: BLE001
                log.debug("Rotacao check falhou: %s", exc)
            # Contexto de competicao (Fase A): peso do jogo + standings
            contexto = None
            try:
                from apostas.segundo_cerebro.contexto_competicao import (
                    obter_contexto, resumo_telegram as resumo_ctx,
                )
                contexto = obter_contexto(
                    liga_nome=estado.liga,
                    tournament_id=estado.tournament_id,
                    season_id=estado.season_id,
                    home_id=estado.home_id,
                    away_id=estado.away_id,
                )
                linha_ctx = resumo_ctx(contexto, estado.home, estado.away)
                if linha_ctx:
                    extras += "\n" + linha_ctx
            except Exception as exc:  # noqa: BLE001
                log.debug("Contexto competicao falhou: %s", exc)
            # Mega-cerebro: 1 linha de sintese (silencio se sinal fraco)
            # Alem da linha, grava o sinal no ledger (Fase D) para o resolver
            # poder avaliar depois.
            _sint = None
            try:
                from apostas.alertas import mega_cerebro
                if estado.home_id and estado.away_id:
                    # Primeiro produz a sintese completa (sem passar logo pelo
                    # wrapper string-only) para podermos tambem gravar o ledger.
                    try:
                        from apostas.alertas.confronto import analisar_confronto
                        _res_conf = analisar_confronto(
                            int(estado.home_id), int(estado.away_id),
                            minuto=minuto,
                            score_casa=int(estado.marcador_casa or 0),
                            score_fora=int(estado.marcador_fora or 0),
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Confronto no mega falhou: %s", exc)
                        _res_conf = None
                    _sint = None
                    if _res_conf is not None:
                        _sint = mega_cerebro.sintetizar_a_partir_confronto(
                            minuto=minuto,
                            score_casa=int(estado.marcador_casa or 0),
                            score_fora=int(estado.marcador_fora or 0),
                            sot_c=sot_c, sot_f=sot_f,
                            cantos_c=can_c, cantos_f=can_f,
                            cards_c=yel_c_live, cards_f=yel_f_live,
                            ficha_casa=_res_conf.ficha_casa,
                            ficha_fora=_res_conf.ficha_fora,
                            contexto=contexto,
                            rotacao_detectada=_rotacao_detectada,
                        )
                    if _sint is not None:
                        extras += "\n" + _sint.to_telegram()
                        # Fase D: grava no ledger
                        try:
                            from apostas.utils.db import abrir_sessao
                            with abrir_sessao() as sess:
                                mega_cerebro.gravar_sinal(
                                    sess,
                                    event_id=int(estado.event_id),
                                    liga=estado.liga,
                                    home=estado.home, away=estado.away,
                                    minuto=minuto,
                                    score_casa=int(estado.marcador_casa or 0),
                                    score_fora=int(estado.marcador_fora or 0),
                                    sot_c=sot_c, sot_f=sot_f,
                                    cantos_c=can_c, cantos_f=can_f,
                                    cards_c=yel_c_live, cards_f=yel_f_live,
                                    sintese=_sint,
                                )
                        except Exception as exc:  # noqa: BLE001
                            log.debug("Gravar sinal mega falhou: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.debug("Mega-cerebro na projecao falhou: %s", exc)
            # ROI historico do mercado+liga
            # Alem de mostrar, se o ROI historico for muito negativo com
            # volume suficiente (ex: -15% em 50+ alertas), nao envia — este
            # mercado nessa liga esta provado como mau.
            roi_line = ""
            _skip_por_roi = False
            try:
                from apostas.alertas.historico_alertas import (
                    agregar_por_mercado, carregar_perfis,
                )
                from apostas.utils.config import project_root
                csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
                perfis = carregar_perfis(csv_path)
                if perfis:
                    merc_key = "sot" if mercado == "sot" else mercado
                    agg = agregar_por_mercado(perfis).get((estado.liga, merc_key))
                    if agg and agg.n_resolvidos >= 10 and agg.roi is not None:
                        sinal = "+" if agg.roi >= 0 else ""
                        roi_line = f"📊 {agg.n_resolvidos} alertas · ROI {sinal}{agg.roi:.1f}%"
                        if agg.clv_medio is not None:
                            s2 = "+" if agg.clv_medio >= 0 else ""
                            roi_line += f" · CLV {s2}{agg.clv_medio:.1f}%"
                        # ROI muito negativo com volume → blacklist dinamica
                        # LIMIARES APERTADOS: 10 alertas + ROI <= -5% chega
                        # para bloquear. Antes era 50+ e -15% — demasiado
                        # permissivo. Ricardo recebeu alertas de 2a Bundesliga
                        # com ROI -30% e -61% em 11-16 alertas que foram para
                        # Telegram apesar do historico ser mau.
                        if agg.n_resolvidos >= 10 and agg.roi <= -5.0:
                            log.info(
                                "Projecao %s %s/%s: skip — ROI historico %.1f%% "
                                "em %d alertas (categoria perdedora)",
                                event_id, estado.liga, mercado,
                                agg.roi, agg.n_resolvidos,
                            )
                            _skip_por_roi = True
            except Exception as exc:  # noqa: BLE001
                log.debug("ROI historico na projecao falhou: %s", exc)
            if _skip_por_roi:
                continue
            # Se rotacao detectada numa das equipas (taca ou lineup com
            # >=3 habituais fora), nao enviar a projecao de todo — as
            # projeccoes derivam das fichas que agora nao se aplicam.
            if _rotacao_detectada:
                log.debug(
                    "Projecao %s %s: skip — rotacao detectada",
                    event_id, mercado,
                )
                continue
            # HARD FLOOR: so projeccoes com >= min_estrelas vao para Telegram.
            # Projeccoes de 1-3 estrelas sao palpites fracos — nao enviar.
            n_estrelas = projecao_live._estrelas_projecao(proj)
            if n_estrelas < min_estrelas:
                log.debug(
                    "Projecao %s %s: skip — %d estrelas (min %d)",
                    event_id, mercado, n_estrelas, min_estrelas,
                )
                continue
            msg = projecao_live.formatar_telegram(
                proj, estado.home, estado.away,
                estado.marcador_casa, estado.marcador_fora,
                extras=extras, roi_line=roi_line,
            )
            # Narrativa final mega-cerebro (previsao + certezas) tambem
            # nas projeccoes, igual aos sinais do analista.
            try:
                from apostas.alertas.mega_narrativa import narrativa_final
                # Escolhe a linha central da projeccao como "linha de referencia"
                linha_ref = (proj.linhas_total[len(proj.linhas_total) // 2].linha
                             if proj.linhas_total else proj.projecao_final_total)
                # Para projecoes, o "lado" default e over (projeccao > valor atual)
                lado_ref = "over" if proj.projecao_final_total > proj.valor_atual_total else "under"
                sinal_dict = {
                    "mercado": mercado,
                    "lado": lado_ref,
                    "linha": linha_ref,
                    "minuto": minuto,
                    "marcador": f"{estado.marcador_casa}-{estado.marcador_fora}",
                    "prob_modelo": max(lo.prob_over for lo in proj.linhas_total)
                        if proj.linhas_total else None,
                    "shots_on_target_c": sot_c,
                    "shots_on_target_f": sot_f,
                    "corners_c": can_c,
                    "corners_f": can_f,
                    "yellows_c": yel_c_live,
                    "yellows_f": yel_f_live,
                    "xg_casa": getattr(estado, "xg_casa", None),
                    "xg_fora": getattr(estado, "xg_fora", None),
                }
                # Razao: usa a sintese do mega se existir
                razao_mega = ""
                if _sint is not None:
                    razao_mega = getattr(_sint, "razao", "") or ""
                narr = narrativa_final(sinal_dict, sintese_razao=razao_mega)
                if narr:
                    msg = msg + "\n" + narr
            except Exception as exc:  # noqa: BLE001
                log.debug("Narrativa final na projecao falhou: %s", exc)
            try:
                telegram_cliente().enviar(msg)
                enviadas += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram projecao erro: %s", exc)

    ja.add(target)
    return enviadas


# Mercados dinamicos (SoT/remates/cantos/cards) movem-se muito ao HT:
# bookies reajustam linhas, Odds API feed demora a actualizar. Snapshots
# com linha pre-match + marcador live geram sinais falsos (ex: OVER 5.5
# SoT quando ja temos 4 SoT ao HT — fair odd ~1.05, bookie real ja fechou
# essa linha). Rejeitamos sinais na janela 40'-50' nestes mercados; golos
# e BTTS continuam a passar (movem-se mais devagar).
_INTERVALO_INI = 40
_INTERVALO_FIM = 50
_MERCADOS_SEM_INTERVALO = {"sot", "remates", "remates_a_baliza", "cantos", "cartoes"}

# Margem minima entre linha e marcador actual (over). Se linha - atuais
# e abaixo deste limiar, o mercado real ja ajustou a linha para cima e
# aquela odd nao existe mais no bookie. Evita falsos positivos como
# "OVER 5.5 remates a baliza com 4 SoT ao 45'" (bookie real tem 9.5).
_MARGEM_MIN_LINHA = {
    "golos":            0.5,
    "cantos":           3.0,
    "cartoes":          1.5,
    "remates":          5.0,
    "remates_a_baliza": 3.0,
}


def _linha_stale_vs_marcador(mercado: str, lado: str, linha: float | None, estado) -> bool:
    """True se a linha OVER esta demasiado perto do marcador actual.

    Significa que o mercado real ja ajustou e a odd apanhada no snapshot
    nao existe mais. UNDER nao e afectado (mais atuais = mais perto de UNDER
    perder, mas pode continuar valido se faltam poucos minutos).
    """
    if linha is None or lado != "over":
        return False
    margem = _MARGEM_MIN_LINHA.get(mercado)
    if margem is None:
        return False
    try:
        linha_f = float(linha)
    except (ValueError, TypeError):
        return False
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
    if atuais is None:
        return False
    return (linha_f - atuais) < margem


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


def _ajustes_cerebro_para(estado):
    """Fase 5+10: ajustes do cerebro a aplicar no predictor. None se nao
    ha historico suficiente ou IDs em falta."""
    if estado.home_id is None or estado.away_id is None:
        return None
    try:
        return cerebro_live.calcular_ajustes(
            estado.home_id, estado.away_id,
            minuto=estado.minuto,
            score_casa=estado.marcador_casa, score_fora=estado.marcador_fora,
            missing_casa=getattr(estado, "missing_casa", None),
            missing_fora=getattr(estado, "missing_fora", None),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("cerebro falhou %s-%s: %s", estado.home_id, estado.away_id, exc)
        return None


def _analisar_jogo(estado, odds_json: dict, ev_min: float, filtro_casas: tuple[str, ...]) -> list[dict]:
    """Devolve lista de sugestoes (dicts) com EV >= ev_min."""
    sugestoes = []
    exp_pre = _puxar_expectativa(estado)
    ajustes = _ajustes_cerebro_para(estado)
    prev = live_predictor.prever(
        estado, expectativa_pre=exp_pre, ajustes_cerebro=ajustes,
    )

    def _emit(mercado, lado, linha, casa, odd, prob):
        # NOVO: skip linhas ja decididas (Over 2.5 num 4-2 = inutil)
        if not _linha_live(mercado, lado, linha, estado):
            return
        # Snapshot stale: linha do feed demasiado perto do marcador actual.
        # Mercado real ja ajustou para linha mais alta; a odd nao existe mais.
        if _linha_stale_vs_marcador(mercado, lado, linha, estado):
            log.debug(
                "Skip %s %s %s %s@%s: linha stale vs marcador live "
                "(mercado real ja ajustou)",
                estado.event_id, mercado, lado, linha, casa,
            )
            return
        # Intervalo (40-50'): mercados dinamicos movem-se muito, feed demora
        # a actualizar. Nao enviar sinais de SoT/remates/cantos/cards ao HT.
        if (mercado in _MERCADOS_SEM_INTERVALO
                and _INTERVALO_INI <= (estado.minuto or 0) <= _INTERVALO_FIM):
            log.debug(
                "Skip %s %s %s: janela intervalo (%s') — mercado dinamico",
                estado.event_id, mercado, lado, estado.minuto,
            )
            return
        # §19 blend p_modelo × p_mercado (w=0.3). O EV e calculado em
        # cima da p_final, nao da p_modelo pura. Flag "verificar" quando
        # discrepancia >10pp OU EV_puro >15% (sinal de problema de dados).
        from apostas.modelos.blend_mercado import aplicar_blend
        prob_bruta = prob
        prob = _calibrador_live().calibrar(prob, mercado, lado, "odds")
        blend_data = aplicar_blend(prob, odd)
        ev = blend_data["ev"]
        if ev < ev_min:
            return
        sugestoes.append({
            "ts_scan": agora_utc().isoformat(timespec="seconds"),
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
            "prob_bruta": round(prob_bruta, 4),
            "p_mercado": blend_data["p_mercado"],
            "p_final": blend_data["p_final"],
            "verificar": blend_data["verificar"],
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
            # Nao vao para CSV, so para a mensagem Telegram
            "home_id": estado.home_id,
            "away_id": estado.away_id,
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
    # ─── Pos-processamento M18+M19: consolidar + filtrar + melhor casa ───
    sugestoes = _consolidar_e_filtrar(sugestoes, odds_json)
    return sugestoes


# Mapa (mercado → (market_key_odds_api, outcome_opposto))
# Serve para o filtros_mercado procurar os bookmakers correctos.
_MAPA_MERCADO_FILTROS = {
    "golos":            ("totals",         {"over": "under", "under": "over"}),
    "btts":             ("btts",           {"sim": "no", "nao": "yes"}),
    "cantos":           ("totals_corners", {"over": "under", "under": "over"}),
    "cartoes":          ("totals_cards",   {"over": "under", "under": "over"}),
    "remates":          ("totals_shots",   {"over": "under", "under": "over"}),
    "remates_a_baliza": ("totals_shots_on_target", {"over": "under", "under": "over"}),
}

# Para outcome names, scanner usa "sim/nao" para BTTS mas Odds API usa "yes/no"
_OUTCOME_SCANNER_TO_API = {"sim": "yes", "nao": "no"}


def _consolidar_e_filtrar(sugestoes: list[dict], odds_json: dict) -> list[dict]:
    """Pos-processa sugestoes (M18+M19).

    Agrupa por (mercado, lado, linha). Para cada grupo:
      1. Chama filtros_mercado.avaliar() → bloqueia se margem/stale/<2 casas
      2. Mantem a sugestao com a MELHOR odd (M19)
      3. Adiciona campos melhor_casa, melhor_odd, outras_casas, flag_verificar
    """
    if not sugestoes:
        return []
    bms = odds_json.get("bookmakers", []) if odds_json else []
    if not bms:
        return sugestoes  # sem bookmakers → nada a filtrar

    agrupadas: dict[tuple, list[dict]] = {}
    for s in sugestoes:
        chave = (s.get("mercado"), s.get("lado"), s.get("linha") or "")
        agrupadas.setdefault(chave, []).append(s)

    out: list[dict] = []
    for (mercado, lado, linha), grupo in agrupadas.items():
        mapa = _MAPA_MERCADO_FILTROS.get(mercado)
        if mapa is None:
            # Mercado nao filtrado (ex: 1x2) — passa como esta
            out.extend(grupo)
            continue
        market_key_api, mapa_lados = mapa
        lado_oposto = mapa_lados.get(lado, "")
        if not lado_oposto:
            out.extend(grupo)
            continue

        # Converte linha para float se possivel
        point = None
        if linha:
            try:
                point = float(linha)
            except (ValueError, TypeError):
                point = None

        # Para BTTS, scanner usa "sim/nao"; API usa "yes/no"
        outcome_api = _OUTCOME_SCANNER_TO_API.get(lado, lado)
        oposto_api = _OUTCOME_SCANNER_TO_API.get(lado_oposto, lado_oposto)

        ev_grupo = max((s.get("ev", 0) for s in grupo), default=0)
        try:
            res = filtros_mercado.avaliar(
                bms, market_key_api, outcome_api, oposto_api, point,
                ev_bruto=float(ev_grupo),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Filtros mercado (%s, %s, %s) falhou: %s",
                        mercado, lado, linha, exc)
            out.extend(grupo)
            continue

        if not res.passa:
            log.debug("Sugestao bloqueada (%s %s %s): %s",
                      mercado, lado, linha, res.razao_bloqueio)
            continue

        # Escolhe a sugestao com a melhor odd (M19)
        melhor = max(grupo, key=lambda s: s.get("odd", 0))
        melhor_copy = dict(melhor)
        melhor_copy["melhor_casa"] = res.melhor_casa
        melhor_copy["melhor_odd"] = res.melhor_odd
        melhor_copy["outras_casas"] = filtros_mercado.formatar_melhor_casa(res)
        melhor_copy["margem_mercado"] = (
            round(res.margem_mercado * 100, 1) if res.margem_mercado else None
        )
        if res.flag_verificar:
            melhor_copy["verificar"] = True
        out.append(melhor_copy)

    return out


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


# Calibrador por (mercado, lado) treinado nos alertas resolvidos do CSV.
# Retreina a cada 6h para a correccao acompanhar os resultados novos.
_CALIBRADOR_TTL_S = 6 * 3600
_calibrador_cache: tuple[float, Calibrador] | None = None


def _calibrador_live() -> Calibrador:
    global _calibrador_cache
    agora = time.time()
    if _calibrador_cache is not None and agora - _calibrador_cache[0] < _CALIBRADOR_TTL_S:
        return _calibrador_cache[1]
    try:
        from apostas.relatorios.calibracao_alertas import carregar_resolvidos
        rows = carregar_resolvidos(_ficheiro_csv())
        cal = treinar(rows)
        log.info("Calibrador por mercado: %d alertas, %d curvas", len(rows), len(cal.curvas))
    except Exception as exc:  # noqa: BLE001
        log.warning("Calibrador por mercado falhou (%s), sem correccao", exc)
        cal = Calibrador()
    _calibrador_cache = (agora, cal)
    return cal


_correcoes_proj_cache: tuple[float, dict] | None = None


def _mult_projecao(mercado: str, janela: int) -> float:
    """Multiplicador do ritmo restante aprendido com projeccoes vs final real."""
    global _correcoes_proj_cache
    agora = time.time()
    if _correcoes_proj_cache is None or agora - _correcoes_proj_cache[0] >= _CALIBRADOR_TTL_S:
        from apostas.modelos.correcao_projecoes import pares_projecao_real, treinar_correcoes
        try:
            with abrir_sessao() as sess:
                correcoes = treinar_correcoes(pares_projecao_real(sess))
            log.info("Correccao projeccoes: %s", {
                f"{m}/{j}": c.mult for (m, j), c in correcoes.items() if j is not None})
        except Exception as exc:  # noqa: BLE001
            log.warning("Correccao projeccoes falhou (%s), sem correccao", exc)
            correcoes = {}
        _correcoes_proj_cache = (agora, correcoes)
    from apostas.modelos.correcao_projecoes import multiplicador
    return multiplicador(_correcoes_proj_cache[1], mercado, janela)


def _calibrar_sinal(estado, sinal):
    """Devolve (sinal com prob/odds/estrelas calibradas, prob bruta).

    Sinais que a calibracao poe abaixo de 55% ou com odd alvo < 1.15 ficam
    com 1 estrela: continuam a ir para o CSV (dados para recalibrar) mas
    nunca chegam ao Telegram.
    """
    import dataclasses
    bruta = sinal.prob_modelo
    cal = _calibrador_live().calibrar(bruta, sinal.mercado, sinal.lado, "analista")
    odd_be, odd_val = analista._odds_minimas(cal)
    estrelas = analista.calcular_estrelas(sinal.mercado, estado.liga, cal)
    if cal < 0.55 or odd_val < 1.15:
        estrelas = 1
    return dataclasses.replace(
        sinal, prob_modelo=cal, odd_minima=odd_be, odd_valor=odd_val, estrelas=estrelas,
    ), bruta


def _sinal_para_sugestao(estado, sinal) -> dict:
    """Converte Sinal numa linha de sugestao para o CSV."""
    racional_str = "\n".join(f"· {r}" for r in sinal.racional) if sinal.racional else ""
    return {
        "ts_scan": agora_utc().isoformat(timespec="seconds"),
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
        "estrelas": sinal.estrelas,
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
        "home_id": estado.home_id,
        "away_id": estado.away_id,
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


# Health do Sofascore live: contagem de ciclos consecutivos em que o pedido
# FALHOU (erro, bloqueio Cloudflare, circuit aberto) dentro da janela de jogos
# (16h-02h PT). "0 jogos" com resposta valida NAO conta: depois das 23h nao ha
# jogos nas ligas que seguimos e isso e normal. Alerta UMA vez por dia.
_SOFA_LIVE_HEALTH_LIMIAR_CICLOS = 4   # ~12 min a 3 min por ciclo
_SOFA_LIVE_HEALTH_HORA_INI = 16       # Europa/Lisboa (jogos finais da tarde)
_SOFA_LIVE_HEALTH_HORA_FIM = 2        # pode varar meia-noite
_sofa_live_ciclos_falha = 0
_sofa_live_alerta_enviado_dia: str | None = None


def _em_janela_jogos_pt() -> bool:
    """True se hora Europa/Lisboa esta entre 16h-02h (janela de jogos)."""
    try:
        from zoneinfo import ZoneInfo
        h = datetime.now(ZoneInfo("Europe/Lisbon")).hour
    except Exception:  # noqa: BLE001
        h = agora_utc().hour  # fallback UTC
    if _SOFA_LIVE_HEALTH_HORA_INI <= _SOFA_LIVE_HEALTH_HORA_FIM:
        return _SOFA_LIVE_HEALTH_HORA_INI <= h <= _SOFA_LIVE_HEALTH_HORA_FIM
    # Janela que vara meia-noite (ex: 16h-02h)
    return h >= _SOFA_LIVE_HEALTH_HORA_INI or h <= _SOFA_LIVE_HEALTH_HORA_FIM


def _actualizar_health_sofa_live(sofa_ok: bool, enviar_telegram: bool) -> None:
    """Rastreia ciclos consecutivos com o Sofascore a falhar; alerta 1x/dia.

    sofa_ok: o pedido de jogos live teve resposta valida (mesmo com 0 jogos).
    - Reset da conta a cada ciclo com resposta valida.
    - Alerta UMA vez por dia (dia local PT), e so se estiver em janela.
    """
    global _sofa_live_ciclos_falha, _sofa_live_alerta_enviado_dia
    if sofa_ok:
        _sofa_live_ciclos_falha = 0
        return
    if not _em_janela_jogos_pt():
        return
    _sofa_live_ciclos_falha += 1
    if _sofa_live_ciclos_falha < _SOFA_LIVE_HEALTH_LIMIAR_CICLOS:
        return
    hoje = agora_utc().strftime("%Y-%m-%d")
    if _sofa_live_alerta_enviado_dia == hoje:
        return
    _sofa_live_alerta_enviado_dia = hoje
    msg = (
        "⚠ *Sofascore live cego*\n"
        f"{_sofa_live_ciclos_falha} ciclos consecutivos com o Sofascore a falhar "
        "(erro ou bloqueio). Scanner live sem dados ao vivo.\n\n"
        "• Preview das 17h continua a funcionar (fallback Odds API)\n"
        "• Sinais pre-match continuam a sair\n"
        "• Scanner volta sozinho quando Cloudflare libertar"
    )
    log.warning("Sofa live health: %s", msg.replace("\n", " | "))
    print("\n" + msg)
    if enviar_telegram:
        try:
            telegram_cliente().enviar(msg)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram health alert falhou: %s", exc)


# Blacklist dinamica por (liga, mercado) OU (liga, mercado, lado) com ROI
# historico <= -5% em >= 10 alertas resolvidos NAO vai para Telegram.
# Granularidade do lado permite bloquear so OVER (que perde) sem matar
# UNDER (que ganha), ou vice-versa.
_BLACKLIST_ROI_LIMIAR_N = 10
_BLACKLIST_ROI_LIMIAR_ROI = -5.0
# cache: chave -> bool. Invalida a cada ciclo.
_blacklist_cache: dict[tuple, bool] = {}
# Cache dos perfis carregados (poupa IO entre chamadas no mesmo ciclo).
_perfis_cache: tuple[dict, dict] | None = None  # (perfis_lado, agg_mercado)


def _resetar_blacklist_cache() -> None:
    """Chamado no inicio de cada ciclo para refrescar com perfis actuais."""
    global _perfis_cache
    _blacklist_cache.clear()
    _perfis_cache = None
    _cache_n_jogos_ficha.clear()


# Quarentena: ligas obscuras (nao-top) precisam de >= N alertas resolvidos
# nessa (liga, mercado) para que confiemos em enviar. Senao, nao sabemos
# se o modelo e bom aqui.
_QUARENTENA_MIN_RESOLVIDOS = 5

# Fichas fracas: equipas com < N jogos no historico nao sao suficientes
# para o modelo projetar. Caso real: 61% das equipas na BD tem <5 jogos
# (equipas de 2as divisoes, taças, amigaveis que o scanner apanha).
_MIN_JOGOS_FICHA = 5
# cache: team_id -> n_jogos. Invalida a cada ciclo.
_cache_n_jogos_ficha: dict[int, int] = {}


def _n_jogos_ficha(team_id: int) -> int:
    """Devolve numero de jogos de ficha. 0 se nao da para calcular."""
    if not team_id:
        return 0
    if team_id in _cache_n_jogos_ficha:
        return _cache_n_jogos_ficha[team_id]
    try:
        from apostas.relatorios.ficha_equipa import gerar_ficha
        f = gerar_ficha(int(team_id), n_jogos=20)
        # FichaEquipa tem atributo `jogos_lidos` (NAO `n_jogos`).
        # Bug critico corrigido: `f.n_jogos` levantava AttributeError
        # silenciosamente, resultado era sempre 0 → todas as equipas
        # marcadas como "fichas fracas" → 99% dos sinais bloqueados.
        n = f.jogos_lidos if f else 0
    except Exception as exc:  # noqa: BLE001
        log.warning("Ficha n_jogos team=%s falhou: %s", team_id, exc)
        n = 0
    _cache_n_jogos_ficha[team_id] = n
    return n


def _ambas_fichas_fortes(home_id, away_id) -> bool:
    """True se ambas as equipas tem >= _MIN_JOGOS_FICHA jogos de ficha."""
    if not home_id or not away_id:
        return False
    return (_n_jogos_ficha(home_id) >= _MIN_JOGOS_FICHA
            and _n_jogos_ficha(away_id) >= _MIN_JOGOS_FICHA)


def _liga_top_ou_com_historico(liga: str, mercado: str) -> bool:
    """True se:
      - Liga esta nas 7 top (prioridade 1) → sempre OK, confiamos em
        mini-cerebros densos.
      - OU liga nao-top mas (liga, mercado) tem >= 5 alertas resolvidos
        → ja temos amostra para julgar.
    False (bloqueia) se nao-top sem historico suficiente.
    """
    try:
        from apostas.dados_referencia.ligas_prioritarias import (
            nome_e_prioridade_1,
        )
    except Exception:  # noqa: BLE001
        return True  # falha segura
    if nome_e_prioridade_1(liga):
        return True
    merc_key = "sot" if mercado in ("sot", "remates_a_baliza") else mercado
    _, agg = _carregar_perfis_cached()
    if not agg:
        return False  # nenhum historico → bloqueia liga obscura
    a = agg.get((liga, merc_key))
    if a is None or a.n_resolvidos < _QUARENTENA_MIN_RESOLVIDOS:
        return False
    return True


def _carregar_perfis_cached():
    """Carrega perfis (liga, mercado, lado) + agregado por (liga, mercado).

    Devolve ({}, {}) se nao ha historico. Chama sempre agregar_por_mercado
    (mesmo com perfis vazio) para que os testes possam monkeypatchar cada
    um independentemente.
    """
    global _perfis_cache
    if _perfis_cache is not None:
        return _perfis_cache
    try:
        from apostas.alertas.historico_alertas import (
            agregar_por_mercado, carregar_perfis,
        )
        from apostas.utils.config import project_root
        csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
        perfis = carregar_perfis(csv_path) or {}
        agg = agregar_por_mercado(perfis) or {}
        _perfis_cache = (perfis, agg)
        return _perfis_cache
    except Exception as exc:  # noqa: BLE001
        log.debug("Carregar perfis falhou: %s", exc)
        _perfis_cache = ({}, {})
        return _perfis_cache


def _blacklist_roi_bloqueia(liga: str, mercado: str,
                            lado: str | None = None) -> bool:
    """True se tem ROI negativo com volume suficiente.

    Prioriza granularidade: se `lado` dado, tenta (liga, mercado, lado) PRIMEIRO.
    Se o perfil especifico ao lado tem volume + ROI mau, bloqueia. Caso contrario
    cai para (liga, mercado) agregado.

    Normaliza 'sot' e 'remates_a_baliza' para a mesma chave.
    """
    merc_key = "sot" if mercado in ("sot", "remates_a_baliza") else mercado
    lado_key = (lado or "").strip().lower() or None
    cache_key = (liga, merc_key, lado_key)
    if cache_key in _blacklist_cache:
        return _blacklist_cache[cache_key]
    perfis, agg = _carregar_perfis_cached()
    if not perfis and not agg:
        _blacklist_cache[cache_key] = False
        return False
    # 1. Checa perfil por lado (granularidade maxima). Se o perfil especifico
    # tem volume suficiente, decide SO por ele — dados granulares sao mais
    # fiaveis que o agregado (ex: UNDER +10% enquanto OVER -30%).
    if lado_key and perfis:
        from apostas.alertas.historico_alertas import perfil_para
        p = perfil_para(perfis, liga, merc_key, lado_key)
        if p and p.n_resolvidos >= _BLACKLIST_ROI_LIMIAR_N:
            bloqueia = (p.roi is not None
                        and p.roi <= _BLACKLIST_ROI_LIMIAR_ROI)
            _blacklist_cache[cache_key] = bloqueia
            return bloqueia
    # 2. Fallback agregado por mercado (sem lado, ou lado sem volume)
    a = agg.get((liga, merc_key))
    if (a and a.n_resolvidos >= _BLACKLIST_ROI_LIMIAR_N
            and a.roi is not None
            and a.roi <= _BLACKLIST_ROI_LIMIAR_ROI):
        _blacklist_cache[cache_key] = True
        return True
    _blacklist_cache[cache_key] = False
    return False


def _correr_ciclo(args) -> None:
    _resetar_blacklist_cache()
    agora = agora_utc().strftime("%H:%M:%S")
    # Heartbeat no LOG (nao so no console) para o watchdog nao achar
    # que o scanner esta encravado quando so esta a esperar jogos.
    log.info("Ciclo scanner: a listar jogos live")
    print(f"\n[{agora}] A listar jogos live...")
    try:
        if args.todas_ligas:
            jogos = sofascore_live.listar_jogos_live()  # tudo
        else:
            jogos = sofascore_live.listar_jogos_live(
                ligas_filter=tuple(args.ligas) if args.ligas else None,
                ids_ligas=IDS_LIGAS_DEFAULT if not args.ligas else None,
            )
        sofa_ok = sofascore_live.ultima_listagem_live_ok
    except Exception as exc:  # noqa: BLE001
        # HTTP 500 do flaresolverr, timeout, etc. Nao crashamos o ciclo —
        # tratamos como "0 jogos" (circuit breaker ja tomou nota).
        log.warning("listar_jogos_live falhou: %s. Ciclo salta.", exc)
        print(f"    [ERRO Sofascore] {type(exc).__name__}: {exc}")
        jogos = []
        sofa_ok = False
    _actualizar_health_sofa_live(sofa_ok, args.telegram)
    if not jogos:
        if sofa_ok:
            log.info("Ciclo: 0 jogos live (nada para analisar)")
            print("    (sem jogos live nas ligas que seguimos)")
        else:
            log.info("Ciclo: Sofascore falhou (erro/bloqueio), ciclo salta")
            print("    (Sofascore falhou neste ciclo)")
        return
    log.info("Ciclo: %d jogos live encontrados", len(jogos))
    print(f"    {len(jogos)} jogos encontrados.")

    todas_sugestoes = []
    for ev in jogos:
        event_id = ev.get("id")
        if not event_id:
            continue
        # PRE-FILTRO: salta cedo jogos de ligas nao-top sem historico
        # suficiente, antes de gastar 6 pedidos Sofascore por jogo.
        # Isto evita o scanner encravar quando ha muitos jogos obscuros
        # (2as divisoes, taças, amigaveis) no feed.
        try:
            liga_nome = (ev.get("tournament") or {}).get("name") or ""
            if liga_nome:
                home_id_ev = (ev.get("homeTeam") or {}).get("id") or 0
                away_id_ev = (ev.get("awayTeam") or {}).get("id") or 0
                from apostas.dados_referencia.ligas_prioritarias import (
                    nome_e_prioridade_1,
                )
                if (not nome_e_prioridade_1(liga_nome)
                        and not _ambas_fichas_fortes(home_id_ev, away_id_ev)):
                    log.debug(
                        "Pre-filtro: skip %s (liga %s nao top + fichas fracas)",
                        event_id, liga_nome,
                    )
                    continue
        except Exception as exc:  # noqa: BLE001
            log.debug("Pre-filtro falhou: %s", exc)
        try:
            estado = sofascore_live.obter_estado_live(event_id)
        except _flaresolverr.FlaresolverrIndisponivel as exc:
            print(f"    FLARESOLVERR OFF: {exc}")
            return
        if estado is None:
            continue
        # Filtra paises banidos (Russia, Belarus...) ANTES de qualquer analise.
        # Jogos que a bet365 nao oferece nao devem gastar atencao do user.
        banido, razao = paises_banidos.jogo_banido(
            estado.pais, estado.liga, estado.home, estado.away,
        )
        if banido:
            print(f"    [SKIP banido] {estado.home} vs {estado.away} — {razao}")
            continue
        # Grava snapshot do estado para historico/calibracao futura (Nivel 2)
        try:
            _gravar_snapshot(estado)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha gravar snapshot %s: %s", estado.event_id, exc)
        # Modelo corre SEMPRE, nao depende de ter odds da Odds API.
        # Fase 5: ajustes do cerebro (confronto 2 fichas) injectados.
        ajustes = _ajustes_cerebro_para(estado)
        prev = live_predictor.prever(estado, ajustes_cerebro=ajustes)
        _imprimir_estado(estado, prev)

        # PROJECCOES aos 40'/55'/70': substituem sinais "faceis" de
        # remates/SOT/cantos. Mostram projeccao final + odd justa por linha,
        # para o user comparar com o que a casa paga.
        try:
            n_proj = _enviar_projecoes_jogo(
                estado, prev, args.telegram,
                min_estrelas=args.projecao_tele_estrelas,
            )
            if n_proj:
                print(f"    📊 {n_proj} projeccao(oes) enviadas ao Telegram.")
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha enviar projeccao %s: %s", estado.event_id, exc)

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
            # Converte sinais em dicts. So os de ALTA CONVICCAO vao para
            # Telegram (via marker _alta_conviccao). Os outros aparecem
            # no terminal para analise mas nao notificam.
            for sinal in sinais:
                sinal, prob_bruta = _calibrar_sinal(estado, sinal)
                sug = _sinal_para_sugestao(estado, sinal)
                sug["prob_bruta"] = round(prob_bruta, 4)
                # Marca se o analista apostaria aqui o seu proprio dinheiro
                sug["_alta_conviccao"] = analista._conviccao_alta(sinal, estado)
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

            # M17: carrega perfis historicos (M16) uma vez por ciclo para
            # avaliar dual signal. Reaproveita o CSV live resolvido.
            perfis_hist: dict | None = None
            perfis_mercado: dict | None = None
            try:
                from apostas.alertas.historico_alertas import (
                    agregar_por_mercado, carregar_perfis,
                )
                perfis_hist = carregar_perfis(_ficheiro_csv())
                # M23: agrega por (liga, mercado) para o filtro de desempenho.
                perfis_mercado = agregar_por_mercado(perfis_hist) if perfis_hist else None
            except Exception as exc:  # noqa: BLE001
                log.debug("Carga perfis para dual-sinal/desempenho falhou: %s", exc)
                perfis_hist = None
                perfis_mercado = None
            for s in todas_sugestoes:
                # Blacklist por liga (dados reais: Brasileirao -42% ROI,
                # LaLiga 2 -35% ROI com n>=50). Mantida como override extremo
                # — M23 ja refina por (liga, mercado) mas isto apanha ligas
                # inteiras muito mas quando o filtro ainda nao tem volume.
                if analista.liga_na_blacklist(s.get("liga", "")):
                    continue
                # M23: filtro de desempenho — bloqueia (liga, mercado) com
                # ROI real < -5% em n_resolvidos >= 30. Preserva mercados
                # ganhadores (remates, SOT) mesmo em ligas globalmente fracas.
                dec = filtro_desempenho.avaliar(
                    perfis_mercado, s.get("liga", ""), s.get("mercado", ""),
                )
                if dec.bloquear:
                    continue
                # Mercado 1X2 live: dados reais (n=188, ROI -27%, hit 54%)
                # mostraram que o modelo nao bate a casa aqui. Previsao
                # live de resultado final e NOTORIAMENTE dificil (um golo
                # vira o jogo). Deixa aparecer no terminal para acumular
                # mais dados mas nao notifica Telegram.
                if s.get("mercado") == "1x2":
                    continue
                # Mercados em `_MERCADOS_SO_PROJECAO` (cantos, sot): sinal
                # pontual nao vai, so por projeccao 40'/55'/70'. Remates
                # e remates_a_baliza sairam da lista — M23 trata (liga,
                # mercado) por dados reais e eles mostram +25%/+48% ROI.
                if s.get("mercado") in _MERCADOS_SO_PROJECAO:
                    continue
                # MODO VENCEDOR: so notifica se mercado vencedor OR liga
                # vencedora (dados reais n=1927). Resto fica no terminal.
                if not analista.passa_zona_vencedora(
                    s.get("mercado", ""), s.get("liga", "")
                ):
                    continue
                # §19.3 doc estrategias: sinais com discrepancia grande
                # p_modelo vs p_mercado (>10pp) ou EV_puro >15% CONTINUAM
                # a ir para Telegram, mas com flag ⚠ VERIFICAR para o user
                # julgar (onze? lesao? dados corrompidos?). Nao esconder.
                if s.get("casa") == "ANALISTA":
                    # HARD FLOOR: user quer so sinais 3-5 estrelas no Telegram.
                    # Mesmo com _alta_conviccao, se estrelas < 3 NAO envia.
                    try:
                        n_estrelas = int(s.get("estrelas", 0))
                    except (ValueError, TypeError):
                        n_estrelas = 0
                    if n_estrelas < args.analista_tele_estrelas:
                        continue
                    # EDGE MINIMO: analista sem Odds API usa odd_valor fair.
                    # Se odd fair < 1.40 E prob < 0.82, a aposta tem margem
                    # fina — 1 unidade abaixo da linha significa perda certa.
                    # Caso real Lecce-Genoa Over 30.5 remates (prob 79%,
                    # odd alvo 1.33) perdeu por 1 remate. Elimina lotarias.
                    try:
                        prob_val = float(s.get("prob_modelo", 0))
                        odd_val = float(s.get("odd", 0))
                    except (ValueError, TypeError):
                        prob_val = odd_val = 0.0
                    if (odd_val > 0 and odd_val < 1.40
                            and prob_val < 0.82):
                        log.info(
                            "Analista %s %s/%s: skip — edge fino "
                            "(odd %.2f, prob %.0f%%)",
                            s.get("event_id"), s.get("mercado"),
                            s.get("lado"), odd_val, prob_val * 100,
                        )
                        continue
                    notificar.append(s)
                else:
                    # Sugestao com EV real (Odds API)
                    try:
                        ev_val = float(s.get("ev", 0))
                        n_estrelas = int(s.get("estrelas", 0))
                        odd_val = float(s.get("odd", 0))
                    except (ValueError, TypeError):
                        continue
                    # HARD FLOOR 3 estrelas tambem aqui — mesmo com EV alto,
                    # 1-2 estrelas significa historicamente ROI fraco
                    if n_estrelas < args.analista_tele_estrelas:
                        continue
                    if ev_val < ev_min_telegram:
                        continue
                    # EDGE FINO: EV >= 7% mas odd < 1.40 significa que a
                    # probabilidade implicita e >= 71% — margem para erro
                    # minima. 1 unidade de desvio na projeccao destroi o EV.
                    if odd_val > 0 and odd_val < 1.40 and ev_val < 0.12:
                        log.info(
                            "EV %s %s/%s: skip — edge fino "
                            "(odd %.2f, EV %.1f%%)",
                            s.get("event_id"), s.get("mercado"),
                            s.get("lado"), odd_val, ev_val * 100,
                        )
                        continue
                    notificar.append(s)
            # M17: carimba `dual_sinal=True` quando historico forte confirma
            # o edge. Nao filtra — so marca para o telegram_live mostrar o
            # badge e para o ranking rebocar primeiro.
            for s in notificar:
                try:
                    res_dual = dual_sinal.avaliar_dual_sinal(
                        s, perfis_historicos=perfis_hist,
                    )
                    s["dual_sinal"] = res_dual.e_dual
                except Exception as exc:  # noqa: BLE001
                    log.debug("Dual-sinal falhou em %s: %s", s.get("mercado"), exc)
                    s["dual_sinal"] = False

            # Score para ranking: edge × fator_mercado (Caan Berry:
            # ligas pouco monitoradas, nicho em Big-5, fade mediaticas).
            # Sinais DUAL (M17) ganham ×1.5 — duas confirmacoes puxam o
            # ranking para o topo.
            def _score(s):
                try:
                    prob = float(s.get("prob_modelo", 0))
                    odd = float(s.get("odd", 0))
                    edge = prob * odd
                    fator = analista.fator_mercado(
                        s.get("liga", ""), s.get("home", ""), s.get("away", ""),
                        s.get("mercado", ""), s.get("lado", ""),
                    )
                    base = edge * fator
                    if s.get("dual_sinal"):
                        base *= 1.5
                    return base
                except (ValueError, TypeError):
                    return 0
            notificar.sort(key=_score, reverse=True)

            # Max N notificacoes por ciclo (default 3) — top do ranking
            max_por_ciclo = args.max_notif_por_ciclo
            enviadas = 0
            jogos_notificados_este_ciclo = set()
            for s in notificar:
                if enviadas >= max_por_ciclo:
                    break
                event_id = s.get("event_id")
                mercado = s["mercado"]
                lado = s["lado"]
                linha = s.get("linha", "")
                jogo_key = (s["home"], s["away"])
                # Cooldown dedup entre ciclos
                if _em_cooldown(event_id, mercado, lado, linha):
                    continue
                # Max 1 notificacao por jogo por ciclo
                if jogo_key in jogos_notificados_este_ciclo:
                    continue
                # Modo exigente: EV>=0.10 + mega-cerebro confianca>=2 +
                # ambas as fichas com >=5 jogos
                if args.modo_exigente:
                    try:
                        if not _passa_modo_exigente(s):
                            continue
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Filtro exigente falhou: %s", exc)
                        continue
                # BLACKLIST ROI historico (granularidade por lado):
                # Prioriza (liga, mercado, lado); cai para (liga, mercado)
                # se nao ha volume no lado. 10 alertas + ROI <= -5% bloqueia.
                try:
                    if _blacklist_roi_bloqueia(
                        s.get("liga", ""), mercado, lado,
                    ):
                        log.info(
                            "Sinal %s %s/%s/%s: skip — ROI historico "
                            "negativo (categoria perdedora)",
                            event_id, s.get("liga"), mercado, lado,
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    log.debug("Blacklist ROI falhou: %s", exc)
                # QUARENTENA PARA LIGAS OBSCURAS: se nao e liga top (p1) E
                # nao temos pelo menos 5 alertas resolvidos nesta (liga,
                # mercado) para julgar, nao enviamos — nao sabemos se o
                # modelo funciona nessa combinacao.
                try:
                    if not _liga_top_ou_com_historico(
                        s.get("liga", ""), mercado,
                    ):
                        log.info(
                            "Sinal %s %s/%s: skip — liga nao-top sem "
                            "historico suficiente (quarentena)",
                            event_id, s.get("liga"), mercado,
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    log.debug("Quarentena liga falhou: %s", exc)
                # FICHAS FRACAS: ambas as equipas precisam de >= 5 jogos
                # na ficha. Sem isso, modelo projeta por extrapolacao
                # vazia. Caso real do /saude: 61% das equipas tem <5 jogos.
                try:
                    if not _ambas_fichas_fortes(
                        s.get("home_id"), s.get("away_id"),
                    ):
                        n_h = _n_jogos_ficha(s.get("home_id") or 0)
                        n_a = _n_jogos_ficha(s.get("away_id") or 0)
                        log.info(
                            "Sinal %s %s vs %s: skip — fichas fracas "
                            "(home=%d, away=%d, min %d)",
                            event_id, s.get("home"), s.get("away"),
                            n_h, n_a, _MIN_JOGOS_FICHA,
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    log.debug("Fichas fortes check falhou: %s", exc)
                telegram_live.enviar_sugestao_live(s)
                _marcar_enviado(event_id, mercado, lado, linha)
                jogos_notificados_este_ciclo.add(jogo_key)
                enviadas += 1
            if enviadas:
                print(f"  → {enviadas}/{len(notificar)} notificacoes Telegram enviadas "
                      f"(top {max_por_ciclo} do ranking)")


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
                             "Default: Big-5 + segundas + competicoes europeias + tacas.")
    parser.add_argument("--todas-ligas", action="store_true",
                        help="Analisa TODOS os jogos (incluindo ligas obscuras). "
                             "Util para teste mas enche de ruido.")
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
    parser.add_argument("--max-notif-por-ciclo", type=int, default=3,
                        help="Max notificacoes Telegram por ciclo (default 3). "
                             "Ranking: top edge × √racionais × forca.")
    parser.add_argument("--analista-tele-estrelas", type=int, default=4,
                        help="Sinais do ANALISTA com >= N estrelas vao sempre para "
                             "Telegram, mesmo sem passar filtro Paulo Rebelo "
                             "(default 4). Pon 5 para so os de 5 estrelas; "
                             "2 para quase tudo.")
    parser.add_argument("--projecao-tele-estrelas", type=int, default=4,
                        help="Projeccoes (40'/55'/70') so vao para Telegram com "
                             ">= N estrelas (default 4). 1-3 estrelas ficam so "
                             "no log — nao poluem o Telegram.")
    parser.add_argument("--modo-exigente", action="store_true",
                        help="So manda sinais que passam o filtro combinado: "
                             "EV>=0.10 + mega-cerebro com confianca>=2 + "
                             "ambas as fichas com >=5 jogos. Reduz volume mas "
                             "cada alerta tem concordancia multi-camada.")
    parser.add_argument("--rapido", action="store_true",
                        help="Salta endpoints Sofascore opcionais (graph + "
                             "comments) para reduzir carga por jogo. Util "
                             "quando o scanner esta a encravar com muitos "
                             "jogos no feed. Perde xG momentum e narrativa "
                             "do commentary mas poupa 2 chamadas por jogo.")
    args = parser.parse_args()

    # Mata scanners antigos (orfaos de restarts) antes de arrancar: 2 scanners
    # na mesma sessao flaresolverr dao HTTP 500 e alertas em duplicado.
    from apostas.utils.instancia_unica import garantir_instancia_unica
    mortos = garantir_instancia_unica("scanner_live_v2.py")
    if mortos:
        print(f"  Terminados {len(mortos)} scanner(s) antigo(s): {mortos}")

    # Activa modo rapido (menos chamadas Sofascore) via env que a ingestao le.
    import os as _os_main
    if args.rapido:
        _os_main.environ["SOFA_LIVE_RAPIDO"] = "1"

    print("Scanner LIVE v2 iniciado.")
    print(f"  Intervalo: {args.intervalo_s}s  EV min: {args.ev_minimo*100:.0f} por cento")
    print(f"  Flaresolverr: {_flaresolverr._url_flaresolverr()}")
    if args.rapido:
        print("  Modo rapido ON (sem graph/comments)")
    print("Sai com Ctrl+C.\n")

    try:
        ciclo = 0
        while True:
            ciclo += 1
            inicio_ciclo = agora_utc()
            try:
                _correr_ciclo(args)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro ciclo %d", ciclo)
                print(f"    ERRO ciclo {ciclo}: {exc}")
            duracao = (agora_utc() - inicio_ciclo).total_seconds()
            if duracao > 300:  # 5 min
                log.warning(
                    "Ciclo %d demorou %.0fs (lento) — possivel trava Sofascore. "
                    "Considera activar --rapido ou aumentar --intervalo-s.",
                    ciclo, duracao,
                )
                print(f"    ⚠ Ciclo {ciclo} demorou {duracao:.0f}s")
            if args.ciclos and ciclo >= args.ciclos:
                break
            time.sleep(args.intervalo_s)
    except KeyboardInterrupt:
        print("\nInterrompido. A fechar sessao flaresolverr...")
    finally:
        _flaresolverr.encerrar_sessao()


if __name__ == "__main__":
    main()
