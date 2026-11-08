"""Mini-cerebro de cada jogador: remates, remates a baliza e cartao.

O normal de cada jogador vem dos jogos dele na BD (EstatisticaJogadorJogo):
  - que parte dos remates (e dos remates a baliza) da equipa ele faz quando
    esta em campo;
  - amarelos e faltas por 90 minutos;
puxado para o normal da posicao dele quando tem poucos jogos.

Ao vivo: o que a equipa ainda vai fazer (a previsao por equipa, ja medida e
corrigida) x a parte desse jogador, ajustada pelo que ele ja fez neste jogo,
x a probabilidade de ele ainda ficar em campo. Nos cartoes, as faltas que ja
fez pesam.

Antes do jogo: o normal da equipa (mini-cerebros) x os minutos que se espera
que jogue (titular ou suplente) x a parte dele - serve a multipla de jogadores.

A noite compara o que se previa (PrevisaoJogadorLive) com o que cada jogador
fez e aprende um multiplicador por mercado e fase; os alertas so de jogador so
ligam quando essa medicao mostra que acertamos.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MERCADOS = ("remates", "sot", "cartao")
NOMES = {"remates": "remates", "sot": "a baliza", "cartao": "amarelo"}
N_JOGOS = 15
# Peso do normal do jogador (em "remates da equipa" ficticios) vs o deste jogo.
K_PARTE = {"remates": 8.0, "sot": 3.0}
# Peso do normal da posicao no normal do jogador (~3 jogos).
K_HIST = {"remates": 36.0, "sot": 12.0}
K_CARTAO_90 = 6.0
FALTA_PESO = 0.12          # cada falta neste jogo soma ao peso de cartao do jogador
MIN_POS = 50               # jogos por posicao para usar o normal da BD
# Normal por posicao quando ainda nao ha dados: parte dos remates / a baliza
# da equipa, amarelos e faltas por 90'.
PRIOR_POS = {"F": (0.22, 0.25, 0.15, 1.2), "M": (0.10, 0.09, 0.22, 1.4),
             "D": (0.04, 0.03, 0.22, 1.2), "G": (0.0, 0.0, 0.05, 0.1)}


def pos_letra(pos: str | None) -> str:
    p = (pos or "").upper()
    if p in ("GK", "G"):
        return "G"
    if p.startswith(("CB", "LB", "RB", "WB", "D")):
        return "D"
    if p.startswith(("CDM", "CM", "CAM", "M", "DM", "AM")):
        return "M"
    if p.startswith(("ST", "LW", "RW", "CF", "F", "W")):
        return "F"
    return "M"


def _n(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _poisson_ge(lam: float, k: int) -> float:
    """P(Poisson(lam) >= k)."""
    if k <= 0:
        return 1.0
    lam = max(lam, 0.0)
    acc, termo = 0.0, math.exp(-lam)
    for i in range(k):
        acc += termo
        termo *= lam / (i + 1)
    return max(0.0, min(1.0, 1.0 - acc))


def _amarelo(r) -> float:
    """Viu amarelo? (as estatisticas antigas nao o tinham: vem dos incidentes)."""
    if _n(getattr(r, "cards_amarelos", 0)) > 0 or getattr(r, "minuto_amarelo", None) is not None:
        return 1.0
    return 0.0


def _remates_jogador(r, tot) -> float:
    """Remates do jogador nesse jogo. Os jogos guardados antes de 08/11 nao os
    tem (o Sofascore nao da o total): estima-se pelos remates a baliza dele e
    pela pontaria da equipa nesse jogo."""
    if r.remates is not None:
        return _n(r.remates)
    sot, rem_eq, sot_eq = _n(r.sot), _n(tot[0]), _n(tot[1])
    return sot * rem_eq / sot_eq if sot_eq > 0 else sot / 0.35


@dataclass
class PerfilJogador:
    id: int
    nome: str
    pos: str
    n_jogos: int
    quota_remates: float
    quota_sot: float
    cartoes90: float
    faltas90: float


class HistoricoJogadores:
    """Jogos de cada jogador + totais da equipa nesses jogos (para a parte dele)."""

    def __init__(self, linhas_jogador, totais_equipa: dict[tuple[int, int], tuple],
                 prior: dict[str, tuple[float, float, float, float]] | None = None):
        self._jogos: dict[int, list] = {}
        somas: dict[str, list[float]] = {}
        for r in linhas_jogador:
            if not r.minutos or r.minutos <= 0 or r.data_jogo is None:
                continue
            self._jogos.setdefault(int(r.sofascore_player_id), []).append(r)
            tot = totais_equipa.get((int(r.sofascore_event_id), int(r.sofascore_team_id)))
            acc = somas.setdefault(pos_letra(r.posicao), [0.0] * 9)
            f90 = r.minutos / 90.0
            acc[8] += 1
            acc[4] += _amarelo(r)
            acc[5] += f90
            acc[6] += _n(r.fouls_committed)
            if tot is not None and tot[0]:
                acc[0] += _remates_jogador(r, tot)
                acc[1] += _n(tot[0]) * f90
            if tot is not None and tot[1]:
                acc[2] += _n(r.sot)
                acc[3] += _n(tot[1]) * f90
        for lista in self._jogos.values():
            lista.sort(key=lambda r: r.data_jogo, reverse=True)
        self._totais = totais_equipa
        # Normal de cada posicao: da BD com dados suficientes, senao o de partida.
        self.prior: dict[str, tuple[float, float, float, float]] = dict(prior or {})
        for pos, base in PRIOR_POS.items():
            if pos in self.prior:
                continue
            a = somas.get(pos)
            if a is None or a[8] < MIN_POS:
                self.prior[pos] = base
                continue
            self.prior[pos] = (a[0] / a[1] if a[1] else base[0], a[2] / a[3] if a[3] else base[1],
                               a[4] / a[5] if a[5] else base[2], a[6] / a[5] if a[5] else base[3])

    def perfil(self, pid: int, antes: datetime | None = None, n: int = N_JOGOS,
               pos_hint: str | None = None) -> PerfilJogador | None:
        jogos = [r for r in self._jogos.get(int(pid), []) if antes is None or r.data_jogo < antes][:n]
        if not jogos and not pos_hint:
            return None
        pos = pos_letra(pos_hint) if pos_hint else pos_letra(
            max({pos_letra(r.posicao) for r in jogos},
                key=lambda p: sum(1 for r in jogos if pos_letra(r.posicao) == p)))
        pr_r, pr_s, pr_c, pr_f = self.prior.get(pos, PRIOR_POS["M"])
        num_r = den_r = num_s = den_s = cartoes = faltas = n90 = 0.0
        for r in jogos:
            f90 = r.minutos / 90.0
            n90 += f90
            cartoes += _amarelo(r)
            faltas += _n(r.fouls_committed)
            tot = self._totais.get((int(r.sofascore_event_id), int(r.sofascore_team_id)))
            if tot is not None and tot[0]:
                num_r += _remates_jogador(r, tot)
                den_r += _n(tot[0]) * f90
            if tot is not None and tot[1]:
                num_s += _n(r.sot)
                den_s += _n(tot[1]) * f90
        kr, ks = K_HIST["remates"], K_HIST["sot"]
        return PerfilJogador(
            id=int(pid), nome=jogos[0].nome_jogador if jogos else "?", pos=pos,
            n_jogos=len(jogos),
            quota_remates=(num_r + kr * pr_r) / (den_r + kr),
            quota_sot=(num_s + ks * pr_s) / (den_s + ks),
            cartoes90=(cartoes + K_CARTAO_90 * pr_c) / (n90 + K_CARTAO_90),
            faltas90=(faltas + K_CARTAO_90 * pr_f) / (n90 + K_CARTAO_90))


def priors_da_bd(sess, dias: int = 365) -> dict[str, tuple[float, float, float, float]]:
    """Normal de cada posicao com todos os jogadores da BD, numa so consulta."""
    from sqlalchemy import and_, func, select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    from apostas.utils.tempo import agora_utc
    from sqlalchemy import case, or_
    f90 = EJJ.minutos / 90.0
    tem_rem = EJJ.remates.is_not(None)
    amarelo = case((or_(EJJ.cards_amarelos > 0, EJJ.minuto_amarelo.is_not(None)), 1), else_=0)
    q = (select(EJJ.posicao, func.count(), func.sum(case((tem_rem, EJJ.remates), else_=0)),
                func.sum(case((tem_rem, EEJ.remates * f90), else_=0)),
                func.sum(func.coalesce(EJJ.sot, 0)),
                func.sum(EEJ.sot * f90), func.sum(amarelo),
                func.sum(func.coalesce(EJJ.fouls_committed, 0)), func.sum(f90))
         .join(EEJ, and_(EEJ.sofascore_event_id == EJJ.sofascore_event_id,
                         EEJ.sofascore_team_id == EJJ.sofascore_team_id))
         .where(EJJ.minutos > 0, EJJ.data_jogo >= agora_utc() - timedelta(days=dias),
                EEJ.remates.is_not(None))
         .group_by(EJJ.posicao))
    somas: dict[str, list[float]] = {}
    for pos, n, rem, rem_eq, sot, sot_eq, cart, falt, n90 in sess.execute(q).all():
        a = somas.setdefault(pos_letra(pos), [0.0] * 7)
        for i, v in enumerate((n, rem, rem_eq or 0, sot, sot_eq or 0, cart, falt)):
            a[i] += float(v or 0)
        a.append(float(n90 or 0))
    out = {}
    for pos, a in somas.items():
        n90 = sum(a[7:])
        if a[0] < MIN_POS or not n90:
            continue
        base = PRIOR_POS.get(pos, PRIOR_POS["M"])
        # remates: jogos antigos nao os tem; sem dados suficientes, a parte dos
        # remates segue a parte dos remates a baliza (a pontaria e parecida)
        quota_sot = a[3] / a[4] if a[4] else base[1]
        quota_rem = a[1] / a[2] if a[2] > 200 else quota_sot
        out[pos] = (quota_rem, quota_sot, a[5] / n90, a[6] / n90)
    return out


def carregar_do_bd(sess, jogadores: list[int] | None = None, dias: int = 365,
                   prior: dict | None = None) -> HistoricoJogadores:
    """Historico (so destes jogadores, se dados) + totais das equipas nesses jogos.
    `prior`: normal das posicoes (priors_da_bd); sem ele sai dos carregados."""
    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    from apostas.utils.tempo import agora_utc
    desde = agora_utc() - timedelta(days=dias)
    cols = [EJJ.sofascore_player_id, EJJ.sofascore_event_id, EJJ.sofascore_team_id,
            EJJ.nome_jogador, EJJ.posicao, EJJ.data_jogo, EJJ.minutos, EJJ.remates, EJJ.sot,
            EJJ.cards_amarelos, EJJ.minuto_amarelo, EJJ.fouls_committed]
    q = select(*cols).where(EJJ.data_jogo >= desde)
    linhas = []
    if jogadores:
        ids = sorted(set(int(j) for j in jogadores))
        for i in range(0, len(ids), 900):
            linhas += sess.execute(q.where(EJJ.sofascore_player_id.in_(ids[i:i + 900]))).all()
    else:
        linhas = sess.execute(q).all()
    eventos = sorted({int(r.sofascore_event_id) for r in linhas})
    totais: dict[tuple[int, int], tuple] = {}
    for i in range(0, len(eventos), 900):
        for e in sess.execute(select(EEJ.sofascore_event_id, EEJ.sofascore_team_id, EEJ.remates,
                                     EEJ.sot, EEJ.cards_amarelos)
                              .where(EEJ.sofascore_event_id.in_(eventos[i:i + 900]))).all():
            totais[(int(e.sofascore_event_id), int(e.sofascore_team_id))] = (
                e.remates, e.sot, e.cards_amarelos)
    return HistoricoJogadores(linhas, totais, prior=prior)


def registar(sess, estado_event_id: int, minuto: int, prevs: list["PrevJogador"],
             equipas: dict[str, str]) -> int:
    """Guarda o que se previa (uma vez por jogo, fase, jogador e mercado).
    minuto 0 = antes do jogo."""
    from sqlalchemy import select

    from apostas.utils.schema import PrevisaoJogadorLive as PJL
    janela = 0 if minuto <= 0 else janela_de(minuto)
    ja = {(r.player_id, r.mercado) for r in sess.execute(
        select(PJL.player_id, PJL.mercado).where(PJL.event_id == int(estado_event_id),
                                                 PJL.janela == janela)).all()}
    n = 0
    for p in prevs:
        if (p.id, p.mercado) in ja:
            continue
        sess.add(PJL(event_id=int(estado_event_id), player_id=p.id, nome=p.nome[:100],
                     equipa=(equipas.get(p.lado) or "")[:100], pos=p.pos[:2], minuto=minuto,
                     janela=janela, mercado=p.mercado, atual=p.atual, lam=round(p.lam, 4)))
        ja.add((p.id, p.mercado))
        n += 1
    return n


# ─── Previsao ──────────────────────────────────────────────────────────────

@dataclass
class PrevJogador:
    id: int
    nome: str
    lado: str
    pos: str
    mercado: str
    atual: int
    lam: float           # o que ainda vai fazer ate ao fim (media)
    faltas: int = 0

    def p_mais(self, k: int = 1) -> float:
        return _poisson_ge(self.lam, k)

    def p_total(self, alvo: int) -> float:
        """Probabilidade de acabar com pelo menos `alvo` (ex: 2+ a baliza)."""
        return _poisson_ge(self.lam, alvo - self.atual)


def fracao_fica(j: dict, minuto: int) -> float:
    """Parte do resto do jogo que se espera que o jogador ainda jogue
    (avancados e medios titulares saem muitas vezes a partir dos 60')."""
    if not j.get("em_campo", True):
        return 0.0
    pos = j.get("pos") or "M"
    if not j.get("titular", True) or pos in ("G",) or minuto >= 80:
        return 1.0
    if pos == "D":
        return 0.95
    return 0.75 if minuto < 60 else 0.85


def _restante_equipa(estado, prev, precisao, mercado_eq: str, lado: str) -> float:
    from apostas.modelos import precisao_equipas as pe
    pf = pe.prever_final(estado, prev, precisao, mercado_eq) if precisao is not None else None
    atual = _n(getattr(estado, pe.ACTUAL[mercado_eq][0 if lado == "casa" else 1], 0))
    if pf is None:
        rc, rf = pe.restantes(prev, estado, mercado_eq)
        return rc if lado == "casa" else rf
    return max(0.0, pf[lado][0] - atual)


def prever_live(estado, prev, perfis: dict[int, PerfilJogador | None], historico: HistoricoJogadores,
                precisao=None, medicao: "Medicao | None" = None) -> list[PrevJogador]:
    """Remates, a baliza e cartao ate ao fim para cada jogador em campo."""
    minuto = int(getattr(estado, "minuto", 0) or 0)
    out: list[PrevJogador] = []
    for lado, eq_rem, eq_sot in (("casa", "shots_total_casa", "shots_on_target_casa"),
                                 ("fora", "shots_total_fora", "shots_on_target_fora")):
        em_campo = [j for j in getattr(estado, "jogadores", []) or []
                    if j.get("lado") == lado and j.get("em_campo", True)]
        if not em_campo:
            continue
        resto = {m: _restante_equipa(estado, prev, precisao, me, lado)
                 for m, me in (("remates", "remates"), ("sot", "remates_a_baliza"),
                               ("cartao", "cartoes"))}
        feitos = {"remates": _n(getattr(estado, eq_rem, 0)), "sot": _n(getattr(estado, eq_sot, 0))}
        pesos_cartao: dict[int, float] = {}
        perfis_lado: dict[int, PerfilJogador] = {}
        for j in em_campo:
            pf = perfis.get(j["id"]) or historico.perfil(j["id"], pos_hint=j.get("pos") or "M")
            if pf is None:
                continue
            perfis_lado[j["id"]] = pf
            w = pf.cartoes90 + FALTA_PESO * j.get("faltas", 0)
            pesos_cartao[j["id"]] = w * (0.5 if j.get("amarelo") else 1.0)
        soma_cartao = sum(pesos_cartao.values()) or 1.0
        for j in em_campo:
            pf = perfis_lado.get(j["id"])
            if pf is None:
                continue
            fica = fracao_fica(j, minuto)
            pos = j.get("pos") or pf.pos
            for m in ("remates", "sot"):
                quota_pf = pf.quota_remates if m == "remates" else pf.quota_sot
                k = K_PARTE[m]
                quota = (quota_pf * k + j.get(m, 0)) / (k + feitos[m])   # normal + este jogo
                lam = resto[m] * quota * fica
                if medicao is not None:
                    lam *= medicao.mult(m, minuto)
                out.append(PrevJogador(j["id"], j["nome"], lado, pos, m, int(j.get(m, 0)),
                                       lam, j.get("faltas", 0)))
            if not j.get("amarelo"):
                lam = resto["cartao"] * pesos_cartao[j["id"]] / soma_cartao * fica
                if medicao is not None:
                    lam *= medicao.mult("cartao", minuto)
                out.append(PrevJogador(j["id"], j["nome"], lado, pos, "cartao", 0, lam,
                                       j.get("faltas", 0)))
    return out


def prever_pre_jogo(lineup: list[dict], perfis: dict[int, PerfilJogador | None],
                    historico: HistoricoJogadores, remates_equipa: tuple[float, float],
                    sot_equipa: tuple[float, float], fator_arbitro: float = 1.0) -> list[PrevJogador]:
    """Antes do jogo: o normal de cada equipa x minutos esperados x a parte do
    jogador. `lineup`: jogadores (titular/suplente) de jogadores_das_lineups."""
    out: list[PrevJogador] = []
    for j in lineup:
        if not j.get("titular"):
            continue  # so os titulares: os suplentes sao incertos demais
        pf = perfis.get(j["id"]) or historico.perfil(j["id"], pos_hint=j.get("pos") or "M")
        if pf is None:
            continue
        i = 0 if j["lado"] == "casa" else 1
        pos = j.get("pos") or pf.pos
        minutos = 88.0 if pos in ("G", "D") else 75.0
        f90 = minutos / 90.0
        out.append(PrevJogador(j["id"], j["nome"], j["lado"], pos, "remates", 0,
                               remates_equipa[i] * pf.quota_remates * f90))
        out.append(PrevJogador(j["id"], j["nome"], j["lado"], pos, "sot", 0,
                               sot_equipa[i] * pf.quota_sot * f90))
        out.append(PrevJogador(j["id"], j["nome"], j["lado"], pos, "cartao", 0,
                               pf.cartoes90 * f90 * fator_arbitro))
    return out


def texto_live(prevs: list[PrevJogador], mercado: str, n: int = 3) -> str:
    """'👤 Ate ao fim (a baliza): Taremi (1) +1 71% · Galeno 55% · Pepe 41%'."""
    sel = sorted((p for p in prevs if p.mercado == mercado), key=lambda p: -p.p_mais(1))[:n]
    sel = [p for p in sel if p.p_mais(1) >= 0.2]
    if not sel:
        return ""

    def item(p: PrevJogador) -> str:
        if mercado == "cartao":
            extra = f" ({p.faltas} faltas)" if p.faltas else ""
            return f"{p.nome} {p.p_mais(1) * 100:.0f}%{extra}"
        tem = f" ({p.atual})" if p.atual else ""
        return f"{p.nome}{tem} +1 {p.p_mais(1) * 100:.0f}%"
    return f"👤 Ate ao fim ({NOMES[mercado]}): " + " · ".join(item(p) for p in sel)


# ─── Medicao nocturna e aprendizagem ───────────────────────────────────────

FAIXAS = ((0.2, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 0.85), (0.85, 1.01))
MIN_CONFIAVEL = 300
JANELAS = (15, 30, 45, 60, 75)


def janela_de(minuto: int) -> int:
    for ini in reversed(JANELAS):
        if minuto >= ini:
            return ini
    return JANELAS[0]


@dataclass
class Faixa:
    n: int = 0
    soma_p: float = 0.0
    acertos: int = 0

    @property
    def p_media(self) -> float:
        return self.soma_p / self.n if self.n else 0.0

    @property
    def real(self) -> float:
        return self.acertos / self.n if self.n else 0.0


@dataclass
class Medicao:
    mults: dict[str, dict[str, float]] = field(default_factory=dict)   # mercado -> janela -> x
    ativos: list[str] = field(default_factory=list)
    faixas: dict[str, dict[str, Faixa]] = field(default_factory=dict)  # testadas, modelo final
    n_teste: dict[str, int] = field(default_factory=dict)
    brier: dict[str, list[float]] = field(default_factory=dict)        # [sem, com] camada
    calculado_em: str = ""

    def mult(self, mercado: str, minuto: int) -> float:
        """Correccao aprendida; minuto 0 = antes do jogo (medida a parte)."""
        if mercado not in self.ativos:
            return 1.0
        janela = 0 if minuto <= 0 else janela_de(minuto)
        return self.mults.get(mercado, {}).get(str(janela), 1.0)

    def confiavel(self, mercado: str, p_min: float = 0.7) -> bool:
        """Os alertas so de jogador ligam quando, nas probabilidades altas, o que
        prometemos bate com o que acontece (±5pp em 50+ casos) e ha casos testados."""
        if self.n_teste.get(mercado, 0) < MIN_CONFIAVEL:
            return False
        fs = self.faixas.get(mercado, {})
        altas = [fs[_faixa_nome(ini, fim)] for ini, fim in FAIXAS
                 if ini >= p_min - 1e-9 and _faixa_nome(ini, fim) in fs]
        n = sum(f.n for f in altas)
        if n < 50:
            return False
        p = sum(f.soma_p for f in altas) / n
        real = sum(f.acertos for f in altas) / n
        return real >= p - 0.05


def _faixa_nome(ini: float, fim: float) -> str:
    return f"{ini * 100:.0f}-{min(fim, 1) * 100:.0f}%"


def pares_da_bd(sess, agora: datetime | None = None, dias: int = 60) -> list[dict]:
    """O que se previa para cada jogador vs o que ele fez (so jogos ja guardados)."""
    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    from apostas.utils.schema import PrevisaoJogadorLive as PJL
    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    prevs = sess.execute(select(PJL).where(PJL.ts >= agora - timedelta(days=dias))).scalars().all()
    if not prevs:
        return []
    eventos = sorted({p.event_id for p in prevs})
    finais: dict[tuple[int, int], Any] = {}
    for i in range(0, len(eventos), 900):
        for r in sess.execute(select(EJJ.sofascore_event_id, EJJ.sofascore_player_id, EJJ.minutos,
                                     EJJ.remates, EJJ.sot, EJJ.cards_amarelos, EJJ.minuto_amarelo)
                              .where(EJJ.sofascore_event_id.in_(eventos[i:i + 900]))).all():
            finais[(int(r.sofascore_event_id), int(r.sofascore_player_id))] = r
    out = []
    for p in prevs:
        f = finais.get((p.event_id, p.player_id))
        if f is None:
            continue
        if p.mercado == "cartao":
            real = 1.0 if (_n(f.cards_amarelos) > 0 or f.minuto_amarelo is not None) else 0.0
            if p.atual:
                continue
        else:
            if p.mercado == "remates" and f.remates is None:
                continue  # jogo guardado sem remates do jogador: nao se sabe
            total = _n(f.remates if p.mercado == "remates" else f.sot)
            real = max(0.0, total - p.atual)
        out.append({"mercado": p.mercado, "janela": p.janela, "lam": float(p.lam), "real": real,
                    "data": p.ts, "event_id": p.event_id})
    return out


def _brier(regs: list[dict], mults: dict | None) -> float:
    if not regs:
        return 0.0
    s = 0.0
    for r in regs:
        k = (mults or {}).get(r["mercado"], {}).get(str(r["janela"]), 1.0)
        p = _poisson_ge(r["lam"] * k, 1)
        s += (p - (1.0 if r["real"] >= 1 else 0.0)) ** 2
    return s / len(regs)


def _treinar(regs: list[dict], k: float = 30.0) -> dict[str, dict[str, float]]:
    soma: dict[tuple[str, str], list[float]] = {}
    for r in regs:
        acc = soma.setdefault((r["mercado"], str(r["janela"])), [0.0, 0.0])
        acc[0] += min(r["real"], 3.0)  # um jogador com 6 remates nao manda em tudo
        acc[1] += r["lam"]
    return {m: {j: round(min(2.5, max(0.4, (re + k * 0.1) / (pr + k * 0.1))), 3)
                for (mm, j), (re, pr) in soma.items() if mm == m and pr > 0}
            for m in {mm for mm, _ in soma}}


def calcular(pares: list[dict], anterior: Medicao | None = None,
             agora: datetime | None = None) -> Medicao:
    agora = agora or datetime.now()
    med = Medicao(calculado_em=agora.isoformat(timespec="seconds"))
    regs = sorted(pares, key=lambda r: str(r["data"]))
    if not regs:
        return med
    corte = int(len(regs) * 0.7)
    treino, teste = regs[:corte], regs[corte:]
    m_treino = _treinar(treino)
    for m in MERCADOS:
        t_m = [r for r in teste if r["mercado"] == m]
        med.n_teste[m] = len(t_m)
        if len(t_m) < 100:
            continue
        sem, com = _brier(t_m, None), _brier(t_m, m_treino)
        med.brier[m] = [round(sem, 5), round(com, 5)]
        antes = anterior is not None and m in anterior.ativos
        if com <= sem * (1.0 + 0.01 if antes else 1.0 - 0.01):    # histerese
            med.ativos.append(m)
    med.mults = _treinar(regs)
    for r in teste:
        k = med.mults.get(r["mercado"], {}).get(str(r["janela"]), 1.0) \
            if r["mercado"] in med.ativos else 1.0
        p = _poisson_ge(r["lam"] * k, 1)
        for ini, fim in FAIXAS:
            if ini <= p < fim:
                f = med.faixas.setdefault(r["mercado"], {}).setdefault(_faixa_nome(ini, fim),
                                                                     Faixa())
                f.n += 1
                f.soma_p += p
                f.acertos += int(r["real"] >= 1)
                break
    # ordem das faixas como em FAIXAS (para confiavel)
    for m, fs in med.faixas.items():
        med.faixas[m] = {_faixa_nome(i, f): fs[_faixa_nome(i, f)] for i, f in FAIXAS
                         if _faixa_nome(i, f) in fs}
    return med


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "jogadores.json"


def gravar(m: Medicao, path: Path | None = None) -> None:
    path = path or caminho()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(m), ensure_ascii=False, indent=1), encoding="utf-8")


