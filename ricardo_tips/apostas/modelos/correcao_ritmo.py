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
poucos jogos e limitado a [0.6, 1.8]. Um mercado so fica LIGADO se, treinando
nos jogos mais antigos, a correccao baixar o erro (desvio de Poisson) nos mais
recentes que nunca viu. Recalculado todas as noites (incremento diario).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

MIN_PARES = 30
K_ENCOLHIMENTO = 30
MULT_MIN, MULT_MAX = 0.6, 1.8
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


def _finais(sess, desde: datetime) -> dict[int, dict[str, float]]:
    """Totais finais (2 equipas) por jogo e mercado; so mercados com as 2 linhas."""
    from sqlalchemy import func, select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    cols = {m: getattr(EEJ, c) for m, (_, _, c) in MERCADOS.items()}
    q = (select(EEJ.sofascore_event_id, func.count(),
                *[func.sum(c) for c in cols.values()], *[func.count(c) for c in cols.values()])
         .where(EEJ.data_jogo >= desde - timedelta(days=1))
         .group_by(EEJ.sofascore_event_id))
    out: dict[int, dict[str, float]] = {}
    k = len(cols)
    for row in sess.execute(q).all():
        eid, n = row[0], row[1]
        if n != 2:
            continue
        somas, contagens = row[2:2 + k], row[2 + k:2 + 2 * k]
        out[int(eid)] = {m: float(s) for m, s, c in zip(cols, somas, contagens)
                         if c == 2 and s is not None}
    return out


