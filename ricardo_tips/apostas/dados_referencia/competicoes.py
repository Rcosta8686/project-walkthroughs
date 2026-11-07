"""So jogos oficiais de clubes: o que os jogadores fazem no seu clube.

Regra do Ricardo: campeonato, tacas, Liga dos Campeoes, Liga Europa e
Conference. Amigaveis (de clubes ou de seleccoes) e jogos de seleccoes ficam
de fora de TUDO: nao se analisam, nao dao alertas e nem se guardam (estragavam
o "normal" das equipas: rotacoes, pouca intensidade, quase sem cartoes).
Tambem ficam de fora as equipas de formacao (sub-19, sub-21, youth league...).

Uso: motivo_exclusao(ev) com o evento do Sofascore (usa a marca "national" das
equipas e os nomes da competicao) ou motivo_exclusao(liga="...") so com o nome.
"""
from __future__ import annotations

import re
import unicodedata

_AMIGAVEIS = ("friendly", "friendlies", "amigavel", "amigaveis", "amistoso", "amistosos",
              "freundschaft", "club friendly")
# Com limites de palavra: "Euro" (seleccoes) nao pode apanhar "Europa League".
_RE_SELECCOES = re.compile(
    r"world cup|world championship|nations league|european championship|\beuro\b"
    r"|copa america|africa cup of nations|african nations championship|asian cup|gold cup"
    r"|olympic|concacaf nations|arab cup|gulf cup|international friendly|maurice revello"
    r"|toulon tournament|shebelieves")
_RE_FORMACAO = re.compile(
    r"\bu\s?-?(1[5-9]|2[0-3])\b|\bsub\s?-?(1[5-9]|2[0-3])\b|under[ -]?(1[5-9]|2[0-3])\b"
    r"|youth|primavera|juniores|juvenil|premier league 2|reserve")


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def motivo_por_nome(nome: str | None) -> str | None:
    n = _norm(nome)
    if not n:
        return None
    if any(p in n for p in _AMIGAVEIS):
        return "amigavel"
    if _RE_SELECCOES.search(n):
        return "selecao"
    if _RE_FORMACAO.search(n):
        return "formacao"
    return None


def motivo_exclusao(ev: dict | None = None, *, liga: str | None = None) -> str | None:
    """None se e um jogo oficial de clubes; senao 'amigavel', 'selecao' ou 'formacao'."""
    if ev:
        for lado in ("homeTeam", "awayTeam"):
            if (ev.get(lado) or {}).get("national"):
                return "selecao"
        t = ev.get("tournament") or {}
        ut = t.get("uniqueTournament") or {}
        for nome in (t.get("name"), ut.get("name")):
            m = motivo_por_nome(nome)
            if m:
                return m
    return motivo_por_nome(liga)


def e_oficial_de_clubes(ev: dict | None = None, *, liga: str | None = None) -> bool:
    return motivo_exclusao(ev, liga=liga) is None
