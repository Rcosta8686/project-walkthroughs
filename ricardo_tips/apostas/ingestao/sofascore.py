"""Scraping não-oficial do Sofascore para xG, lineups e stats detalhados.

AVISO: Este módulo faz scraping dos endpoints internos do Sofascore
(`api.sofascore.com/api/v1/...`). Isto:
  - Viola os Termos de Serviço do Sofascore.
  - Pode resultar em IP ban, especialmente com volume alto.
  - A estrutura pode mudar sem aviso; o módulo partirá quando mudar.

Usa `curl_cffi` para impersonar Chrome real (bypassa Cloudflare JA3
fingerprinting). Mantém rate limit prudente (~1 req/s) e cache em
disco para minimizar pedidos repetidos.

Mapeamento jogo-nosso → event_id Sofascore é por nome de equipa
+ data. Não há garantia 100% de match.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from curl_cffi import requests as cffi_requests
    _CURL_CFFI_DISPONIVEL = True
except ImportError:
    cffi_requests = None
    _CURL_CFFI_DISPONIVEL = False

try:
    import cloudscraper
    _CLOUDSCRAPER_DISPONIVEL = True
except ImportError:
    cloudscraper = None
    _CLOUDSCRAPER_DISPONIVEL = False

from apostas.utils.config import project_root
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE = "https://api.sofascore.com/api/v1"
_DELAY_ENTRE_PEDIDOS = 1.5  # segundos — mais prudente
_ultimo_pedido_ts: float = 0.0
_SESSAO: Any = None  # curl_cffi Session persistente

# Headers que o browser real envia; Cloudflare valida muitos deles.
_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _obter_sessao():
    """Lazy-init session com warm-up Cloudflare.

    Tenta curl_cffi (Chrome 131 impersonation) primeiro. Se não existir,
    cai para cloudscraper. Em qualquer caso, visita a homepage primeiro
    para apanhar cookies cf_clearance.
    """
    global _SESSAO
    if _SESSAO is not None:
        return _SESSAO

    if _CURL_CFFI_DISPONIVEL:
        _SESSAO = cffi_requests.Session(impersonate="chrome131")
        tipo = "curl_cffi+chrome131"
    elif _CLOUDSCRAPER_DISPONIVEL:
        _SESSAO = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
        )
        tipo = "cloudscraper"
    else:
        raise RuntimeError(
            "Nem curl_cffi nem cloudscraper instalados. "
            "Corre: pip install curl-cffi>=0.7 cloudscraper>=1.2"
        )

    log.info("Sofascore: warm-up na homepage (via %s)", tipo)
    try:
        resp = _SESSAO.get("https://www.sofascore.com/", timeout=20)
        log.info(
            "Sofascore warm-up: HTTP %d, cookies apanhados: %d",
            resp.status_code, len(_SESSAO.cookies),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore warm-up falhou: %s", exc)
    return _SESSAO


def _rate_limit() -> None:
    """Garante que não excedemos 1 pedido por segundo."""
    global _ultimo_pedido_ts
    agora = time.monotonic()
    espera = _DELAY_ENTRE_PEDIDOS - (agora - _ultimo_pedido_ts)
    if espera > 0:
        time.sleep(espera)
    _ultimo_pedido_ts = time.monotonic()


def _cache_dir() -> Path:
    d = project_root() / "dados" / "raw" / "sofascore"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_json(path: str, cache_name: str | None = None) -> dict | None:
    """GET com rate limit, cache opcional e session com warm-up Cloudflare."""
    cache_file = _cache_dir() / f"{cache_name}.json" if cache_name else None
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    sessao = _obter_sessao()
    _rate_limit()
    url = f"{_BASE}{path}"
    log.debug("Sofascore GET %s", url)
    try:
        resp = sessao.get(url, headers=_HEADERS, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore GET %s falhou: %s", url, exc)
        return None

    if resp.status_code == 403:
        log.warning(
            "Sofascore 403 em %s. Cloudflare bloqueou mesmo com warm-up. "
            "Pode ser necessario cloudscraper ou proxies.", url,
        )
        return None
    if resp.status_code == 404:
        log.debug("Sofascore 404 em %s (recurso não existe)", url)
        return None
    if not resp.ok:
        log.warning("Sofascore %d em %s", resp.status_code, url)
        return None

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Sofascore resposta nao-JSON em %s: %s", url, exc)
        return None
    if cache_file:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


# ───────────────────────────── Endpoints ──────────────────────────────


def listar_jogos_do_dia(data: datetime) -> list[dict]:
    """Devolve a lista de jogos de futebol para uma data (YYYY-MM-DD).

    Cada jogo tem {id, startTimestamp, homeTeam, awayTeam, tournament, ...}.
    """
    data_str = data.strftime("%Y-%m-%d")
    payload = _get_json(
        f"/sport/football/scheduled-events/{data_str}",
        cache_name=f"events_{data_str}",
    )
    if payload is None:
        return []
    return payload.get("events", [])


def obter_estatisticas(event_id: int) -> dict | None:
    """Estatísticas detalhadas de um jogo (remates, xG, posse, etc.).

    Devolve a estrutura bruta do Sofascore ou None se não disponível.
    """
    return _get_json(
        f"/event/{event_id}/statistics",
        cache_name=f"stats_{event_id}",
    )


def obter_lineups(event_id: int) -> dict | None:
    """Formações iniciais (prováveis antes do jogo, finais depois)."""
    return _get_json(
        f"/event/{event_id}/lineups",
        cache_name=f"lineups_{event_id}",
    )


def obter_detalhes_jogo(event_id: int) -> dict | None:
    """Detalhes do jogo (resultado, data, equipas, árbitro, estádio)."""
    return _get_json(
        f"/event/{event_id}",
        cache_name=f"event_{event_id}",
    )


# ───────────────────────────── Extração ────────────────────────────────


@dataclass
class XGJogo:
    event_id: int
    xg_casa: float | None
    xg_fora: float | None


def extrair_xg(stats_payload: dict) -> XGJogo | None:
    """Extrai xG das estatísticas do Sofascore.

    O payload típico tem um array `statistics` com grupos ("Expected goals",
    "Possession", etc.). xG aparece como string tipo "1.42" em `home`/`away`.
    """
    if stats_payload is None:
        return None
    grupos = stats_payload.get("statistics", [])
    for periodo in grupos:
        if periodo.get("period") != "ALL":
            continue
        for grupo in periodo.get("groups", []):
            for item in grupo.get("statisticsItems", []):
                nome = (item.get("name") or "").lower()
                if "expected" in nome and "goals" in nome:
                    try:
                        xg_h = float(item.get("home", "0") or 0)
                        xg_a = float(item.get("away", "0") or 0)
                        return XGJogo(
                            event_id=stats_payload.get("event", {}).get("id", 0),
                            xg_casa=xg_h, xg_fora=xg_a,
                        )
                    except (ValueError, TypeError):
                        return None
    return None