def pares_da_bd(sess, agora: datetime | None = None, dias: int = JANELA_DIAS) -> list[dict]:
    """Um par por (jogo, fase, mercado): o 1o snapshot dessa fase."""
    from sqlalchemy import select

    from apostas.modelos import live_predictor
    from apostas.utils.schema import EstadoLiveSnapshot as Snap

    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    desde = agora - timedelta(days=dias)
    finais = _finais(sess, desde)
    if not finais:
        return []
    ids = sorted(finais)
    # So as colunas usadas (linhas leves, nao objectos ORM): 60 dias de
    # snapshots podem ser centenas de milhares de linhas.
    cols = [getattr(Snap, c) for c in _COLUNAS_SNAPSHOT]
    snaps = []
    for i in range(0, len(ids), 900):  # limite de variaveis por consulta no SQLite
        snaps += sess.execute(
            select(*cols).where(Snap.event_id.in_(ids[i:i + 900]), Snap.ts >= desde,
                                Snap.minuto >= JANELAS[0], Snap.minuto <= MINUTO_MAX)
            .order_by(Snap.event_id, Snap.minuto)).all()
    vistos: set[tuple[int, int]] = set()
    pares: list[dict] = []
    for sn in snaps:
        chave = (int(sn.event_id), janela_de(int(sn.minuto)))
        if chave in vistos:
            continue
        vistos.add(chave)
        try:
            estado = _estado_de_snapshot(sn)
            prev = live_predictor.prever(estado)
            prev_suave = live_predictor.prever(
                estado, ritmo_suavizado=live_predictor.MERCADOS_SUAVIZAVEIS)
        except Exception:  # noqa: BLE001 — snapshot incompleto
            continue
        final = finais[int(sn.event_id)]
        for mercado, (attr, (cc, cf), _) in MERCADOS.items():
            vc, vf = getattr(sn, cc), getattr(sn, cf)
            if mercado not in final or vc is None or vf is None:
                continue
            real = final[mercado] - (vc + vf)
            if real < -0.5:  # final incoerente com o que ja se via
                continue
            pares.append({"mercado": mercado, "janela": chave[1], "event_id": chave[0],
                          "previsto": max(0.0, float(getattr(prev, attr))),
                          "previsto_suave": max(0.0, float(getattr(prev_suave, attr))),
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


def _com_base(pares: list[dict], suaves: set[str]) -> list[dict]:
    """Pares com 'previsto' = previsao da base escolhida para cada mercado."""
    return [{**p, "previsto": p.get("previsto_suave", p["previsto"])}
            if p["mercado"] in suaves else p for p in pares]


def _erro(teste: list[dict], chave: str = "previsto", mults: dict | None = None) -> float:
    return sum(_desvio_poisson(p["real"], p.get(chave, p["previsto"])
                               * (_mult(mults, p["mercado"], p["janela"]) if mults else 1.0))
               for p in teste) / len(teste)


def avaliar(pares: list[dict], frac_treino: float = 0.7) -> dict[str, dict]:
    """Treina nos jogos mais antigos, mede nos mais recentes (por jogo, sem fuga).

    Por mercado: erro do modelo antigo, do ritmo suavizado (escolhe-se o que
    erra menos) e da base escolhida com o multiplicador aprendido no treino.
    """
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
        erro_antigo = _erro(teste, "previsto")
        erro_suave = _erro(teste, "previsto_suave")
        suave = erro_suave <= erro_antigo * (1 - MELHORIA_MIN)
        base = _com_base(do_mercado, {mercado} if suave else set())
        mults = treinar([p for p in base if p["event_id"] in corte])
        teste_base = [p for p in base if p["event_id"] not in corte]
        res[mercado] = {
            "n_teste": len(teste), "suave": suave,
            "erro_antigo": erro_antigo, "erro_suave": erro_suave,
            "erro_bruto": min(erro_antigo, erro_suave) if suave else erro_antigo,
            "erro_corrigido": _erro(teste_base, mults=mults),
            "previsto": sum(p["previsto"] for p in teste_base) / len(teste_base),
            "real": sum(p["real"] for p in teste_base) / len(teste_base),
        }
    return res


# ─── O que o scanner usa ────────────────────────────────────────────────

@dataclass
class CorrecaoRitmo:
    mults: dict[tuple[str, int | None], Mult] = field(default_factory=dict)
    ativos: set[str] = field(default_factory=set)       # multiplicador ligado
    suavizados: set[str] = field(default_factory=set)   # ritmo suavizado ligado
    datas_ativacao: dict[str, datetime] = field(default_factory=dict)
    avaliacao: dict[str, dict] = field(default_factory=dict)

    @property
    def mudados(self) -> set[str]:
        return self.ativos | self.suavizados

    def mult(self, mercado: str, minuto: int) -> float:
        if mercado not in self.ativos:
            return 1.0
        return _mult(self.mults, mercado, janela_de(minuto))

    def afecta(self, mercado: str) -> str | None:
        """Mercado do ritmo de que este depende, se estiver ligado (golos p/ BTTS, 1X2)."""
        base = DEPENDE_DE.get((mercado or "").lower())
        return base if base in self.mudados else None


def calcular(pares: list[dict], anteriores: dict[str, datetime] | None = None,
             agora: datetime | None = None) -> CorrecaoRitmo:
    avaliacao = avaliar(pares)
    fiaveis = {m: a for m, a in avaliacao.items() if a["n_teste"] >= MIN_TESTE}
    suavizados = {m for m, a in fiaveis.items() if a["suave"]}
    ativos = {m for m, a in fiaveis.items()
              if a["erro_corrigido"] <= a["erro_bruto"] * (1 - MELHORIA_MIN)}
    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    anteriores = anteriores or {}
    datas = {m: anteriores.get(m, agora) for m in ativos | suavizados}
    return CorrecaoRitmo(mults=treinar(_com_base(pares, suavizados)), ativos=ativos,
                         suavizados=suavizados, datas_ativacao=datas, avaliacao=avaliacao)


def prever_corrigido(estado, correcao: CorrecaoRitmo, **kwargs):
    """live_predictor.prever com o ritmo suavizado nos mercados em que ganhou
    e o multiplicador aprendido por cima. kwargs vao para prever()."""
    from apostas.modelos import live_predictor
    prev = live_predictor.prever(estado, ritmo_suavizado=tuple(sorted(correcao.suavizados)),
                                 **kwargs)
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
        "suavizados": sorted(c.suavizados),
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
            suavizados=set(d.get("suavizados", [])),
            datas_ativacao={m: datetime.fromisoformat(v)
                            for m, v in d.get("datas_ativacao", {}).items()},
            avaliacao=d.get("avaliacao", {}),
        )
    except (ValueError, KeyError, TypeError):
        return CorrecaoRitmo()


def recalcular_e_gravar(sess, path: Path | None = None) -> CorrecaoRitmo:
    anterior = ler(path)
    c = calcular(pares_da_bd(sess), anteriores=anterior.datas_ativacao)
    gravar(c, path)
    return c


_NOMES = {"golos": "⚽ golos", "cantos": "🚩 cantos", "cartoes": "🟨 cartoes",
          "remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza"}


def formatar(c: CorrecaoRitmo) -> str:
    linhas = ["*Ritmo do resto do jogo* (o que o modelo previa que faltava vs o que aconteceu)"]
    if not c.avaliacao:
        return "\n".join(linhas + ["  Ainda sem jogos acabados com momentos guardados suficientes."])
    for mercado in MERCADOS:
        a = c.avaliacao.get(mercado)
        if not a:
            continue
        estado = "✅ ligado" if mercado in c.mudados else "· desligado"
        melhoria = (1 - a["erro_corrigido"] / a["erro_bruto"]) * 100 if a["erro_bruto"] else 0
        efeito = (f"erro -{melhoria:.0f}%" if melhoria >= 0.5
                  else f"erro +{-melhoria:.0f}%" if melhoria <= -0.5 else "erro igual")
        fases = " ".join(f"{j}'×{c.mults[(mercado, j)].mult:.2f}"
                         for j in JANELAS if (mercado, j) in c.mults)
        linhas.append(f"  {estado} {_NOMES[mercado]}: previa +{a['previsto']:.1f} · real "
                      f"+{a['real']:.1f} · {efeito} com a correccao "
                      f"(teste em {a['n_teste']} momentos)")
        if a.get("suave"):
            ganho = (1 - a["erro_suave"] / a["erro_antigo"]) * 100 if a["erro_antigo"] else 0
            linhas.append(f"      ritmo suavizado (com o normal de um jogo): erro -{ganho:.0f}% "
                          "vs o calculo antigo")
        if fases and mercado in c.ativos:
            linhas.append(f"      multiplicador por fase: {fases}")
    return "\n".join(linhas)
