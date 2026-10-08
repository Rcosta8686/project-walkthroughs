"""Ingestão de xG (expected goals) do Understat.

Understat é uma fonte gratuita (não oficial) com dados Opta-style:
expected goals (xG) por equipa, por jogo, para as 5 top ligas desde
2014/15. Os dados estão embutidos como JSON dentro das páginas HTML.

Formato típico da URL:
    https://understat.com/league/{liga}/{ano}

onde `liga` é uma de: EPL, La_liga, Serie_A, Bundesliga, Ligue_1
e `ano` é o ano de início da época (ex.: 2024 para 2024/25).

Não cobre Primeira Liga Portugal — essa fica sem xG até arranjarmos
outra fonte.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import select

from apostas.ingestao import aliases as _aliases
from apostas.ingestao import _mocks_understat
from apostas.utils.config import get_env, project_root
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, EstatisticasJogo, Jogo, Liga

log = get_logger(__name__)

# Mapeamento liga do nosso config → slug do Understat
LIGAS_UNDERSTAT = {
    "Premier League": "EPL",
    "La Liga": "La_liga",
    "Serie A": "Serie_A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue_1",
}

_BASE_URL = "https://understat.com/league"

# A Understat embute JSON em várias formas; apanhamos single e double quotes.
_PAD_HTML_RE = re.compile(
    r"var\s+(\w+)\s*=\s*JSON\.parse\(['\"](?P<payload>[^'\"]+)['\"]\)"
)
# Fallback: aceita conteúdo com aspas internas escapadas (\')
_PAD_HTML_RE_RELAXADO = re.compile(
    r"var\s+(\w+)\s*=\s*JSON\.parse\('((?:\\.|[^'\\])*)'\)"
)
# Nomes possíveis do array de jogos.
# Na página de liga: "datesData"; nos mocks e algumas páginas antigas: "matchesData".
_NOMES_MATCHES = {"datesData", "matchesData"}


@dataclass
class ResultadoIngestaoUnderstat:
    jogos_atualizados: int = 0
    jogos_sem_match: int = 0
    equipas_sem_match: set = field(default_factory=set)
    erros: list[str] = field(default_factory=list)


def _caminho_cache(liga: str, ano: int) -> Path:
    return project_root() / "dados" / "raw" / "understat" / f"{ano}_{liga}.html"


def _obter_html(liga: str, ano: int, modo: str) -> str:
    """Devolve o HTML da página de Understat para liga+ano, com cache local."""
    if modo == "desenvolvimento":
        return _mocks_understat.gerar_html(liga, ano)

    cache = _caminho_cache(liga, ano)
    if cache.exists():
        return cache.read_text(encoding="utf-8")

    url = f"{_BASE_URL}/{liga}/{ano}"
    log.info("A descarregar %s", url)
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Ricardo Tips"}, timeout=60)
    resp.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(resp.text, encoding="utf-8")
    return resp.text


def _parse_matches_json(html: str) -> list[dict]:
    """Extrai o array de jogos (datesData/matchesData) do HTML da Understat."""
    # Diagnóstico: lista todos os `var X = JSON.parse(...)` encontrados
    variaveis_vistas: list[str] = []

    for regex in (_PAD_HTML_RE, _PAD_HTML_RE_RELAXADO):
        for match in regex.finditer(html):
            nome = match.group(1)
            if nome not in variaveis_vistas:
                variaveis_vistas.append(nome)
            if nome not in _NOMES_MATCHES:
                continue
            valor_escapado = match.group(2)
            try:
                decoded = valor_escapado.encode("utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                continue
            try:
                return json.loads(decoded)
            except json.JSONDecodeError as exc:
                log.warning("Falha a parsear %s: %s", nome, exc)
                continue

    if variaveis_vistas:
        log.warning(
            "Understat: nenhum datesData/matchesData, mas vi: %s "
            "(cola isto no chat para diagnóstico)",
            variaveis_vistas[:10],
        )
    else:
        log.warning(
            "Understat: HTML não tem padrões JSON.parse esperados "
            "(primeiros 500 chars: %s)",
            html[:500].replace("\n", " "),
        )
    return []


_CACHE_ALIAS_UNDERSTAT = {
    # Nome Understat → nome canónico (como aparece na BD vindo do football-data).
    # Pedir ao utilizador os que faltarem (ver output "equipas sem match").

    # Premier League
    "Newcastle United": "Newcastle",
    "Tottenham": "Tottenham",
    "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest": "Nott'm Forest",
    "Brighton": "Brighton",
    "Leicester": "Leicester",
    "Sheffield United": "Sheffield United",
    "Bournemouth": "Bournemouth",

    # La Liga
    "Real Betis": "Betis",
    "Athletic Club": "Ath Bilbao",
    "Atletico Madrid": "Ath Madrid",
    "Real Sociedad": "Sociedad",
    "Celta Vigo": "Celta",
    "Deportivo Alaves": "Alaves",

    # Serie A
    "AC Milan": "Milan",
    "AS Roma": "Roma",
    "Hellas Verona": "Verona",

    # Bundesliga
    "RasenBallsport Leipzig": "RB Leipzig",
    "Bayer Leverkusen": "Leverkusen",
    "Borussia Dortmund": "Dortmund",
    "Borussia M.Gladbach": "M'gladbach",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "SC Freiburg": "Freiburg",
    "VfL Wolfsburg": "Wolfsburg",
    "FC Koeln": "FC Koln",

    # Ligue 1
    "Paris Saint Germain": "Paris SG",
    "Olympique Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon",
    "Stade Rennais": "Rennes",
    "AS Saint-Etienne": "St Etienne",
    "Clermont Foot": "Clermont",
    "RC Lens": "Lens",
}


def _canoniza(nome_understat: str) -> str:
    return _CACHE_ALIAS_UNDERSTAT.get(nome_understat, nome_understat)


def _get_equipa(s, nome_canonico: str, liga: Liga) -> Equipa | None:
    """Procura por nome canónico ou por alias."""
    equipa = s.scalar(
        select(Equipa).where(Equipa.nome == nome_canonico, Equipa.liga_id == liga.id)
    )
    if equipa is not None:
        return equipa
    # Via alias
    alias_row = s.scalar(
        select(_aliases.ALIASES.__class__)  # placeholder — vamos usar tabela EquipaAlias
    ) if False else None  # noqa: SIM300
    return None


def sincronizar(
    epocas: list[int] | None = None,
    modo: str | None = None,
) -> ResultadoIngestaoUnderstat:
    """Puxa xG para cada liga suportada em cada época, actualiza EstatisticasJogo."""
    modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
    epocas = epocas or [datetime.utcnow().year - 1, datetime.utcnow().year]
    resultado = ResultadoIngestaoUnderstat()

    with abrir_sessao() as s:
        for nome_liga, slug in LIGAS_UNDERSTAT.items():
            liga = s.scalar(select(Liga).where(Liga.nome == nome_liga))
            if liga is None:
                resultado.erros.append(f"Liga '{nome_liga}' não existe na BD.")
                continue
            for ano in epocas:
                try:
                    html = _obter_html(slug, ano, modo)
                    matches = _parse_matches_json(html)
                except Exception as exc:  # noqa: BLE001
                    resultado.erros.append(f"{nome_liga} {ano}: {exc}")
                    continue

                for match in matches:
                    _processar_match(s, match, liga, resultado)

    log.info(
        "Understat: %d jogos atualizados com xG, %d sem match, %d equipas sem match",
        resultado.jogos_atualizados, resultado.jogos_sem_match,
        len(resultado.equipas_sem_match),
    )
    return resultado


def _processar_match(s, match: dict, liga: Liga, resultado: ResultadoIngestaoUnderstat) -> None:
    try:
        nome_casa = _canoniza(match["h"]["title"])
        nome_fora = _canoniza(match["a"]["title"])
        xg_casa = float(match["xG"]["h"])
        xg_fora = float(match["xG"]["a"])
        data_str = match["datetime"]
    except (KeyError, TypeError, ValueError) as exc:
        resultado.erros.append(f"Match com campos inválidos: {exc}")
        return

    data = _parse_data_understat(data_str)
    if data is None:
        return

    casa = s.scalar(
        select(Equipa).where(Equipa.nome == nome_casa, Equipa.liga_id == liga.id)
    )
    fora = s.scalar(
        select(Equipa).where(Equipa.nome == nome_fora, Equipa.liga_id == liga.id)
    )
    if casa is None:
        resultado.equipas_sem_match.add(nome_casa)
        return
    if fora is None:
        resultado.equipas_sem_match.add(nome_fora)
        return

    # Procurar o jogo correspondente (±36h)
    from datetime import timedelta
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
        resultado.jogos_sem_match += 1
        return

    _upsert_xg(s, jogo, casa.id, xg_casa, xg_fora)
    _upsert_xg(s, jogo, fora.id, xg_fora, xg_casa)
    resultado.jogos_atualizados += 1


def _upsert_xg(s, jogo: Jogo, equipa_id: int, xg: float, xga: float) -> None:
    existente = s.scalar(
        select(EstatisticasJogo).where(
            EstatisticasJogo.jogo_id == jogo.id,
            EstatisticasJogo.equipa_id == equipa_id,
        )
    )
    if existente is None:
        s.add(EstatisticasJogo(jogo_id=jogo.id, equipa_id=equipa_id, xg=xg, xga=xga))
    else:
        existente.xg = xg
        existente.xga = xga


def _parse_data_understat(s: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
