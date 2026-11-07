"""Mercados que a casa do Ricardo oferece, por liga.

Remates e remates a baliza (totais do jogo) so existem nas ligas principais:
nas segundas divisoes (2. Bundesliga, Championship, Serie B...) e nas tacas a
casa nao tem esse mercado, e um alerta que nao se pode apostar e so ruido.
Cantos, cartoes, golos e BTTS existem em todo o lado e nao sao filtrados.

Para acrescentar uma liga onde a casa passe a ter remates: juntar o nome
(como aparece nos alertas, sem acentos e em minusculas) a LIGAS_COM_REMATES.
"""
from __future__ import annotations

import re
import unicodedata

MERCADOS_SO_LIGAS_PRINCIPAIS = frozenset({"remates", "remates_a_baliza", "sot"})

LIGAS_COM_REMATES = frozenset({
    "premier league",
    "laliga", "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "liga portugal", "liga portugal betclic", "primeira liga",
    "eredivisie",
    "uefa champions league",
    "uefa europa league",
    "uefa conference league", "uefa europa conference league",
})


def _norm(liga: str) -> str:
    s = unicodedata.normalize("NFKD", liga or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)  # "Serie A (ITA)" -> "serie a"
    return re.sub(r"\s+", " ", s).strip()


def mercado_disponivel(mercado: str, liga: str) -> bool:
    """False quando a casa nao oferece este mercado nesta liga."""
    if (mercado or "").strip().lower() not in MERCADOS_SO_LIGAS_PRINCIPAIS:
        return True
    return _norm(liga) in LIGAS_COM_REMATES
