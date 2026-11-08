"""Mini-cerebro dos arbitros, a partir dos nossos proprios jogos.

Cada jogo terminado guarda o arbitro (EstatisticaEquipaJogo.arbitro). O
arbitro compara-se com a LIGA onde apita: um arbitro holandes parece "brando"
contra a media geral so porque a Eredivisie da poucos cartoes, e o nivel da
liga ja esta no normal das equipas (contava 2 vezes). Por isso:

  fator = amarelos nos jogos dele / o que a liga de cada jogo costuma dar,

puxado para 1 com poucos jogos (K_ENCOLHIMENTO jogos ficticios). 1.25 = da 25%
mais cartoes do que a liga dele costuma dar.

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
K_LIGA = 10          # media da liga puxada para a geral com poucos jogos


@dataclass
class PerfilArbitro:
    nome: str
    n: int
    media: float       # amarelos por jogo, tal como sairam
    fator: float       # vs o que as ligas dos jogos dele costumam dar (encolhido)
    media_liga: float = 0.0

    def texto(self) -> str:
        txt = f"arbitro {self.nome}: {self.media:.1f} amarelos/jogo nos nossos {self.n} jogos"
        return txt + (f" (a liga costuma dar {self.media_liga:.1f})" if self.media_liga else "")


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
        self._nomes: dict[str, str] = {}   # chave -> nome como aparece
        jogos: list[tuple[str, datetime, float, str]] = []   # arbitro, data, total, liga
        por_liga: dict[str, list[float]] = {}
        totais = []
        for rows in por_evento.values():
            if len(rows) != 2 or any(r.cards_amarelos is None for r in rows):
                continue
            nome = next((r.arbitro for r in rows if r.arbitro), None)
            liga = chave(next((getattr(r, "liga", None) for r in rows
                               if getattr(r, "liga", None)), None))
            total = float(rows[0].cards_amarelos) + float(rows[1].cards_amarelos)
            totais.append(total)
            if liga:
                por_liga.setdefault(liga, []).append(total)
            if nome and (k := chave(nome)):
                jogos.append((k, rows[0].data_jogo, total, liga))
                self._nomes.setdefault(k, html.unescape(nome).strip())
        self.media_geral = sum(totais) / len(totais) if len(totais) >= 30 else MEDIA_DEFAULT
        self.media_liga = {lg: (sum(xs) + K_LIGA * self.media_geral) / (len(xs) + K_LIGA)
                           for lg, xs in por_liga.items()}
        # por arbitro: (data, amarelos, o que a liga desse jogo costuma dar)
        self._jogos: dict[str, list[tuple[datetime, float, float]]] = {}
        for k, d, total, liga in jogos:
            self._jogos.setdefault(k, []).append(
                (d, total, self.media_liga.get(liga, self.media_geral)))

    def perfil(self, nome: str | None, antes: datetime | None = None) -> PerfilArbitro | None:
        k = chave(nome)
        if not k:
            return None
        jogos = [(t, e) for d, t, e in self._jogos.get(k, []) if antes is None or d < antes]
        if len(jogos) < MIN_JOGOS:
            return None
        n = len(jogos)
        real, esperado = sum(t for t, _ in jogos), sum(e for _, e in jogos)
        e_medio = esperado / n
        fator = (real + K_ENCOLHIMENTO * e_medio) / (esperado + K_ENCOLHIMENTO * e_medio)
        return PerfilArbitro(nome=self._nomes.get(k, k), n=n, media=real / n,
                             fator=round(fator, 3), media_liga=round(e_medio, 2))

    def ranking(self, min_jogos: int = 8) -> list[PerfilArbitro]:
        out = [p for n in self._jogos if (p := self.perfil(n)) is not None and p.n >= min_jogos]
        return sorted(out, key=lambda p: -p.fator)


_COLUNAS = ("sofascore_event_id", "data_jogo", "arbitro", "cards_amarelos", "liga")
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


def fator_jogo(nome: str | None) -> tuple[float, str | None]:
    """(fator, texto) do arbitro deste jogo: dos nossos jogos (comparado com a
    liga) quando ja ha jogos dele; senao a lista fixa; texto so se fugir do normal."""
    if not nome:
        return 1.0, None
    try:
        p = perfil_live(nome)
    except Exception:  # noqa: BLE001 — sem BD: lista fixa
        p = None
    if p is not None:
        return p.fator, (p.texto() if abs(p.fator - 1) >= 0.05 else None)
    from apostas.modelos.referencias import fator_arbitro, rate_arbitro
    f = fator_arbitro(nome)
    if abs(f - 1) < 0.05:
        return 1.0, None
    return f, f"arbitro {nome} ({rate_arbitro(nome):.1f} cartoes/jogo, lista fixa)"


def formatar_ranking(h: HistoricoArbitros, n: int = 5) -> str:
    r = h.ranking()
    linhas = [f"*🧑‍⚖️ Arbitros* (dos nossos jogos, comparados com a liga onde apitam; "
              f"media geral {h.media_geral:.1f} amarelos/jogo)"]
    if not r:
        return "\n".join(linhas + ["  Ainda sem arbitros com 8+ jogos guardados."])

    def item(p: PerfilArbitro) -> str:
        return (f"{p.nome} {(p.fator - 1) * 100:+.0f}% ({p.media:.1f} vs liga "
                f"{p.media_liga:.1f}, {p.n}j)")
    mais = [p for p in r if p.fator > 1][:n]
    menos = [p for p in reversed(r) if p.fator < 1][:n]
    if mais:
        linhas.append("  Mais cartoes que a liga: " + " · ".join(item(p) for p in mais))
    if menos:
        linhas.append("  Menos cartoes que a liga: " + " · ".join(item(p) for p in menos))
    ligas = sorted(h.media_liga.items(), key=lambda x: -x[1])
    if len(ligas) >= 2:
        linhas.append(f"  Ligas: mais cartoes {ligas[0][0]} {ligas[0][1]:.1f} · menos "
                      f"{ligas[-1][0]} {ligas[-1][1]:.1f}")
    return "\n".join(linhas)
