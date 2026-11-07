"""Odds reais ao vivo do Sofascore para a linha exacta de uma dica.

A pagina de cada jogo no Sofascore mostra odds ao vivo (/event/{id}/odds/1/all).
Daqui sai a odd real do mercado/linha da dica, para:
  - nao enviar dicas cuja odd real esta abaixo da odd justa (sem valor);
  - medir o lucro das dicas enviadas a odd real (nao a odd alvo).

O formato varia: a linha pode vir em "choiceGroup" ("9.5", com escolhas
"Over"/"Under") ou no nome da escolha ("Over 9.5"); as odds em fraccao
("4/5") ou decimal. So mercados do jogo inteiro (sem 1a/2a parte nem totais
de uma so equipa).
"""
from __future__ import annotations

import re
from fractions import Fraction

from apostas.utils.logger import get_logger

log = get_logger(__name__)

# Ordem importa: o mais especifico primeiro.
_MERCADOS = (
    ("shots on target", "remates_a_baliza"), ("on target", "remates_a_baliza"),
    ("corner", "cantos"), ("card", "cartoes"), ("booking", "cartoes"),
    ("shot", "remates"), ("both teams", "btts"), ("goals", "golos"), ("total", "golos"),
)
_EXCLUIR = ("1st half", "first half", "2nd half", "second half", "half time", "halftime",
            "home", "away", "team total", "player", "asian", "handicap", "minute", "race")
_RE_LINHA = re.compile(r"(\d+(?:[.,]\d+)?)")

Odds = dict[tuple[str, float | None], dict[str, float]]


def _decimal(choice: dict) -> float | None:
    for chave in ("decimalValue", "odds"):
        v = choice.get(chave)
        try:
            if v is not None and float(v) > 1.0:
                return round(float(v), 3)
        except (TypeError, ValueError):
            pass
    frac = choice.get("fractionalValue")
    if not frac:
        return None
    try:
        return round(float(Fraction(str(frac).strip())) + 1.0, 3)
    except (ValueError, ZeroDivisionError):
        return None


def _mercado(nome: str) -> str | None:
    n = nome.lower()
    if "both teams" not in n and any(x in n for x in _EXCLUIR):
        return None
    for chave, mercado in _MERCADOS:
        if chave in n:
            return mercado
    return None


def interpretar(data: dict) -> Odds:
    """Payload do Sofascore -> {(mercado, linha): {"over"/"under"/"sim"/"nao": odd}}."""
    out: Odds = {}
    for market in (data or {}).get("markets") or []:
        nome = str(market.get("marketName") or "")
        periodo = str(market.get("marketPeriod") or "").lower()
        if periodo and "full" not in periodo:
            continue
        mercado = _mercado(nome)
        if mercado is None:
            continue
        grupo = market.get("choiceGroup")
        for c in market.get("choices") or []:
            odd = _decimal(c)
            if odd is None:
                continue
            escolha = str(c.get("name") or "").strip().lower()
            if mercado == "btts":
                lado = {"yes": "sim", "no": "nao"}.get(escolha)
                if lado:
                    out.setdefault(("btts", None), {})[lado] = odd
                continue
            lado = "over" if escolha.startswith("over") else (
                "under" if escolha.startswith("under") else None)
            if lado is None:
                continue
            m = _RE_LINHA.search(escolha) or (_RE_LINHA.search(str(grupo)) if grupo else None)
            if m is None:
                continue
            linha = float(m.group(1).replace(",", "."))
            out.setdefault((mercado, linha), {})[lado] = odd
    return out


def procurar(odds: Odds, mercado: str, lado: str, linha) -> float | None:
    from apostas.alertas.corte_mercados import canonico
    m = canonico(mercado)
    lado = (lado or "").lower()
    if m == "btts":
        return odds.get(("btts", None), {}).get(lado)
    try:
        lf = float(linha)
    except (TypeError, ValueError):
        return None
    return odds.get((m, lf), {}).get(lado)


def obter(event_id: int) -> Odds:
    """Pede as odds ao vivo do jogo ao Sofascore (respeita o circuit breaker)."""
    from apostas.ingestao.sofascore_live import _get_json
    data = _get_json(f"/event/{int(event_id)}/odds/1/all")
    if not data:
        return {}
    odds = interpretar(data)
    nomes = sorted({str(m.get("marketName")) for m in data.get("markets") or []})
    log.info("Odds live %s: %d linhas (%s)", event_id, len(odds), ", ".join(nomes[:12]))
    return odds
