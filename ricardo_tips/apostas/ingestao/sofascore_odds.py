"""Odds pre-match do Sofascore (gratis via flaresolverr).

Usa o endpoint /event/{id}/odds/1/all que o site chama ao abrir uma
pagina de jogo. Devolve odds de varias casas para 1X2, Over/Under e BTTS.

Estrutura tipica da resposta:
  {
    "markets": [
      {
        "marketName": "Full time",
        "choices": [
          {"name": "1", "fractionalValue": "21/10", "sourceId": 123, "winning": null},
          {"name": "X", "fractionalValue": "11/5", ...},
          {"name": "2", "fractionalValue": "6/5", ...}
        ]
      },
      {"marketName": "Total", "choices": [{"name": "Over 2.5", ...}, {"name": "Under 2.5", ...}]},
      {"marketName": "Both teams to score", "choices": [{"name": "Yes", ...}, {"name": "No", ...}]}
    ]
  }
"""
from __future__ import annotations

from fractions import Fraction

from apostas.ingestao import _flaresolverr
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE = "https://www.sofascore.com/api/v1"
_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}
_WARMUP = "https://www.sofascore.com/"


def _fractional_to_decimal(frac_str: str) -> float | None:
    """Converte "21/10" para 3.10. Devolve None se invalido."""
    if not frac_str:
        return None
    try:
        f = Fraction(frac_str)
    except (ValueError, ZeroDivisionError):
        return None
    return float(f) + 1.0  # Sofascore fractional e so o profit, decimal = profit + 1


def obter_odds_prematch(event_id: int) -> dict:
    """Puxa odds pre-match do Sofascore. Devolve dict com:

      {
        "1x2": {"casa": 2.10, "empate": 3.20, "fora": 3.40},
        "golos_2.5": {"over": 1.85, "under": 1.95},
        "golos_1.5": {"over": 1.30, "under": 3.50},
        "btts": {"sim": 1.75, "nao": 2.05}
      }
    Chaves ausentes = mercado nao disponivel. Odds em decimal.
    """
    url = f"{_BASE}/event/{event_id}/odds/1/all"
    try:
        resp = _flaresolverr.get(url, headers=_HEADERS, warmup_url=_WARMUP)
    except Exception as exc:  # noqa: BLE001
        log.debug("odds prematch %d falhou: %s", event_id, exc)
        return {}

    if resp.status != 200:
        return {}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}

    resultado: dict[str, dict[str, float]] = {}
    for market in data.get("markets") or []:
        nome = (market.get("marketName") or "").lower()
        choices = market.get("choices") or []
        if "full time" in nome or "match winner" in nome or "1x2" in nome:
            _parse_1x2(choices, resultado)
        elif "total" in nome and "goals" in nome or nome == "total":
            _parse_total(choices, resultado)
        elif "both teams" in nome or "btts" in nome:
            _parse_btts(choices, resultado)
    return resultado


def _parse_1x2(choices: list[dict], out: dict) -> None:
    mapa = {"1": "casa", "x": "empate", "2": "fora"}
    r: dict[str, float] = {}
    for c in choices:
        n = (c.get("name") or "").lower().strip()
        lado = mapa.get(n)
        if not lado:
            continue
        odd = _fractional_to_decimal(c.get("fractionalValue"))
        if odd and odd > 1.0:
            r[lado] = odd
    if r:
        out["1x2"] = r


def _parse_total(choices: list[dict], out: dict) -> None:
    # Agrupa por linha: {2.5: {"over": X, "under": Y}, ...}
    por_linha: dict[str, dict[str, float]] = {}
    for c in choices:
        n = (c.get("name") or "").strip()
        odd = _fractional_to_decimal(c.get("fractionalValue"))
        if not odd:
            continue
        n_low = n.lower()
        if "over" in n_low:
            linha_str = n_low.replace("over", "").strip()
            por_linha.setdefault(linha_str, {})["over"] = odd
        elif "under" in n_low:
            linha_str = n_low.replace("under", "").strip()
            por_linha.setdefault(linha_str, {})["under"] = odd
    for linha, pares in por_linha.items():
        try:
            # Normaliza "2.5" ou "2,5" para float e volta a string
            l = float(linha.replace(",", "."))
            chave = f"golos_{l}"
            out[chave] = pares
        except ValueError:
            continue


def _parse_btts(choices: list[dict], out: dict) -> None:
    r: dict[str, float] = {}
    for c in choices:
        n = (c.get("name") or "").lower().strip()
        odd = _fractional_to_decimal(c.get("fractionalValue"))
        if not odd:
            continue
        if n == "yes":
            r["sim"] = odd
        elif n == "no":
            r["nao"] = odd
    if r:
        out["btts"] = r
