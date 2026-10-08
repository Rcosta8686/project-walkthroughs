"""Ingestão de odds pré-jogo em tempo real via The Odds API.

Documentação: https://the-odds-api.com/liveapi/guides/v4/

Modelo de preços (plano FREE):
  500 credits/mês. Cada chamada a /sports/{sport}/odds consome
  (regions × markets) créditos. Com regions=eu + markets=h2h,totals =
  2 créditos/sport × 6 ligas = 12 créditos por puxada diária →
  ~41 puxadas/mês → mais que suficiente para 1x por dia.

Guarda as odds em ``odds_correntes`` (não em ``odds_fecho``) porque
mudam ao longo do tempo. A análise live prefere estas às históricas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
from sqlalchemy import select

from apostas.ingestao import _mocks_odds_api
from apostas.utils.config import get_env
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, Jogo, Liga, OddsCorrentes

log = get_logger(__name__)

_BASE_URL = "https://api.the-odds-api.com/v4"

# Mapeamento liga do config → chave de sport na Odds API
LIGAS_ODDS_API = {
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Serie A": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga",
    "Ligue 1": "soccer_france_ligue_one",
    "Liga Portugal": "soccer_portugal_primeira_liga",
}

# Nomes vindos da Odds API → nomes como estão no football-data (BD).
# Expandido com base nos "equipas sem match" vistos em run real.
_ALIAS_ODDS_API = {
    # ─── EPL ─────────────────────────────────────────────────────
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Wolverhampton Wanderers": "Wolves",
    "Tottenham Hotspur": "Tottenham",
    "Brighton and Hove Albion": "Brighton",
    "West Ham United": "West Ham",
    "Leicester City": "Leicester",
    "Leeds United": "Leeds",
    "Sheffield United": "Sheffield United",
    "Luton Town": "Luton",
    "Ipswich Town": "Ipswich",
    "Coventry City": "Coventry",       # Championship — pode nem estar na BD
    "Burnley": "Burnley",
    "Sunderland AFC": "Sunderland",
    "Leeds": "Leeds",
    "Southampton": "Southampton",
    "Southampton FC": "Southampton",
    "West Ham": "West Ham",
    "Everton FC": "Everton",
    "Everton": "Everton",
    "AFC Bournemouth": "Bournemouth",
    "Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford",
    "Brentford": "Brentford",
    "Fulham FC": "Fulham",
    "Fulham": "Fulham",
    "Crystal Palace": "Crystal Palace",
    "Aston Villa": "Aston Villa",
    "Arsenal FC": "Arsenal",
    "Arsenal": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Chelsea": "Chelsea",
    "Liverpool FC": "Liverpool",
    "Liverpool": "Liverpool",

    # ─── La Liga ─────────────────────────────────────────────────
    "Atlético Madrid": "Ath Madrid",
    "Atletico Madrid": "Ath Madrid",
    "Athletic Bilbao": "Ath Bilbao",
    "Athletic Club": "Ath Bilbao",
    "Real Sociedad": "Sociedad",
    "Real Betis": "Betis",
    "Celta de Vigo": "Celta",
    "Celta Vigo": "Celta",
    "RC Celta de Vigo": "Celta",
    "Rayo Vallecano": "Vallecano",
    "Alavés": "Alaves",
    "Deportivo Alavés": "Alaves",
    "CA Osasuna": "Osasuna",
    "RCD Mallorca": "Mallorca",
    "Villarreal CF": "Villarreal",
    "UD Almería": "Almeria",
    "Girona FC": "Girona",
    "Getafe CF": "Getafe",
    "RCD Espanyol": "Espanol",   # football-data usa 'Espanol' (sem y)
    "Espanyol": "Espanol",
    "Espanyol Barcelona": "Espanol",
    "UD Las Palmas": "Las Palmas",
    "Real Valladolid": "Valladolid",
    "CD Leganés": "Leganes",
    "Leganés": "Leganes",
    "Cadiz CF": "Cadiz",
    "Elche CF": "Elche",
    "Elche": "Elche",
    "Real Oviedo": "Oviedo",
    "Levante UD": "Levante",
    "Deportivo La Coruña": "La Coruna",
    "Deportivo": "La Coruna",

    # ─── Serie A ─────────────────────────────────────────────────
    "AC Milan": "Milan",
    "AS Roma": "Roma",
    "Hellas Verona": "Verona",
    "Atalanta BC": "Atalanta",
    "Internazionale": "Inter",
    "Inter Milan": "Inter",
    "ACF Fiorentina": "Fiorentina",
    "SSC Napoli": "Napoli",
    "SS Lazio": "Lazio",
    "US Lecce": "Lecce",
    "Bologna FC": "Bologna",
    "Genoa CFC": "Genoa",
    "Udinese Calcio": "Udinese",
    "Cagliari Calcio": "Cagliari",
    "Torino FC": "Torino",
    "US Sassuolo": "Sassuolo",
    "Empoli FC": "Empoli",
    "AC Monza": "Monza",
    "US Salernitana": "Salernitana",
    "Frosinone Calcio": "Frosinone",
    "Parma Calcio 1913": "Parma",
    "Venezia FC": "Venezia",
    "Como 1907": "Como",

    # ─── Bundesliga ──────────────────────────────────────────────
    "Bayern München": "Bayern Munich",
    "FC Bayern München": "Bayern Munich",
    "Borussia Dortmund": "Dortmund",
    "BV Borussia 09 Dortmund": "Dortmund",
    "Bayer Leverkusen": "Leverkusen",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "Borussia Mönchengladbach": "M'gladbach",
    "Borussia Monchengladbach": "M'gladbach",
    "VfL Wolfsburg": "Wolfsburg",
    "VfB Stuttgart": "Stuttgart",
    "VfL Bochum": "Bochum",
    "1. FC Köln": "FC Koln",
    "1. FC Union Berlin": "Union Berlin",
    "1. FC Heidenheim": "Heidenheim",
    "1. FSV Mainz 05": "Mainz",
    "FSV Mainz 05": "Mainz",
    "Mainz 05": "Mainz",
    "Mainz": "Mainz",
    "SC Freiburg": "Freiburg",
    "TSG 1899 Hoffenheim": "Hoffenheim",
    "SV Werder Bremen": "Werder Bremen",
    "FC Augsburg": "Augsburg",
    "FC St. Pauli": "St Pauli",
    "Hertha BSC": "Hertha",
    "FC Schalke 04": "Schalke 04",

    # ─── Ligue 1 ─────────────────────────────────────────────────
    "Paris Saint-Germain": "Paris SG",
    "Olympique Marseille": "Marseille",
    "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon",
    "AS Saint-Étienne": "St Etienne",
    "AS Monaco": "Monaco",
    "OGC Nice": "Nice",
    "Stade Rennais": "Rennes",
    "Stade Rennais FC": "Rennes",
    "RC Strasbourg": "Strasbourg",
    "FC Nantes": "Nantes",
    "LOSC Lille": "Lille",
    "Montpellier HSC": "Montpellier",
    "RC Lens": "Lens",
    "Stade Brestois 29": "Brest",
    "Stade de Reims": "Reims",
    "Toulouse FC": "Toulouse",
    "FC Lorient": "Lorient",
    "Clermont Foot 63": "Clermont",
    "Le Havre AC": "Le Havre",
    "AJ Auxerre": "Auxerre",
    "Angers SCO": "Angers",

    # ─── Liga Portugal ───────────────────────────────────────────
    # football-data P1 CSVs usam nomes curtos: "Porto", "Braga", "Famalicao" etc.
    "FC Porto": "Porto",
    "Porto": "Porto",
    "SL Benfica": "Benfica",
    "Benfica": "Benfica",
    "Sporting CP": "Sp Lisbon",
    "Sporting Clube de Portugal": "Sp Lisbon",
    "Sporting Lisbon": "Sp Lisbon",
    "SC Braga": "Braga",
    "Braga": "Braga",
    "Sporting Braga": "Braga",
    "Vitória SC": "Guimaraes",
    "V Guimaraes": "Guimaraes",
    "Vitoria Guimaraes": "Guimaraes",
    "Vitória de Guimarães": "Guimaraes",
    "CS Marítimo": "Maritimo",
    "CS Maritimo": "Maritimo",
    "Maritimo": "Maritimo",
    "GD Chaves": "Chaves",
    "Chaves": "Chaves",
    "GD Estoril Praia": "Estoril",
    "Estoril Praia": "Estoril",
    "Estoril": "Estoril",
    "Portimonense SC": "Portimonense",
    "Portimonense": "Portimonense",
    "Rio Ave FC": "Rio Ave",
    "Rio Ave": "Rio Ave",
    "FC Famalicão": "Famalicao",
    "Famalicão": "Famalicao",
    "Famalicao": "Famalicao",
    "Casa Pia AC": "Casa Pia",
    "Casa Pia": "Casa Pia",
    "Boavista FC": "Boavista",
    "Boavista": "Boavista",
    "Moreirense FC": "Moreirense",
    "Moreirense": "Moreirense",
    "CF Os Belenenses": "Belenenses",
    "Belenenses": "Belenenses",
    "CF Estrela": "Estrela",
    "CF Estrela da Amadora": "Estrela",
    "Estrela da Amadora": "Estrela",
    "Académico de Viseu": "Ac Viseu",
    "Academico de Viseu": "Ac Viseu",
    "AVS Futebol SAD": "AVS",
    "AVS": "AVS",
    "Nacional da Madeira": "Nacional",
    "CD Nacional": "Nacional",
    "Nacional": "Nacional",
    "FC Arouca": "Arouca",
    "Arouca": "Arouca",
    "Gil Vicente FC": "Gil Vicente",
    "Gil Vicente": "Gil Vicente",
    "CD Tondela": "Tondela",
    "Tondela": "Tondela",
    "SC Farense": "Farense",
    "Farense": "Farense",
    "GD Alverca": "Alverca",
    "Alverca": "Alverca",
}


@dataclass
class ResultadoOddsApi:
    jogos_atualizados: int = 0
    jogos_criados: int = 0
    odds_criadas: int = 0
    odds_atualizadas: int = 0
    jogos_sem_match: int = 0
    equipas_sem_match: set = field(default_factory=set)
    erros: list[str] = field(default_factory=list)
    credits_restantes: int | None = None


def _epoca_de(data: datetime) -> int:
    """Devolve o ano de início da época para uma data (temporada europeia Jul-Jun)."""
    return data.year if data.month >= 7 else data.year - 1


# Soft books (margens maiores, linhas menos eficientes que Pinnacle).
# Pinnacle (sharp) usado como preço "justo"; soft books como preços a bater.
# Nomes correctos conforme Odds API (confirmados via verificar_bookmaker.py):
#   - betano_uk (não "betano"), onexbet (não "1xbet"), unibet_uk (não "unibet")
#   - bet365 bloqueou a Odds API — não disponível em nenhum plano
BOOKMAKERS_DEFAULT = (
    "pinnacle,"
    # Softs UK genuínas (edge historico documentado):
    "paddypower,skybet,boylesports,betway,virginbet,betvictor,betfred_uk,"
    # Multi-nacional softs:
    "betano_uk,unibet_uk,coral,williamhill,sport888,"
    # Outras (menos efficient que Pinnacle mas ainda sharp):
    "onexbet,marathonbet"
)


def sincronizar(
    bookmakers: str = BOOKMAKERS_DEFAULT,
    regions: str = "eu,uk,us,au",
    modo: str | None = None,
) -> ResultadoOddsApi:
    """Puxa odds de pré-jogo para as ligas configuradas e grava em odds_correntes.

    Default pede Pinnacle (sharp, fair-price) + soft books (Betano, bet365, etc.).
    """
    modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
    resultado = ResultadoOddsApi()

    with abrir_sessao() as s:
        for nome_liga, sport_key in LIGAS_ODDS_API.items():
            liga = s.scalar(select(Liga).where(Liga.nome == nome_liga))
            if liga is None:
                resultado.erros.append(f"Liga '{nome_liga}' não existe na BD.")
                continue
            try:
                jogos = _puxar_odds_sport(sport_key, bookmakers, regions, modo, resultado)
            except Exception as exc:  # noqa: BLE001
                resultado.erros.append(f"{nome_liga}: {exc}")
                continue
            for jogo_json in jogos:
                _processar_jogo(s, jogo_json, liga, resultado)

    log.info(
        "The Odds API: %d jogos com odds (%d criados aqui), %d sem match, %d equipas sem match. "
        "Credits restantes: %s",
        resultado.jogos_atualizados, resultado.jogos_criados,
        resultado.jogos_sem_match,
        len(resultado.equipas_sem_match),
        resultado.credits_restantes,
    )
    return resultado


def _puxar_odds_sport(
    sport: str, bookmakers: str, regions: str, modo: str,
    resultado: ResultadoOddsApi,
) -> list[dict]:
    if modo == "desenvolvimento":
        return _mocks_odds_api.jogos_sport(sport)

    key = get_env("ODDS_API_KEY", required=True)
    url = f"{_BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": key,
        "regions": regions,
        # Expandido: 1X2, Over/Under, BTTS, Handicap Asiatico, Dupla Hipotese.
        # Cada mercado adicional cobra +1 credit por region por chamada.
        "markets": "h2h,totals,btts,spreads,double_chance",
        "oddsFormat": "decimal",
        "bookmakers": bookmakers,
    }
    log.info("GET %s/sports/%s/odds (regions=%s, %d bookmakers)",
             _BASE_URL, sport, regions, len(bookmakers.split(",")))
    resp = requests.get(url, params=params, timeout=30)
    if not resp.ok:
        raise requests.HTTPError(
            f"HTTP {resp.status_code} em /sports/{sport}/odds: {resp.text[:300]}"
        )

    # Guarda créditos restantes (vem no header)
    requests_remaining = resp.headers.get("x-requests-remaining")
    if requests_remaining is not None:
        try:
            resultado.credits_restantes = int(requests_remaining)
        except ValueError:
            pass

    data = resp.json()
    # Debug: quantos jogos vieram e quantos com bookmakers
    n_jogos = len(data) if isinstance(data, list) else 0
    com_bms = sum(1 for j in data if j.get("bookmakers")) if n_jogos else 0
    bms_vistos = set()
    for j in data if n_jogos else []:
        for bm in j.get("bookmakers", []):
            bms_vistos.add(bm.get("key", ""))
    log.info("  → %s: %d jogos (%d com bookmakers), casas vistas: %s",
             sport, n_jogos, com_bms, sorted(bms_vistos))
    return data


def _id_externo_odds_api(jogo_json: dict) -> int:
    """Chave sintética estável para um jogo da Odds API.

    A Odds API devolve `id` como string hex (ex. `d2c8...`). Mapeamos para
    int (hash estável de 63 bits) para caber na coluna `id_externo` INTEGER.
    Prefixado com `9` para distinguir de IDs football-data (que começam em `1`).
    """
    raw = str(jogo_json.get("id", ""))
    if not raw:
        raw = f"{jogo_json.get('home_team','')}|{jogo_json.get('away_team','')}|{jogo_json.get('commence_time','')}"
    # md5 estável entre runs (hash() do Python é salted por processo) → 15 dígitos
    # Prefixo 9 para distinguir de IDs football-data (que começam em 1).
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    h = int(digest, 16) % (10**15)
    return 9 * (10**15) + h


def _processar_jogo(s, jogo_json: dict, liga: Liga, resultado: ResultadoOddsApi) -> None:
    nome_casa = _canoniza(jogo_json.get("home_team", ""))
    nome_fora = _canoniza(jogo_json.get("away_team", ""))
    data_str = jogo_json.get("commence_time")
    if not nome_casa or not nome_fora or not data_str:
        return

    casa = s.scalar(
        select(Equipa).where(Equipa.nome == nome_casa, Equipa.liga_id == liga.id)
    )
    fora = s.scalar(
        select(Equipa).where(Equipa.nome == nome_fora, Equipa.liga_id == liga.id)
    )
    if casa is None:
        resultado.equipas_sem_match.add(jogo_json.get("home_team", ""))
        return
    if fora is None:
        resultado.equipas_sem_match.add(jogo_json.get("away_team", ""))
        return

    data = _parse_commence_time(data_str)
    if data is None:
        return

    # Procurar jogo (±36h)
    inicio = data - timedelta(hours=36)
    fim = data + timedelta(hours=36)
    jogo = s.scalar(
        select(Jogo).where(
            Jogo.liga_id == liga.id,
            Jogo.casa_id == casa.id,
            Jogo.fora_id == fora.id,
            Jogo.data_utc >= inicio,
            Jogo.data_utc <= fim,
        )
    )
    if jogo is None:
        # Jogo futuro ainda não ingerido pelo football-data (CSVs só têm jogos
        # jogados). Cria aqui para que a análise live tenha a quem atribuir
        # odds e sugestões.
        if data > datetime.utcnow():
            id_ext = _id_externo_odds_api(jogo_json)
            # Protege contra colisões: se já existe outro jogo com este id,
            # salta (não devia acontecer com hash de 63 bits).
            if s.scalar(select(Jogo).where(Jogo.id_externo == id_ext)) is not None:
                resultado.jogos_sem_match += 1
                return
            jogo = Jogo(
                id_externo=id_ext,
                liga_id=liga.id,
                epoca=_epoca_de(data),
                data_utc=data,
                casa_id=casa.id,
                fora_id=fora.id,
                estado="agendado",
            )
            s.add(jogo)
            s.flush()
            resultado.jogos_criados += 1
        else:
            resultado.jogos_sem_match += 1
            return

    bookmakers = jogo_json.get("bookmakers", [])
    atualizou = False
    for bm in bookmakers:
        casa_apostas = bm.get("key", "unknown")
        for mercado in bm.get("markets", []):
            if _grava_mercado(s, jogo, mercado, casa_apostas, nome_casa, nome_fora, resultado):
                atualizou = True
    if atualizou:
        resultado.jogos_atualizados += 1


def _grava_mercado(
    s, jogo: Jogo, mercado: dict, casa_apostas: str,
    nome_casa: str, nome_fora: str, resultado: ResultadoOddsApi,
) -> bool:
    tipo = mercado.get("key")
    outcomes = mercado.get("outcomes", [])
    atualizou = False

    if tipo == "h2h":
        # Resultado final (1X2)
        for o in outcomes:
            lado = _lado_1x2(o.get("name"), nome_casa, nome_fora)
            if lado is None:
                continue
            if _upsert_odd(s, jogo.id, "1x2", None, lado, float(o["price"]), casa_apostas, resultado):
                atualizou = True

    elif tipo == "totals":
        # Over/Under golos
        for o in outcomes:
            nome = (o.get("name") or "").lower()
            if nome not in ("over", "under"):
                continue
            linha = float(o.get("point", 2.5))
            if _upsert_odd(s, jogo.id, "golos", linha, nome, float(o["price"]), casa_apostas, resultado):
                atualizou = True

    elif tipo == "btts":
        # Both Teams To Score: outcomes {"name": "Yes"/"No", "price": ...}
        for o in outcomes:
            nome = (o.get("name") or "").lower()
            if nome not in ("yes", "no"):
                continue
            lado = "sim" if nome == "yes" else "nao"
            if _upsert_odd(s, jogo.id, "btts", None, lado, float(o["price"]), casa_apostas, resultado):
                atualizou = True

    elif tipo == "spreads":
        # Asian Handicap: outcomes {"name": nome_equipa, "price": ..., "point": +/-X.X}
        for o in outcomes:
            lado = _lado_1x2(o.get("name"), nome_casa, nome_fora)
            if lado is None or lado == "empate":
                continue  # handicap tem só 2 outcomes (casa/fora)
            try:
                point = float(o.get("point"))
            except (TypeError, ValueError):
                continue
            if _upsert_odd(s, jogo.id, "handicap", point, lado, float(o["price"]), casa_apostas, resultado):
                atualizou = True

    elif tipo == "double_chance":
        # Dupla hipotese: outcomes {"name": "Home/Draw"|"Home/Away"|"Draw/Away", "price": ...}
        for o in outcomes:
            nome = (o.get("name") or "")
            lado = _lado_dupla(nome, nome_casa, nome_fora)
            if lado is None:
                continue
            if _upsert_odd(s, jogo.id, "dupla", None, lado, float(o["price"]), casa_apostas, resultado):
                atualizou = True
    return atualizou


def _lado_dupla(nome: str, nome_casa: str, nome_fora: str) -> str | None:
    """Mapeia o nome do outcome da Odds API para codigo 1X / X2 / 12."""
    if not nome:
        return None
    nome_n = nome.replace(" ", "").lower()
    # Formato Odds API: "Home/Draw", "Draw/Away", "Home/Away"
    if "draw" in nome_n and ("home" in nome_n or _canoniza_part(nome, nome_casa) == nome_casa):
        return "1x"  # casa ou empate
    if "draw" in nome_n and ("away" in nome_n or _canoniza_part(nome, nome_fora) == nome_fora):
        return "x2"  # empate ou fora
    if "home" in nome_n and "away" in nome_n:
        return "12"  # casa ou fora
    # Outros formatos: 'Casa/Empate'
    return None


def _canoniza_part(nome: str, equipa_nome: str) -> str:
    # Trata caso em que o nome tem format 'Arsenal/Draw'
    for p in nome.split("/"):
        if p.strip() == equipa_nome:
            return equipa_nome
    return nome


def _upsert_odd(
    s, jogo_id: int, tipo: str, linha: float | None, lado: str,
    odd: float, casa_apostas: str, resultado: ResultadoOddsApi,
) -> bool:
    existente = s.scalar(
        select(OddsCorrentes).where(
            OddsCorrentes.jogo_id == jogo_id,
            OddsCorrentes.mercado == tipo,
            OddsCorrentes.linha == linha,
            OddsCorrentes.lado == lado,
            OddsCorrentes.casa_de_apostas == casa_apostas,
        )
    )
    if existente is None:
        s.add(OddsCorrentes(
            jogo_id=jogo_id, mercado=tipo, linha=linha, lado=lado,
            odd=odd, casa_de_apostas=casa_apostas,
        ))
        resultado.odds_criadas += 1
        return True
    # Actualiza se o preço mudou significativamente (>0.5%)
    if abs(existente.odd - odd) / existente.odd > 0.005:
        existente.odd = odd
        existente.timestamp = datetime.utcnow()
        resultado.odds_atualizadas += 1
        return True
    return False


def _lado_1x2(nome: str | None, nome_casa: str, nome_fora: str) -> str | None:
    if nome is None:
        return None
    nome_lower = nome.lower()
    if nome_lower == "draw":
        return "empate"
    if nome == nome_casa or _canoniza(nome) == nome_casa:
        return "casa"
    if nome == nome_fora or _canoniza(nome) == nome_fora:
        return "fora"
    return None


def _canoniza(nome_odds_api: str) -> str:
    return _ALIAS_ODDS_API.get(nome_odds_api, nome_odds_api)


def _parse_commence_time(s: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
