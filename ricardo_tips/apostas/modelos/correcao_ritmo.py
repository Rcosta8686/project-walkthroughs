"""Correccao do ritmo do resto do jogo, aprendida com os jogos ja acabados.

O modelo live (live_predictor.prever) estima quantos golos/cantos/cartoes/
remates faltam ate ao fim. As projeccoes mostraram que fica ~20-30% abaixo do
que acontece (entre outras coisas conta 90 - minuto e ignora os descontos), e
e isso que faz os UNDER prometerem mais do que acertam.

Aqui, para cada momento guardado de um jogo ao vivo (EstadoLiveSnapshot) cujo
resultado final ja esta na BD (EstatisticaEquipaJogo), volta-se a correr o
modelo e compara-se o que ele previa para o resto do jogo com o que aconteceu.
Por (mercado, fase de 15 min):

    mult = soma(real que faltava) / soma(previsto que faltava)

com os jogos recentes a pesar mais (meia-vida 14 dias), encolhido para 1 com
poucos jogos e limitado a [0.6, 2.5]. Um mercado so fica LIGADO se, treinando
nos jogos mais antigos, a correccao baixar o erro (desvio de Poisson) nos mais
recentes que nunca viu. Recalculado todas as noites (incremento diario).

Tres versoes do modelo competem por mercado (BASES): o calculo antigo, o ritmo
suavizado com o "normal" generico e o ritmo suavizado com o normal das duas
equipas (mini-cerebros, priors_equipas; nos jogos passados so com os jogos de
antes de cada um). Fica a que erra menos nos jogos recentes; so se troca de
versao quando a nova ganha por MELHORIA_MIN (nao salta de noite para noite).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

MIN_PARES = 30
K_ENCOLHIMENTO = 30
# Teto 2.5: com 1.8 os cartoes aos 75' batiam no limite (o modelo ainda previa
# menos cartoes no fim do que saem). So liga se melhorar no teste.
MULT_MIN, MULT_MAX = 0.6, 2.5
MEIA_VIDA_DIAS = 14.0
JANELA_DIAS = 60
MIN_TESTE = 50
MELHORIA_MIN = 0.01          # a correccao tem de baixar o erro pelo menos 1%
JANELAS = (15, 30, 45, 60, 75)  # inicio de cada fase de 15 min (75 = 75'-88')
MINUTO_MAX = 88

# mercado -> (atributo da previsao, colunas do snapshot casa/fora, coluna final na BD)
MERCADOS = {
    "golos": ("lam_golos_restantes", ("marcador_casa", "marcador_fora"), "golos_marcados"),
    "cantos": ("lam_cantos_restantes", ("corners_casa", "corners_fora"), "cantos"),
    "cartoes": ("lam_cartoes_amarelos_restantes", ("yellow_casa", "yellow_fora"), "cards_amarelos"),
    "remates": ("lam_shots_restantes", ("shots_total_casa", "shots_total_fora"), "remates"),
    "remates_a_baliza": ("lam_shots_on_target_restantes",
                         ("shots_on_target_casa", "shots_on_target_fora"), "sot"),
}
BASES = ("antigo", "suave", "equipas")
_CHAVE_BASE = {"antigo": "previsto", "suave": "previsto_suave", "equipas": "previsto_equipas"}
# Campos da PrevisaoLive que pertencem a cada mercado (para juntar versoes).
CAMPOS = {
    "golos": ("lam_golos_restantes", "lam_golos_casa_rest", "lam_golos_fora_rest",
              "p_mais_1_golo", "p_mais_2_golos", "p_btts_final",
              "p_casa_vence", "p_empate", "p_fora_vence"),
    "cantos": ("lam_cantos_restantes", "projeccao_cantos_casa", "projeccao_cantos_fora"),
    "cartoes": ("lam_cartoes_amarelos_restantes",),
    "remates": ("lam_shots_restantes", "projeccao_shots_casa", "projeccao_shots_fora"),
    "remates_a_baliza": ("lam_shots_on_target_restantes", "projeccao_sot_casa",
                         "projeccao_sot_fora"),
}
# Mercados cujas probabilidades dependem do ritmo de golos.
DEPENDE_DE = {"golos": "golos", "btts": "golos", "1x2": "golos", "cantos": "cantos",
              "cartoes": "cartoes", "cards": "cartoes", "remates": "remates",
              "remates_a_baliza": "remates_a_baliza", "sot": "remates_a_baliza"}


def janela_de(minuto: int) -> int:
    for ini in reversed(JANELAS):
        if minuto >= ini:
            return ini
    return JANELAS[0]


# ─── Pares previsto vs real ─────────────────────────────────────────────

_COLUNAS_SNAPSHOT = (
    "event_id", "ts", "home", "away", "liga", "minuto", "estado_jogo",
    "marcador_casa", "marcador_fora", "xg_casa", "xg_fora",
    "shots_total_casa", "shots_total_fora", "shots_on_target_casa", "shots_on_target_fora",
    "big_chances_casa", "big_chances_fora", "posse_casa", "corners_casa", "corners_fora",
    "shots_blocked_casa", "shots_blocked_fora", "yellow_casa", "yellow_fora",
    "red_casa", "red_fora", "fouls_casa", "fouls_fora", "arbitro",
)


def _estado_de_snapshot(sn):
    from apostas.ingestao.sofascore_live import EstadoLive
    posse = sn.posse_casa
    return EstadoLive(
        event_id=int(sn.event_id), home=sn.home, away=sn.away, liga=sn.liga or "",
        minuto=int(sn.minuto), estado=sn.estado_jogo or "",
        marcador_casa=int(sn.marcador_casa or 0), marcador_fora=int(sn.marcador_fora or 0),
        xg_casa=sn.xg_casa, xg_fora=sn.xg_fora,
        shots_total_casa=sn.shots_total_casa, shots_total_fora=sn.shots_total_fora,
        shots_on_target_casa=sn.shots_on_target_casa,
        shots_on_target_fora=sn.shots_on_target_fora,
        big_chances_casa=sn.big_chances_casa, big_chances_fora=sn.big_chances_fora,
        possession_casa=posse, possession_fora=(1 - posse) if posse is not None else None,
        corners_casa=sn.corners_casa, corners_fora=sn.corners_fora,
        shots_blocked_casa=sn.shots_blocked_casa, shots_blocked_fora=sn.shots_blocked_fora,
        yellow_cards_casa=int(sn.yellow_casa or 0), yellow_cards_fora=int(sn.yellow_fora or 0),
        red_cards_casa=int(sn.red_casa or 0), red_cards_fora=int(sn.red_fora or 0),
        fouls_casa=sn.fouls_casa, fouls_fora=sn.fouls_fora, arbitro=sn.arbitro,
    )


_COLUNAS_EEJ = ("sofascore_event_id", "sofascore_team_id", "data_jogo", "era_casa",
                "golos_marcados", "golos_sofridos", "remates", "sot", "cantos", "cards_amarelos",
                "arbitro", "liga")
_DIAS_HISTORICO = 150  # para os priors dos jogos mais antigos da janela


def _jogos_acabados(linhas) -> dict[int, dict]:
    """Por jogo: totais finais por mercado (so com as 2 equipas), equipas e data."""
    por_evento: dict[int, list] = {}
    for r in linhas:
        por_evento.setdefault(int(r.sofascore_event_id), []).append(r)
    out: dict[int, dict] = {}
    for eid, rows in por_evento.items():
        if len(rows) != 2:
            continue
        casa = next((r for r in rows if r.era_casa), None)
        fora = next((r for r in rows if not r.era_casa), None)
        if casa is None or fora is None:
            continue
        totais = {}
        for mercado, (_, _, col) in MERCADOS.items():
            a, b = getattr(casa, col), getattr(fora, col)
            if a is not None and b is not None:
                totais[mercado] = float(a) + float(b)
        out[eid] = {"totais": totais, "casa": int(casa.sofascore_team_id),
                    "fora": int(fora.sofascore_team_id), "data": casa.data_jogo,
                    "arbitro": getattr(casa, "arbitro", None) or getattr(fora, "arbitro", None)}
    return out


def pares_da_bd(sess, agora: datetime | None = None, dias: int = JANELA_DIAS) -> list[dict]:
    """Um par por (jogo, fase, mercado): o 1o snapshot dessa fase, com a previsao
    das 3 versoes do modelo."""
    from sqlalchemy import select

    from apostas.modelos import live_predictor
    from apostas.modelos.priors_equipas import HistoricoEquipas, PriorsJogo
    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.tempo import agora_utc

    agora = agora or agora_utc()
    desde = agora - timedelta(days=dias)
    linhas = sess.execute(
        select(*[getattr(EEJ, c) for c in _COLUNAS_EEJ])
        .where(EEJ.data_jogo >= desde - timedelta(days=_DIAS_HISTORICO))).all()
    historico = HistoricoEquipas(linhas)
    from apostas.modelos.arbitros import HistoricoArbitros
    arbitros = HistoricoArbitros(linhas)
    jogos = {e: j for e, j in _jogos_acabados(linhas).items()
             if j["data"] is not None and j["data"] >= desde - timedelta(days=1)}
    if not jogos:
        return []
    ids = sorted(jogos)
    # So as colunas usadas (linhas leves, nao objectos ORM): 60 dias de
    # snapshots podem ser centenas de milhares de linhas.
    cols = [getattr(Snap, c) for c in _COLUNAS_SNAPSHOT]
    snaps = []
    for i in range(0, len(ids), 900):  # limite de variaveis por consulta no SQLite
        snaps += sess.execute(
            select(*cols).where(Snap.event_id.in_(ids[i:i + 900]), Snap.ts >= desde,
                                Snap.minuto >= JANELAS[0], Snap.minuto <= MINUTO_MAX)
            .order_by(Snap.event_id, Snap.minuto)).all()
    todos = live_predictor.MERCADOS_SUAVIZAVEIS
    priors_cache: dict[int, PriorsJogo | None] = {}
    vistos: set[tuple[int, int]] = set()
    pares: list[dict] = []
    for sn in snaps:
        chave = (int(sn.event_id), janela_de(int(sn.minuto)))
        if chave in vistos:
            continue
        vistos.add(chave)
        jogo = jogos[chave[0]]
        if chave[0] not in priors_cache:  # so jogos de ANTES deste
            pr = historico.priors(jogo["casa"], jogo["fora"], antes=jogo["data"])
            arb = arbitros.perfil(jogo.get("arbitro") or sn.arbitro, antes=jogo["data"])
            if pr is not None and arb is not None:
                pr = replace(pr, fator_arbitro=arb.fator)
            priors_cache[chave[0]] = pr
        priors = priors_cache[chave[0]]
        try:
            estado = _estado_de_snapshot(sn)
            prev = live_predictor.prever(estado)
            prev_suave = live_predictor.prever(estado, ritmo_suavizado=todos)
            prev_eq = (live_predictor.prever(estado, ritmo_suavizado=todos, **priors.kwargs())
                       if priors is not None else prev_suave)
        except Exception:  # noqa: BLE001 — snapshot incompleto
            continue
        for mercado, (attr, (cc, cf), _) in MERCADOS.items():
            vc, vf = getattr(sn, cc), getattr(sn, cf)
            if mercado not in jogo["totais"] or vc is None or vf is None:
                continue
            real = jogo["totais"][mercado] - (vc + vf)
            if real < -0.5:  # final incoerente com o que ja se via
                continue
            pares.append({"mercado": mercado, "janela": chave[1], "event_id": chave[0],
                          "previsto": max(0.0, float(getattr(prev, attr))),
                          "previsto_suave": max(0.0, float(getattr(prev_suave, attr))),
                          "previsto_equipas": max(0.0, float(getattr(prev_eq, attr))),
                          "real": max(0.0, real), "data": sn.ts})
    return pares


# ─── Treino e teste ─────────────────────────────────────────────────────

@dataclass
class Mult:
    n: int
    previsto: float  # media ponderada do que o modelo dizia que faltava
    real: float      # media ponderada do que faltava de facto
    mult: float


def _pesos(pares: list[dict]) -> list[float]:
    datas = [p["data"] for p in pares if p.get("data") is not None]
    if not datas:
        return [1.0] * len(pares)
    ref = max(datas)
    return [1.0 if p.get("data") is None else
            0.5 ** (max(0.0, (ref - p["data"]).total_seconds() / 86400) / MEIA_VIDA_DIAS)
            for p in pares]


def treinar(pares: list[dict]) -> dict[tuple[str, int | None], Mult]:
    grupos: dict[tuple[str, int | None], list[tuple[dict, float]]] = {}
    for p, w in zip(pares, _pesos(pares)):
        grupos.setdefault((p["mercado"], p["janela"]), []).append((p, w))
        grupos.setdefault((p["mercado"], None), []).append((p, w))
    out: dict[tuple[str, int | None], Mult] = {}
    for chave, g in grupos.items():
        if len(g) < MIN_PARES:
            continue
        sw = sum(w for _, w in g)
        prev = sum(w * p["previsto"] for p, w in g)
        real = sum(w * p["real"] for p, w in g)
        if prev <= 0 or sw <= 0:
            continue
        enc = sw / (sw + K_ENCOLHIMENTO)
        mult = min(MULT_MAX, max(MULT_MIN, enc * (real / prev) + (1 - enc)))
        out[chave] = Mult(n=len(g), previsto=prev / sw, real=real / sw, mult=round(mult, 3))
    return out


def _mult(mults: dict, mercado: str, janela: int) -> float:
    m = mults.get((mercado, janela)) or mults.get((mercado, None))
    return m.mult if m is not None else 1.0


def _desvio_poisson(y: float, lam: float) -> float:
    lam = max(lam, 0.05)
    termo = y * math.log(y / lam) if y > 0 else 0.0
    return 2.0 * (termo - (y - lam))


def _com_base(pares: list[dict], bases: dict[str, str]) -> list[dict]:
    """Pares com 'previsto' = previsao da versao escolhida para cada mercado."""
    out = []
    for p in pares:
        chave = _CHAVE_BASE.get(bases.get(p["mercado"], "antigo"), "previsto")
        out.append({**p, "previsto": p.get(chave, p["previsto"])} if chave != "previsto" else p)
    return out


def _erro(teste: list[dict], chave: str = "previsto", mults: dict | None = None) -> float:
    return sum(_desvio_poisson(p["real"], p.get(chave, p["previsto"])
                               * (_mult(mults, p["mercado"], p["janela"]) if mults else 1.0))
               for p in teste) / len(teste)


def _escolher_base(erros: dict[str, float], anterior: str = "antigo") -> str:
    """A versao com menos erro, mas so troca da anterior se ganhar por MELHORIA_MIN."""
    actual = anterior if anterior in erros else "antigo"
    melhor = min(erros, key=lambda b: (erros[b], BASES.index(b)))
    if melhor != actual and erros[melhor] <= erros[actual] * (1 - MELHORIA_MIN):
        return melhor
    return actual


def avaliar(pares: list[dict], frac_treino: float = 0.7,
            bases_anteriores: dict[str, str] | None = None) -> dict[str, dict]:
    """Treina nos jogos mais antigos, mede nos mais recentes (por jogo, sem fuga).

    Por mercado: erro de cada versao do modelo (escolhe-se a base) e da base
    com o multiplicador aprendido no treino.
    """
    bases_anteriores = bases_anteriores or {}
    data_jogo: dict[int, datetime] = {}
    for p in pares:
        d = p.get("data") or datetime.min
        data_jogo[p["event_id"]] = min(data_jogo.get(p["event_id"], d), d)
    jogos = sorted(data_jogo, key=lambda e: data_jogo[e])
    corte = set(jogos[:int(len(jogos) * frac_treino)])
    res: dict[str, dict] = {}
    for mercado in MERCADOS:
        do_mercado = [p for p in pares if p["mercado"] == mercado]
        teste = [p for p in do_mercado if p["event_id"] not in corte]
        if not teste:
            continue
        erros = {b: _erro(teste, _CHAVE_BASE[b]) for b in BASES}
        base = _escolher_base(erros, bases_anteriores.get(mercado, "antigo"))
        com_base = _com_base(do_mercado, {mercado: base})
        mults = treinar([p for p in com_base if p["event_id"] in corte])
        teste_base = [p for p in com_base if p["event_id"] not in corte]
        res[mercado] = {
            "n_teste": len(teste), "base": base,
            "erro_antigo": erros["antigo"], "erro_suave": erros["suave"],
            "erro_equipas": erros["equipas"], "erro_bruto": erros[base],
            "erro_corrigido": _erro(teste_base, mults=mults),
            "previsto": sum(p["previsto"] for p in teste_base) / len(teste_base),
            "real": sum(p["real"] for p in teste_base) / len(teste_base),
        }
    return res


# ─── O que o scanner usa ────────────────────────────────────────────────

@dataclass
class CorrecaoRitmo:
    mults: dict[tuple[str, int | None], Mult] = field(default_factory=dict)
    ativos: set[str] = field(default_factory=set)        # multiplicador ligado
    bases: dict[str, str] = field(default_factory=dict)  # mercado -> versao do modelo
    datas_ativacao: dict[str, datetime] = field(default_factory=dict)
    avaliacao: dict[str, dict] = field(default_factory=dict)

    @property
    def suavizados(self) -> set[str]:
        return {m for m, b in self.bases.items() if b in ("suave", "equipas")}

    @property
    def com_equipas(self) -> set[str]:
        return {m for m, b in self.bases.items() if b == "equipas"}

    @property
    def mudados(self) -> set[str]:
        return self.ativos | {m for m, b in self.bases.items() if b != "antigo"}

    def mult(self, mercado: str, minuto: int) -> float:
        if mercado not in self.ativos:
            return 1.0
        return _mult(self.mults, mercado, janela_de(minuto))

    def afecta(self, mercado: str) -> str | None:
        """Mercado do ritmo de que este depende, se estiver ligado (golos p/ BTTS, 1X2)."""
        base = DEPENDE_DE.get((mercado or "").lower())
        return base if base in self.mudados else None


def filtrar_desde_mudanca(rows: list[dict], ritmo: CorrecaoRitmo) -> list[dict]:
    """So as sugestoes calculadas com o modelo actual: num mercado em que a
    versao do modelo ou o multiplicador mudou, as de antes dessa data
    prometiam com outro calculo (calibrador, PRIME)."""
    if not ritmo.mudados:
        return rows
    out = []
    for r in rows:
        base = ritmo.afecta(r.get("mercado", ""))
        desde = ritmo.datas_ativacao.get(base) if base else None
        if desde is not None and (r.get("ts") or "") < desde.isoformat(timespec="seconds"):
            continue
        out.append(r)
    return out


def calcular(pares: list[dict], anterior: CorrecaoRitmo | None = None,
             agora: datetime | None = None) -> CorrecaoRitmo:
    from apostas.utils.tempo import agora_utc
    anterior = anterior or CorrecaoRitmo()
    avaliacao = avaliar(pares, bases_anteriores=anterior.bases)
    fiaveis = {m: a for m, a in avaliacao.items() if a["n_teste"] >= MIN_TESTE}
    bases = {m: a["base"] for m, a in fiaveis.items() if a["base"] != "antigo"}
    ativos = {m for m, a in fiaveis.items()
              if a["erro_corrigido"] <= a["erro_bruto"] * (1 - MELHORIA_MIN)}
    agora = agora or agora_utc()
    datas = {}
    for m in ativos | set(bases):
        igual = (bases.get(m, "antigo") == anterior.bases.get(m, "antigo")
                 and (m in ativos) == (m in anterior.ativos))
        # a data conta desde a ultima mudanca de configuracao deste mercado:
        # o calibrador so aprende com alertas calculados assim
        datas[m] = anterior.datas_ativacao.get(m, agora) if igual else agora
    return CorrecaoRitmo(mults=treinar(_com_base(pares, bases)), ativos=ativos, bases=bases,
                         datas_ativacao=datas, avaliacao=avaliacao)


def prever_corrigido(estado, correcao: CorrecaoRitmo, priors=None, **kwargs):
    """Previsao com a versao do modelo escolhida para cada mercado e o
    multiplicador aprendido por cima. `priors` (PriorsJogo) para a versao
    'equipas'; kwargs vao para live_predictor.prever."""
    from apostas.modelos import live_predictor
    prev = live_predictor.prever(estado, **kwargs)
    versoes = set(correcao.bases.values()) - {"antigo"}
    if versoes:
        todos = live_predictor.MERCADOS_SUAVIZAVEIS
        suave = live_predictor.prever(estado, ritmo_suavizado=todos, **kwargs)
        equipas = (live_predictor.prever(estado, ritmo_suavizado=todos,
                                         **{**priors.kwargs(), **kwargs})
                   if "equipas" in versoes and priors is not None else suave)
        fontes = {"suave": suave, "equipas": equipas}
        mudancas = {campo: getattr(fontes[b], campo)
                    for m, b in correcao.bases.items() if b in fontes
                    for campo in CAMPOS.get(m, ())}
        if mudancas:
            prev = replace(prev, **mudancas)
    return aplicar(prev, estado, correcao)


def aplicar(prev, estado, correcao: CorrecaoRitmo):
    """PrevisaoLive com o ritmo restante corrigido nos mercados ligados."""
    from apostas.modelos import live_predictor as lp
    minuto = int(getattr(prev, "minuto", 0) or 0)
    m = {k: correcao.mult(k, minuto) for k in MERCADOS}
    if all(v == 1.0 for v in m.values()):
        return prev
    mudancas: dict = {}
    if m["golos"] != 1.0:
        lg = prev.lam_golos_restantes * m["golos"]
        lc, lf = prev.lam_golos_casa_rest * m["golos"], prev.lam_golos_fora_rest * m["golos"]
        mc, mf = int(estado.marcador_casa or 0), int(estado.marcador_fora or 0)
        if mc > 0 and mf > 0:
            btts = 1.0
        elif mc == 0 and mf == 0:
            btts = (1 - math.exp(-lg / 2)) ** 2
        else:
            btts = 1 - math.exp(-lg / 2)
        pc, pe, pf = lp._calc_1x2(mc, mf, lc, lf)
        mudancas.update(lam_golos_restantes=lg, lam_golos_casa_rest=lc, lam_golos_fora_rest=lf,
                        p_mais_1_golo=1 - math.exp(-lg), p_mais_2_golos=1 - math.exp(-lg) * (1 + lg),
                        p_btts_final=btts, p_casa_vence=pc, p_empate=pe, p_fora_vence=pf)
    if m["cartoes"] != 1.0:
        mudancas["lam_cartoes_amarelos_restantes"] = prev.lam_cartoes_amarelos_restantes * m["cartoes"]

    def _proj(total_attr, casa_attr, fora_attr, est_c, est_f, k):
        ac, af = float(getattr(estado, est_c) or 0), float(getattr(estado, est_f) or 0)
        mudancas[total_attr] = getattr(prev, total_attr) * k
        for attr, actual in ((casa_attr, ac), (fora_attr, af)):
            v = getattr(prev, attr)
            mudancas[attr] = actual + max(0.0, v - actual) * k

    if m["cantos"] != 1.0:
        _proj("lam_cantos_restantes", "projeccao_cantos_casa", "projeccao_cantos_fora",
              "corners_casa", "corners_fora", m["cantos"])
    if m["remates"] != 1.0:
        _proj("lam_shots_restantes", "projeccao_shots_casa", "projeccao_shots_fora",
              "shots_total_casa", "shots_total_fora", m["remates"])
    if m["remates_a_baliza"] != 1.0:
        _proj("lam_shots_on_target_restantes", "projeccao_sot_casa", "projeccao_sot_fora",
              "shots_on_target_casa", "shots_on_target_fora", m["remates_a_baliza"])
    return replace(prev, **mudancas)


# ─── Guardar / ler (o scanner le o ficheiro; o incremento diario escreve) ─

def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "correcao_ritmo.json"


def gravar(c: CorrecaoRitmo, path: Path | None = None) -> None:
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    dados = {
        "ativos": sorted(c.ativos),
        "bases": c.bases,
        "datas_ativacao": {m: d.isoformat(timespec="seconds") for m, d in c.datas_ativacao.items()},
        "mults": [{"mercado": m, "janela": j, "n": x.n, "previsto": x.previsto,
                   "real": x.real, "mult": x.mult} for (m, j), x in c.mults.items()],
        "avaliacao": c.avaliacao,
    }
    p.write_text(json.dumps(dados, indent=1, ensure_ascii=False), encoding="utf-8")


def ler(path: Path | None = None) -> CorrecaoRitmo:
    p = path or caminho()
    if not p.exists():
        return CorrecaoRitmo()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return CorrecaoRitmo(
            mults={(x["mercado"], x["janela"]): Mult(n=x["n"], previsto=x["previsto"],
                                                     real=x["real"], mult=x["mult"])
                   for x in d.get("mults", [])},
            ativos=set(d.get("ativos", [])),
            # ficheiros antigos so tinham "suavizados"
            bases=d.get("bases") or {m: "suave" for m in d.get("suavizados", [])},
            datas_ativacao={m: datetime.fromisoformat(v)
                            for m, v in d.get("datas_ativacao", {}).items()},
            avaliacao=d.get("avaliacao", {}),
        )
    except (ValueError, KeyError, TypeError):
        return CorrecaoRitmo()


def recalcular_e_gravar(sess, path: Path | None = None) -> CorrecaoRitmo:
    anterior = ler(path)
    c = calcular(pares_da_bd(sess), anterior=anterior)
    gravar(c, path)
    return c


_NOMES = {"golos": "⚽ golos", "cantos": "🚩 cantos", "cartoes": "🟨 cartoes",
          "remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza"}


_NOME_BASE = {"antigo": "calculo antigo", "suave": "normal generico",
              "equipas": "normal das duas equipas (mini-cerebros)"}


def formatar(c: CorrecaoRitmo) -> str:
    linhas = ["*Ritmo do resto do jogo* (o que o modelo previa que faltava vs o que aconteceu)"]
    if not c.avaliacao:
        return "\n".join(linhas + ["  Ainda sem jogos acabados com momentos guardados suficientes."])

    def pct(erro: float, ref: float) -> str:
        g = (1 - erro / ref) * 100 if ref else 0.0
        return f"-{g:.0f}%" if g >= 0.5 else f"+{-g:.0f}%" if g <= -0.5 else "igual"

    for mercado in MERCADOS:
        a = c.avaliacao.get(mercado)
        if not a:
            continue
        base = c.bases.get(mercado, "antigo")
        com_mult = mercado in c.ativos
        final = a["erro_corrigido"] if com_mult else a["erro_bruto"]
        estado = "✅" if mercado in c.mudados else "·"
        linhas.append(f"  {estado} {_NOMES[mercado]}: previa +{a['previsto']:.1f} · real "
                      f"+{a['real']:.1f} · erro {pct(final, a['erro_antigo'])} vs o modelo "
                      f"antigo (teste em {a['n_teste']} momentos)")
        if "erro_equipas" in a:
            linhas.append(f"      versoes: generico {pct(a['erro_suave'], a['erro_antigo'])} · "
                          f"mini-cerebros {pct(a['erro_equipas'], a['erro_antigo'])} → usa: "
                          f"{_NOME_BASE[base]}")
        if com_mult:
            fases = " ".join(f"{j}'×{c.mults[(mercado, j)].mult:.2f}"
                             for j in JANELAS if (mercado, j) in c.mults)
            linhas.append(f"      + multiplicador por fase ({pct(a['erro_corrigido'], a['erro_bruto'])}"
                          f" por cima): {fases}")
    return "\n".join(linhas)
