"""Scraping do FBref (sports-reference.com) para xG histórico.

FBref é tolerante a scraping (desde que respeites 1 req/3s) e tem xG
histórico desde 2017/18 para as top-5 ligas europeias + Portugal.

Formato das URLs:
    https://fbref.com/en/comps/{liga_id}/{temporada}/schedule/

Liga IDs FBref (confirmados):
    9  = Premier League (Inglaterra)
    12 = La Liga (Espanha)
    11 = Serie A (Itália)
    20 = Bundesliga (Alemanha)
    13 = Ligue 1 (França)
    32 = Primeira Liga (Portugal)

Temporadas no formato "2024-2025".

AVISO: scraping é tolerado mas respeita o rate limit (~1 req/3s) para
não bloquear o IP. Usa cache em disco.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pandas as pd
import requests

from apostas.utils.config import project_root
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE = "https://fbref.com"
_DELAY = 3.0  # FBref ToS pede 1 req/3s
_ultimo_ts: float = 0.0

LIGAS_FBREF = {
    "Premier League": (9, "Premier-League"),
    "La Liga": (12, "La-Liga"),
    "Serie A": (11, "Serie-A"),
    "Bundesliga": (20, "Bundesliga"),
    "Ligue 1": (13, "Ligue-1"),
    "Liga Portugal": (32, "Primeira-Liga"),
}


def _rate_limit():
    global _ultimo_ts
    agora = time.monotonic()
    espera = _DELAY - (agora - _ultimo_ts)
    if espera > 0:
        time.sleep(espera)
    _ultimo_ts = time.monotonic()


def _cache_dir() -> Path:
    d = project_root() / "dados" / "raw" / "fbref"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _url_schedule(liga_nome: str, temporada: str) -> str:
    """temporada formato '2024-2025'."""
    liga_id, slug = LIGAS_FBREF[liga_nome]
    return (
        f"{_BASE}/en/comps/{liga_id}/{temporada}/schedule/"
        f"{temporada}-{slug}-Scores-and-Fixtures"
    )


def obter_schedule(liga_nome: str, ano_inicio: int) -> pd.DataFrame | None:
    """Devolve DataFrame com Date, Home, Score, Away, xG, xG.1, etc.

    `ano_inicio` é o ano em que a temporada começa (ex: 2024 = 2024/25).
    """
    if liga_nome not in LIGAS_FBREF:
        raise ValueError(f"Liga '{liga_nome}' não suportada. Usa uma de: {list(LIGAS_FBREF)}")
    temporada = f"{ano_inicio}-{ano_inicio + 1}"
    cache = _cache_dir() / f"{liga_nome.replace(' ', '_')}_{temporada}.html"
    html: str | None = None
    if cache.exists():
        html = cache.read_text(encoding="utf-8")
    else:
        _rate_limit()
        url = _url_schedule(liga_nome, temporada)
        log.info("FBref GET %s", url)
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 Ricardo Tips (educational use)"},
            timeout=30,
        )
        if not resp.ok:
            log.warning("FBref HTTP %d em %s", resp.status_code, url)
            return None
        html = resp.text
        cache.write_text(html, encoding="utf-8")

    # pandas lê a tabela directamente do HTML
    try:
        tabelas = pd.read_html(io.StringIO(html))
    except ValueError as exc:
        log.warning("FBref: sem tabelas em %s/%s: %s", liga_nome, temporada, exc)
        return None
    # A tabela que queremos tem a coluna "xG" (expected goals do home)
    for df in tabelas:
        cols = [str(c) for c in df.columns]
        if "xG" in cols and "Home" in cols and "Away" in cols:
            return df
    log.warning("FBref: tabela xG não encontrada em %s/%s", liga_nome, temporada)
    return None


def extrair_xg_por_jogo(df: pd.DataFrame) -> list[dict]:
    """Normaliza o DataFrame FBref em lista de dicts.

    Cada item: {date, home, away, xg_home, xg_away, score_home, score_away}.
    """
    out = []
    for _, row in df.iterrows():
        home = row.get("Home", "")
        away = row.get("Away", "")
        if not home or not away or pd.isna(home) or pd.isna(away):
            continue
        try:
            xg_h = float(row.get("xG", "nan"))
        except (ValueError, TypeError):
            xg_h = None
        try:
            xg_a = float(row.get("xG.1", "nan"))
        except (ValueError, TypeError):
            xg_a = None
        score = str(row.get("Score", "") or "")
        gh = ga = None
        if "–" in score or "-" in score:
            sep = "–" if "–" in score else "-"
            partes = score.split(sep)
            try:
                gh = int(partes[0].strip())
                ga = int(partes[1].strip())
            except (ValueError, IndexError):
                pass
        out.append({
            "date": row.get("Date", ""),
            "home": str(home).strip(),
            "away": str(away).strip(),
            "xg_home": xg_h,
            "xg_away": xg_a,
            "golos_home": gh,
            "golos_away": ga,
        })
    return out