def ler(path: Path | None = None) -> Medicao:
    path = path or caminho()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Medicao(mults=d.get("mults", {}), ativos=d.get("ativos", []),
                       faixas={m: {k: Faixa(**v) for k, v in fs.items()}
                               for m, fs in d.get("faixas", {}).items()},
                       n_teste=d.get("n_teste", {}), brier=d.get("brier", {}),
                       calculado_em=d.get("calculado_em", ""))
    except (OSError, ValueError, TypeError):
        return Medicao()


def recalcular_e_gravar(sess, path: Path | None = None) -> Medicao:
    m = calcular(pares_da_bd(sess), anterior=ler(path))
    gravar(m, path)
    return m


def formatar(m: Medicao) -> str:
    linhas = ["*👤 Jogadores* (o que previa para cada jogador vs o que ele fez; testado nos "
              "momentos mais recentes)"]
    if not any(m.n_teste.values()):
        return "\n".join(linhas + ["  Ainda sem jogos acabados com previsoes de jogadores: "
                                   "o scanner guarda-as a partir de agora."])
    for mercado in MERCADOS:
        n = m.n_teste.get(mercado, 0)
        if not n:
            continue
        fs = m.faixas.get(mercado, {})
        faixas = " · ".join(f"{k}: real {f.real * 100:.0f}% (n={f.n})" for k, f in fs.items()
                            if f.n)
        estado = ("✅ alertas so de jogador ligados" if m.confiavel(mercado)
                  else "alertas so de jogador a espera de provas")
        mult = ""
        if mercado in m.ativos:
            mm = m.mults.get(mercado, {})
            mult = " · correccao: " + " ".join(f"{j}'×{v:.2f}" for j, v in
                                               sorted(mm.items(), key=lambda kv: int(kv[0])))
        linhas.append(f"  {NOMES[mercado]} (+1 ate ao fim): {faixas} · {estado}{mult}")
    return "\n".join(linhas)
