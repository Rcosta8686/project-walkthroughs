"""Prazo para trabalhos longos (o incremento da noite tem 4 horas no Windows).

O incremento define o prazo; os ciclos longos (jogos das ligas, resolver,
estatisticas em falta) param quando ele acaba, com o que ja fizeram gravado,
em vez de o Windows matar o programa a meio. Sem prazo definido, nunca acaba.
"""
from __future__ import annotations

from datetime import datetime

_ATE: datetime | None = None


def definir(ate: datetime | None) -> None:
    global _ATE
    _ATE = ate


def esgotado(agora: datetime | None = None) -> bool:
    return _ATE is not None and (agora or datetime.now()) >= _ATE
