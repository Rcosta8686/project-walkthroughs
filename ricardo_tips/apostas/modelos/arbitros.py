"""Mini-cerebro dos arbitros, a partir dos nossos proprios jogos.

Cada jogo terminado guarda o arbitro (EstatisticaEquipaJogo.arbitro). Por
arbitro: media de amarelos por jogo (das 2 equipas), puxada para a media geral
com poucos jogos (K_ENCOLHIMENTO jogos ficticios). fator = media do arbitro /
media geral (1.25 = da 25% mais cartoes que o normal).

Substitui a lista fixa de referencias.py quando o arbitro ja tem MIN_JOGOS na
BD. Com `antes`, so jogos anteriores a essa data (para medir sem batota).
"""
from __future__ import annotations

import html
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime

K_ENCOLHIMENTO = 8
MIN_JOGOS = 5
MEDIA_DEFAULT = 4.3


@dataclass
class PerfilArbitro:
    nome: str
    n: int
    media: float       # amarelos por jogo, tal como sairam
    fator: float       # media encolhida / media geral

    def texto(self) -> str:
        return f"arbitro {self.nome}: {self.media:.1f} amarelos/jogo nos nossos {self.n} jogos"


def chave(nome: str | None) -> str:
    """Nome comparavel: o live nao descodifica '&amp;' e a BD sim; acentos e
    maiusculas tambem podem variar."""
    s = html.unescape(nome or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(s.split())


class HistoricoArbitros:
    def __init__(self, linhas):
        por_evento: dict[int, list] = {}
        for r in linhas:
            por_evento.setdefault(int(r.sofascore_event_id), []).append(r)
        self._jogos: dict[str, list[tuple[datetime, float]]] = {}
        self._nomes: dict[str, str] = {}   # chave -> nome como aparece
        totais = []
        for rows in por_evento.values():
            if len(rows) != 2 or any(r.cards_amarelos is None for r in rows):
                continue
            nome = next((r.arbitro for r in rows if r.arbitro), None)
            total = float(rows[0].cards_amarelos) + float(rows[1].cards_amarelos)
            totais.append(total)
            if nome and (k := chave(nome)):
                self._jogos.setdefault(k, []).append((rows[0].data_jogo, total))
                self._nomes.setdefault(k, html.unescape(nome).strip())
        self.media_geral = sum(totais) / len(totais) if len(totais) >= 30 else MEDIA_DEFAULT

    def perfil(self, nome: str | None, antes: datetime | None = None) -> PerfilArbitro | None:
        k = chave(nome)
        if not k:
            return None
        jogos = [t for d, t in self._jogos.get(k, []) if antes is None or d < antes]
        if len(jogos) < MIN_JOGOS:
            return None
        media = sum(jogos) / len(jogos)
        enc = (sum(jogos) + K_ENCOLHIMENTO * self.media_geral) / (len(jogos) + K_ENCOLHIMENTO)
        return PerfilArbitro(nome=self._nomes.get(k, k), n=len(jogos), media=media,
                             fator=round(enc / self.media_geral, 3))

    def ranking(self, min_jogos: int = 8) -> list[PerfilArbitro]:
        out = [p for n in self._jogos if (p := self.perfil(n)) is not None and p.n >= min_jogos]
        return sorted(out, key=lambda p: -p.fator)


_COLUNAS = ("sofascore_event_id", "data_jogo", "arbitro", "cards_amarelos")
_cache: tuple[float, HistoricoArbitros] | None = None
_TTL_S = 6 * 3600


def da_bd(sess, dias: int = 400) -> HistoricoArbitros:
    from datetime import timedelta

    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.tempo import agora_utc
    desde = agora_utc() - timedelta(days=dias)
    return HistoricoArbitros(sess.execute(
        select(*[getattr(EEJ, c) for c in _COLUNAS]).where(EEJ.data_jogo >= desde)).all())


def perfil_live(nome: str | None) -> PerfilArbitro | None:
    """Para o scanner: historico carregado 1x por 6h."""
    global _cache
    if not nome:
        return None
    agora = time.time()
    if _cache is None or agora - _cache[0] >= _TTL_S:
        from apostas.utils.db import abrir_sessao
        with abrir_sessao() as s:
            _cache = (agora, da_bd(s))
    return _cache[1].perfil(nome)


def formatar_ranking(h: HistoricoArbitros, n: int = 5) -> str:
    r = h.ranking()
    linhas = [f"*🧑‍⚖️ Arbitros* (dos nossos jogos; media geral {h.media_geral:.1f} amarelos/jogo)"]
    if not r:
        return "\n".join(linhas + ["  Ainda sem arbitros com 8+ jogos guardados."])
    linhas.append("  Mais cartoes: " + " · ".join(
        f"{p.nome} {p.media:.1f} ({p.n}j)" for p in r[:n]))
    linhas.append("  Menos cartoes: " + " · ".join(
        f"{p.nome} {p.media:.1f} ({p.n}j)" for p in r[-n:][::-1]))
    return "\n".join(linhas)
