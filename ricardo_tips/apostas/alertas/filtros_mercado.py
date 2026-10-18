"""Filtros de mercado antes de alertar — M18 + M19 (melhorias parte 2).

M18: verificar linha
  - Margem do mercado > 8% → não enviar (referência pouco fiável)
  - Odd lida há > N segundos → não enviar (stale)
  - EV > 15% → ⚠ flag "verificar", nao "apostar"
  - Mercado com pouco volume → não usar (nao expomos liquidez na Odds
    API; aproximacao: numero de bookmakers que oferecem a linha)

M19: melhor preco entre casas suicas
  - Comparar odd entre as casas da Suica listadas
  - Escolher a MAIS ALTA (user ganha mais)
  - Mostrar diferenca para a pior no alerta

Casas suiças disponiveis em The Odds API (lista nao oficial, baseada em
pesquisa). Pode ser expandida.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from apostas.modelos import no_vig
from apostas.utils.logger import get_logger

log = get_logger(__name__)


# Casas de apostas disponiveis para residentes da Suiça.
# Keys usadas pela The Odds API (minusculo, sem espacos).
# Pinnacle fica fora da lista "soft" porque e a de referencia, nao onde
# o Ricardo aposta.
CASAS_SUICAS = frozenset({
    "betano",
    "bet365",
    "sportxbet",     # SportX
    "sportx_bet",
    "sportybet",
    "interwetten",
    "unibet",
    "williamhill",   # William Hill (opera na Suica via WH CH)
    "winamax",
    "marathonbet",
})

# Casa de referência (Pinnacle) para calcular margem e fair prob.
# Nao e para apostar — e para medir se a linha e fiavel.
CASA_REFERENCIA = "pinnacle"


# ─────────── Limites configuráveis ────────────────────────────────────────

MARGEM_MAX_FIAVEL = 0.08           # 8% — acima disto, referencia nao fiavel
EV_LIMIAR_VERIFICAR = 0.15         # 15% — EV acima disto → flag verificar
IDADE_ODD_MAX_SEG = 60             # 60s — odd mais velha que isto → stale
MIN_BOOKMAKERS = 2                 # exigir >=2 bookmakers com a linha


# ─────────── Modelo de resultado ─────────────────────────────────────────

@dataclass
class LinhaBookmaker:
    """Odd de 1 bookmaker para 1 outcome (ex: over 2.5)."""
    casa: str
    odd: float


@dataclass
class ResultadoFiltros:
    """Resultado agregado dos filtros."""
    passa: bool
    razao_bloqueio: str | None  # se nao passa
    melhor_casa: str | None
    melhor_odd: float | None
    pior_odd: float | None
    diferenca_pior: float | None  # delta cents em odds (0.07 = 7 cents)
    outras_casas: list[LinhaBookmaker]  # outras casas suicas (para linha "(bet365 1.85)")
    margem_mercado: float | None
    flag_verificar: bool  # True se EV > 15% ou outro red flag


# ─────────── Helpers ──────────────────────────────────────────────────────

def _odds_do_outcome(bookmakers: list[dict], market_key: str,
                     outcome_name: str, point: float | None) -> list[LinhaBookmaker]:
    """Extrai odds de todos os bookmakers para 1 outcome especifico."""
    out: list[LinhaBookmaker] = []
    for bm in bookmakers:
        casa = (bm.get("key") or "").lower()
        for mkt in bm.get("markets") or []:
            if mkt.get("key") != market_key:
                continue
            for o in mkt.get("outcomes") or []:
                nome = (o.get("name") or "").lower()
                if nome != outcome_name.lower():
                    continue
                if point is not None:
                    try:
                        if float(o.get("point", 0)) != point:
                            continue
                    except (TypeError, ValueError):
                        continue
                try:
                    odd = float(o.get("price", 0))
                except (TypeError, ValueError):
                    continue
                if odd <= 1.0:
                    continue
                out.append(LinhaBookmaker(casa=casa, odd=odd))
    return out


def _odds_referencia_1_outcome(bookmakers: list[dict], market_key: str,
                                outcomes_opostos: list[tuple[str, float | None]],
                                ) -> list[float]:
    """Odds da casa_referencia (Pinnacle) para OS DOIS lados do mercado
    (ex: over 2.5 E under 2.5). Serve para calcular margem.
    """
    for bm in bookmakers:
        casa = (bm.get("key") or "").lower()
        if casa != CASA_REFERENCIA:
            continue
        odds_lados = []
        for mkt in bm.get("markets") or []:
            if mkt.get("key") != market_key:
                continue
            for o in mkt.get("outcomes") or []:
                nome = (o.get("name") or "").lower()
                for nome_alvo, point_alvo in outcomes_opostos:
                    if nome != nome_alvo.lower():
                        continue
                    if point_alvo is not None:
                        try:
                            if float(o.get("point", 0)) != point_alvo:
                                continue
                        except (TypeError, ValueError):
                            continue
                    try:
                        odd = float(o.get("price", 0))
                        if odd > 1.0:
                            odds_lados.append(odd)
                    except (TypeError, ValueError):
                        pass
        return odds_lados
    return []


# ─────────── API principal ────────────────────────────────────────────────

def avaliar(
    bookmakers: list[dict],
    market_key: str,
    outcome_name: str,
    outcome_oposto: str,
    point: float | None,
    *,
    ev_bruto: float | None = None,
    timestamp_leitura: datetime | None = None,
    casas_suicas_overrride: Iterable[str] | None = None,
) -> ResultadoFiltros:
    """Avalia filtros M18+M19 para 1 outcome especifico de 1 jogo.

    Entrada:
        bookmakers: lista `odds_json["bookmakers"]` da Odds API
        market_key: "totals" | "btts" | "h2h"
        outcome_name: "over" | "yes" | "casa"...
        outcome_oposto: "under" | "no" | ... (para calcular margem)
        point: 2.5 (para totals) ou None (para btts/h2h)
        ev_bruto: EV calculado (prob_modelo × odd − 1), opcional
        timestamp_leitura: quando a odd foi lida (default: agora)
        casas_suicas_overrride: se dado, usa esta lista em vez de CASAS_SUICAS

    Devolve ResultadoFiltros com .passa, .melhor_casa, .melhor_odd, etc.
    """
    casas_alvo = set(casas_suicas_overrride) if casas_suicas_overrride else CASAS_SUICAS

    # 1) Odds nas casas suicas
    odds_suicas = _odds_do_outcome(bookmakers, market_key, outcome_name, point)
    odds_suicas = [l for l in odds_suicas if l.casa in casas_alvo]
    if not odds_suicas:
        return ResultadoFiltros(
            passa=False,
            razao_bloqueio="sem casas suicas com esta linha",
            melhor_casa=None, melhor_odd=None, pior_odd=None,
            diferenca_pior=None, outras_casas=[], margem_mercado=None,
            flag_verificar=False,
        )

    # 2) Filtro: pelo menos MIN_BOOKMAKERS
    if len(odds_suicas) < MIN_BOOKMAKERS:
        return ResultadoFiltros(
            passa=False,
            razao_bloqueio=f"so {len(odds_suicas)} casa(s) suica(s) oferecem a linha (min {MIN_BOOKMAKERS})",
            melhor_casa=odds_suicas[0].casa,
            melhor_odd=odds_suicas[0].odd,
            pior_odd=odds_suicas[0].odd,
            diferenca_pior=0.0,
            outras_casas=[],
            margem_mercado=None,
            flag_verificar=False,
        )

    # 3) Melhor + pior (M19)
    odds_sorted = sorted(odds_suicas, key=lambda l: l.odd, reverse=True)
    melhor = odds_sorted[0]
    pior = odds_sorted[-1]
    diferenca = round(melhor.odd - pior.odd, 2)
    outras = [l for l in odds_sorted if l.casa != melhor.casa]

    # 4) Margem via Pinnacle (M18)
    odds_ref = _odds_referencia_1_outcome(
        bookmakers, market_key,
        [(outcome_name, point), (outcome_oposto, point)],
    )
    margem_mercado = None
    if len(odds_ref) == 2:
        try:
            # no_vig.margem() devolve soma das implicitas (ex: 1.02).
            # A margem como percentagem e `soma - 1` (0.02 = 2%).
            margem_mercado = no_vig.margem(odds_ref) - 1.0
        except Exception:  # noqa: BLE001
            margem_mercado = None

    if margem_mercado is not None and margem_mercado > MARGEM_MAX_FIAVEL:
        return ResultadoFiltros(
            passa=False,
            razao_bloqueio=f"margem referencia {margem_mercado*100:.1f}% > {MARGEM_MAX_FIAVEL*100:.0f}% (linha pouco fiavel)",
            melhor_casa=melhor.casa, melhor_odd=melhor.odd,
            pior_odd=pior.odd, diferenca_pior=diferenca,
            outras_casas=outras, margem_mercado=margem_mercado,
            flag_verificar=False,
        )

    # 5) Odd stale (M18)
    if timestamp_leitura is not None:
        idade = datetime.utcnow() - timestamp_leitura
        if idade > timedelta(seconds=IDADE_ODD_MAX_SEG):
            return ResultadoFiltros(
                passa=False,
                razao_bloqueio=f"odd stale ({idade.total_seconds():.0f}s > {IDADE_ODD_MAX_SEG}s)",
                melhor_casa=melhor.casa, melhor_odd=melhor.odd,
                pior_odd=pior.odd, diferenca_pior=diferenca,
                outras_casas=outras, margem_mercado=margem_mercado,
                flag_verificar=False,
            )

    # 6) EV muito alto (M18) → nao bloqueia mas marca como "verificar"
    flag_verificar = False
    if ev_bruto is not None and ev_bruto > EV_LIMIAR_VERIFICAR:
        flag_verificar = True

    return ResultadoFiltros(
        passa=True,
        razao_bloqueio=None,
        melhor_casa=melhor.casa,
        melhor_odd=melhor.odd,
        pior_odd=pior.odd,
        diferenca_pior=diferenca,
        outras_casas=outras,
        margem_mercado=margem_mercado,
        flag_verificar=flag_verificar,
    )


def formatar_melhor_casa(res: ResultadoFiltros) -> str:
    """Produz string 'Melhor: Betano 1.92 · (bet365 1.85, pinnacle 1.80)'."""
    if not res.melhor_casa:
        return ""
    base = f"Melhor: {res.melhor_casa.capitalize()} {res.melhor_odd:.2f}"
    if res.outras_casas:
        outras_str = ", ".join(
            f"{l.casa} {l.odd:.2f}" for l in res.outras_casas[:3]
        )
        base += f" · ({outras_str})"
    return base
