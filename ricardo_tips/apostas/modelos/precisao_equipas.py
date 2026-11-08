"""Precisao por equipa: "Porto vai acabar com 10 remates a baliza, Benfica 3".

Em cada momento guardado de um jogo ja acabado, o modelo (tal como corre ao
vivo: versao escolhida + ritmo corrigido) dizia quanto cada equipa ia acabar
em golos, cantos, cartoes, remates e remates a baliza. Aqui compara-se com o
que acabou mesmo, todas as noites:

  - dentro de 1: a previsao final da equipa ficou a 1 ou menos do real;
  - erro medio por equipa e vies (previa a mais / a menos);
  - curva de aprendizagem: como estes numeros mudam noite apos noite.

E aprende sozinho, por cima do modelo, cinco camadas de cada equipa:
  1. situacao (a ganhar / empate / a perder): quem perde arrisca mais;
  2. treinador (perfil_treinador): o que ESTA equipa faz na 2a parte nessa
     situacao - recua para segurar ou continua a atacar;
  3. substituicoes: o treinador meteu gente para atacar ou para defender;
  4. a propria equipa: o erro que o modelo costuma ter com ela nos nossos
     jogos ao vivo (ha equipas que acabam sempre mais fortes do que parece);
  5. jogadores em campo: com um vermelho, a equipa com 10 remata e ganha
     cantos menos e a outra mais (o total ja contava os vermelhos, a divisao
     entre as equipas nao).
Cada camada so fica ligada num mercado se errar menos nos momentos mais
recentes, que nao usou para aprender. As que ainda nao tem dados esperam.

O "maximo possivel": o futebol tem acaso - mesmo sabendo o ritmo certo de
uma equipa, o numero final varia (Poisson). Para cada momento calcula-se a
probabilidade de acertar a ±1 com um modelo perfeito; a media e o teto
realista, e a distancia ate la e o que ainda ha para aprender.

Daqui sai o texto dos alertas: "Ate ao fim: Porto 10 (9-11) · Benfica 3 (2-4)",
com o intervalo dos erros reais observados (80% dos casos).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MERCADOS = ("golos", "cantos", "cartoes", "remates", "remates_a_baliza")
# Valor actual de cada equipa no estado ao vivo.
ACTUAL = {"golos": ("marcador_casa", "marcador_fora"),
          "cantos": ("corners_casa", "corners_fora"),
          "cartoes": ("yellow_cards_casa", "yellow_cards_fora"),
          "remates": ("shots_total_casa", "shots_total_fora"),
          "remates_a_baliza": ("shots_on_target_casa", "shots_on_target_fora")}
# Total final previsto por equipa na PrevisaoLive (actual + resto).
_PROJ = {"cantos": ("projeccao_cantos_casa", "projeccao_cantos_fora"),
         "remates": ("projeccao_shots_casa", "projeccao_shots_fora"),
         "remates_a_baliza": ("projeccao_sot_casa", "projeccao_sot_fora")}
# Total que falta (o do ritmo, ja medido) por mercado, na PrevisaoLive.
LAM = {"golos": "lam_golos_restantes", "cantos": "lam_cantos_restantes",
       "cartoes": "lam_cartoes_amarelos_restantes", "remates": "lam_shots_restantes",
       "remates_a_baliza": "lam_shots_on_target_restantes"}
VERSOES = ("antigo", "suave", "equipas")
_TOTAL_VERSAO = {"antigo": "previsto", "suave": "previsto_suave", "equipas": "previsto_equipas"}
SITUACOES = ("a ganhar", "empate", "a perder")
POSTURAS = ("ofensiva", "defensiva", "neutra")
NUMEROS = ("com menos", "com mais")      # jogadores em campo vs o adversario
K_ENCOLHIMENTO = 50
K_EQUIPA = 20                  # momentos ficticios por equipa (camada 4)
MULT_MIN, MULT_MAX = 0.6, 1.6
MIN_TESTE = 100
FRAC_TREINO = 0.7
MANTER_HISTORIA = 60
MINUTO_TREINADOR = 46          # o perfil do treinador e da 2a parte
NOMES = {"golos": "golos", "cantos": "cantos", "cartoes": "amarelos", "remates": "remates",
         "remates_a_baliza": "remates a baliza"}


def _n(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def quota_cartoes_casa(estado) -> float:
    """Que parte dos cartoes que faltam vai para a casa: faltas e cartoes de cada
    equipa ate agora (+3 a cada, para nao ir aos extremos) e quem perde arrisca."""
    pc = _n(getattr(estado, "fouls_casa", 0)) + 2 * _n(getattr(estado, "yellow_cards_casa", 0)) + 3
    pf = _n(getattr(estado, "fouls_fora", 0)) + 2 * _n(getattr(estado, "yellow_cards_fora", 0)) + 3
    mc, mf = _n(getattr(estado, "marcador_casa", 0)), _n(getattr(estado, "marcador_fora", 0))
    if mc < mf:
        pc *= 1.15
    elif mf < mc:
        pf *= 1.15
    return pc / (pc + pf)


def restantes(prev, estado, mercado: str) -> tuple[float, float]:
    """O que o modelo diz que falta a cada equipa (casa, fora)."""
    if mercado == "golos":
        return (max(0.0, _n(prev.lam_golos_casa_rest)), max(0.0, _n(prev.lam_golos_fora_rest)))
    if mercado == "cartoes":
        lam = max(0.0, _n(prev.lam_cartoes_amarelos_restantes))
        q = quota_cartoes_casa(estado)
        return lam * q, lam * (1 - q)
    pc, pf = _PROJ[mercado]
    ac, af = (_n(getattr(estado, a, 0)) for a in ACTUAL[mercado])
    return max(0.0, _n(getattr(prev, pc)) - ac), max(0.0, _n(getattr(prev, pf)) - af)


def situacao(marcador_equipa: float, marcador_outra: float) -> str:
    if marcador_equipa > marcador_outra:
        return "a ganhar"
    return "empate" if marcador_equipa == marcador_outra else "a perder"


def em_campo(vermelhos_equipa, vermelhos_outra) -> str | None:
    """'com menos' / 'com mais' jogadores que o adversario (None = iguais ou sem dados)."""
    if vermelhos_equipa is None or vermelhos_outra is None:
        return None
    if vermelhos_equipa > vermelhos_outra:
        return "com menos"
    return "com mais" if vermelhos_outra > vermelhos_equipa else None


def postura(ofensivas, defensivas) -> str | None:
    """O que o treinador foi buscar com as substituicoes (None = sem dados)."""
    if ofensivas is None or defensivas is None:
        return None
    if ofensivas > defensivas:
        return "ofensiva"
    return "defensiva" if defensivas > ofensivas else "neutra"


@dataclass
class Metricas:
    n: int = 0
    soma_erro: float = 0.0
    dentro1: int = 0
    exato: int = 0
    soma_vies: float = 0.0
    soma_teto: float = 0.0     # prob. de acertar a ±1 com um modelo perfeito

    def juntar(self, previsto_final: float, real_final: float, teto: float = 0.0) -> None:
        e = round(previsto_final) - real_final
        self.n += 1
        self.soma_erro += abs(previsto_final - real_final)
        self.dentro1 += int(abs(e) <= 1)
        self.exato += int(e == 0)
        self.soma_vies += previsto_final - real_final
        self.soma_teto += teto

    @property
    def teto(self) -> float:
        return self.soma_teto / self.n if self.n else 0.0

    @property
    def mae(self) -> float:
        return self.soma_erro / self.n if self.n else 0.0

    @property
    def taxa_dentro1(self) -> float:
        return self.dentro1 / self.n if self.n else 0.0

    @property
    def taxa_exato(self) -> float:
        return self.exato / self.n if self.n else 0.0

    @property
    def vies(self) -> float:
        return self.soma_vies / self.n if self.n else 0.0


@dataclass
class Precisao:
    mults: dict[str, dict[str, float]] = field(default_factory=dict)   # mercado -> situacao -> x
    ativos: list[str] = field(default_factory=list)                    # camada 1 ligada
    ativos_treinador: list[str] = field(default_factory=list)          # camada 2 ligada
    mults_postura: dict[str, dict[str, float]] = field(default_factory=dict)
    ativos_postura: list[str] = field(default_factory=list)            # camada 3 ligada
    mults_equipa: dict[str, dict[str, float]] = field(default_factory=dict)  # mercado -> id
    ativos_equipa: list[str] = field(default_factory=list)             # camada 4 ligada
    mults_numero: dict[str, dict[str, float]] = field(default_factory=dict)  # com menos/mais
    ativos_numero: list[str] = field(default_factory=list)             # camada 5 ligada
    ganhos: dict[str, dict[str, float]] = field(default_factory=dict)  # mercado -> camada -> %
    diagnostico: dict[str, dict[str, float]] = field(default_factory=dict)  # todas, ligadas ou nao
    # Como dividir o que falta entre as duas equipas: a versao do modelo que
    # divide melhor (pode nao ser a que acerta melhor o total).
    divisao: dict[str, str] = field(default_factory=dict)
    ganho_divisao: dict[str, float] = field(default_factory=dict)
    teste: dict[str, Metricas] = field(default_factory=dict)           # modelo final
    teste_sem: dict[str, Metricas] = field(default_factory=dict)       # sem as camadas
    por_fase: dict[str, dict[str, Metricas]] = field(default_factory=dict)
    intervalos: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    historia: list[dict] = field(default_factory=list)                 # curva de aprendizagem
    exemplos: list[str] = field(default_factory=list)
    calculado_em: str = ""

    def mult(self, mercado: str, sit: str) -> float:
        if mercado not in self.ativos:
            return 1.0
        return self.mults.get(mercado, {}).get(sit, 1.0)

    def mult_postura(self, mercado: str, post: str | None) -> float:
        if mercado not in self.ativos_postura or post is None:
            return 1.0
        return self.mults_postura.get(mercado, {}).get(post, 1.0)

    def mult_equipa(self, mercado: str, equipa) -> float:
        if mercado not in self.ativos_equipa or equipa is None:
            return 1.0
        return self.mults_equipa.get(mercado, {}).get(str(equipa), 1.0)

    def mult_numero(self, mercado: str, num: str | None) -> float:
        if mercado not in self.ativos_numero or num is None:
            return 1.0
        return self.mults_numero.get(mercado, {}).get(num, 1.0)

    def fator(self, mercado: str, sit: str, treinador: float = 1.0,
              post: str | None = None, equipa=None, num: str | None = None) -> float:
        t = treinador if mercado in self.ativos_treinador else 1.0
        return (self.mult(mercado, sit) * t * self.mult_postura(mercado, post)
                * self.mult_equipa(mercado, equipa) * self.mult_numero(mercado, num))

    def intervalo(self, mercado: str, minuto: int) -> tuple[float, float]:
        from apostas.modelos.correcao_ritmo import janela_de
        por_janela = self.intervalos.get(mercado, {})
        v = por_janela.get(str(janela_de(minuto))) or por_janela.get("todas") or [-1.0, 1.0]
        return float(v[0]), float(v[1])


# ─── Registos por equipa (os pares vem de correcao_ritmo.pares_da_bd) ─────

def _quota(p: dict, lado: str, versao: str) -> float:
    """Parte do que falta que vai para esta equipa, segundo uma versao."""
    c, f = p.get(f"c_{versao}"), p.get(f"f_{versao}")
    if c is None or f is None:
        c, f = p.get("c_antigo", 0.0), p.get("f_antigo", 0.0)
    s = c + f
    if s <= 0:
        return 0.5
    return (c if lado == "c" else f) / s


def _total(p: dict, versao: str) -> float:
    v = p.get(_TOTAL_VERSAO[versao])
    if v is None:  # pares sem total: soma das equipas dessa versao
        c, f = p.get(f"c_{versao}"), p.get(f"f_{versao}")
        if c is None or f is None:
            c, f = p.get("c_antigo", 0.0), p.get("f_antigo", 0.0)
        v = c + f
    return float(v)


def previsoes(pares: list[dict], ritmo, divisao: dict[str, str] | None = None) -> list[dict]:
    """Um registo por (momento, mercado, equipa): o total que falta do modelo
    actual (versao escolhida x multiplicador do ritmo) dividido pelas duas
    equipas segundo `divisao` (por defeito a mesma versao), e o real."""
    out = []
    for p in pares:
        if "c_real" not in p:
            continue
        m = p["mercado"]
        base = ritmo.bases.get(m, "antigo")
        total = _total(p, base) * ritmo.mult(m, p["janela"])
        versao_div = (divisao or {}).get(m, base)
        for lado, outro in (("c", "f"), ("f", "c")):
            out.append({
                "mercado": m, "janela": p["janela"], "data": p.get("data"),
                "sit": situacao(p[f"m{lado}"], p[f"m{outro}"]),
                "atual": p[f"{lado}_atual"], "real": p[f"{lado}_real"],
                "resto": total * _quota(p, lado, versao_div),
                "treinador": p.get(f"{lado}_treinador", 1.0) or 1.0,
                "post": p.get(f"{lado}_postura"),
                "num": em_campo(p.get(f"{lado}_vermelhos"), p.get(f"{outro}_vermelhos")),
                "team": (str(p[f"{lado}_id"]) if p.get(f"{lado}_id") is not None else None),
                "equipa": p.get("home" if lado == "c" else "away"),
                "event_id": p.get("event_id"), "lado": lado})
    return out


def _treinar(regs: list[dict], chave: str, fator_previo,
             k: float = K_ENCOLHIMENTO) -> dict[str, dict[str, float]]:
    soma: dict[tuple[str, str], list[float]] = {}
    for r in regs:
        if r.get(chave) is None:
            continue
        acc = soma.setdefault((r["mercado"], r[chave]), [0.0, 0.0])
        acc[0] += r["real"]
        acc[1] += r["resto"] * fator_previo(r)
    out: dict[str, dict[str, float]] = {}
    for (m, valor), (real, prev) in soma.items():
        if prev <= 0:
            continue
        x = (real + k) / (prev + k)   # encolhido para 1
        out.setdefault(m, {})[valor] = round(min(MULT_MAX, max(MULT_MIN, x)), 3)
    return out


def p_dentro1(atual: float, lam: float) -> float:
    """Probabilidade de o final ficar a ±1 da previsao se o ritmo estiver certo
    e o resto for so acaso (Poisson): o teto de um modelo perfeito."""
    lam = max(lam, 1e-9)
    alvo = math.floor(atual + lam + 0.5)
    total = 0.0
    for k in range(max(0, int(alvo - 1 - atual)), int(alvo + 1 - atual) + 1):
        if abs(atual + k - alvo) <= 1:
            total += math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))
    return min(1.0, total)


def _metricas(regs: list[dict], fator) -> dict[str, Metricas]:
    out: dict[str, Metricas] = {}
    for r in regs:
        lam = r["resto"] * fator(r)
        out.setdefault(r["mercado"], Metricas()).juntar(r["atual"] + lam, r["atual"] + r["real"],
                                                      p_dentro1(r["atual"], lam))
    return out


def _quantil(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _ganho(sem: Metricas | None, com: Metricas | None) -> float | None:
    """Fraccao a menos de erro; None se nao ha casos de teste suficientes."""
    if sem is None or com is None or sem.n < MIN_TESTE or not sem.mae:
        return None
    return 1 - com.mae / sem.mae


def calcular(pares: list[dict], ritmo, anterior: Precisao | None = None,
             agora: datetime | None = None) -> Precisao:
    agora = agora or datetime.now()
    p = Precisao(calculado_em=agora.isoformat(timespec="seconds"),
                 historia=list((anterior.historia if anterior else [])[-MANTER_HISTORIA:]))

    def um(_r) -> float:
        return 1.0

    def ordenar(rs: list[dict]) -> list[dict]:
        return sorted(rs, key=lambda r: str(r["data"] or ""))
    # 0. Que versao divide melhor o jogo entre as duas equipas (nos mais recentes).
    erro_div: dict[str, dict[str, Metricas]] = {}
    for v in VERSOES:
        rs = ordenar(previsoes(pares, ritmo, {m: v for m in MERCADOS}))
        erro_div[v] = _metricas(rs[int(len(rs) * FRAC_TREINO):], um)
    for m in MERCADOS:
        atual = ritmo.bases.get(m, "antigo")
        ref = erro_div.get(atual, {}).get(m)
        if ref is None or ref.n < MIN_TESTE:
            continue
        melhor = min(VERSOES, key=lambda v: erro_div[v][m].mae if m in erro_div[v] else 1e9)
        g = 1 - erro_div[melhor][m].mae / ref.mae if ref.mae else 0.0
        if melhor != atual and g > 0.01:
            p.divisao[m] = melhor
            p.ganho_divisao[m] = round(g, 4)
    regs = ordenar(previsoes(pares, ritmo, p.divisao))
    if not regs:
        return p
    # Aprende nos mais antigos, testa nos mais recentes que nao viu.
    corte = int(len(regs) * FRAC_TREINO)
    treino, teste = regs[:corte], regs[corte:]
    p.teste_sem = _metricas(teste, um)
    # Camada 1: situacao.
    m1 = _treinar(treino, "sit", um)

    def f1(r) -> float:
        return m1.get(r["mercado"], {}).get(r["sit"], 1.0) if r["mercado"] in p.ativos else 1.0
    com1 = _metricas(teste, lambda r: m1.get(r["mercado"], {}).get(r["sit"], 1.0))
    for m in MERCADOS:
        g = _ganho(p.teste_sem.get(m), com1.get(m))
        if g is not None:
            p.diagnostico.setdefault(m, {})["situacao"] = round(g, 4)
        if g is not None and g > 0.01:
            p.ativos.append(m)
            p.ganhos.setdefault(m, {})["situacao"] = round(g, 4)
    # Camada 2: treinador (so na 2a parte; o registo traz 1.0 na 1a).
    base2 = _metricas(teste, f1)
    com2 = _metricas(teste, lambda r: f1(r) * r["treinador"])
    for m in MERCADOS:
        g = _ganho(base2.get(m), com2.get(m))
        if g is not None:
            p.diagnostico.setdefault(m, {})["treinador"] = round(g, 4)
        regs_m = [r for r in teste if r["mercado"] == m and r["janela"] >= 45]
        if regs_m:  # em quantos momentos da 2a parte havia perfil do treinador
            p.diagnostico.setdefault(m, {})["treinador_cobertura"] = round(
                sum(1 for r in regs_m if r["treinador"] != 1.0) / len(regs_m), 3)
        if g is not None and g > 0.01:
            p.ativos_treinador.append(m)
            p.ganhos.setdefault(m, {})["treinador"] = round(g, 4)

    def f2(r) -> float:
        return f1(r) * (r["treinador"] if r["mercado"] in p.ativos_treinador else 1.0)
    # Camada 3: substituicoes (so momentos que ja as tem guardadas).
    m3 = _treinar(treino, "post", f2)
    com_post = [r for r in teste if r["post"] is not None]
    base3 = _metricas(com_post, f2)
    com3 = _metricas(com_post, lambda r: f2(r) * m3.get(r["mercado"], {}).get(r["post"], 1.0))
    for m in MERCADOS:
        g = _ganho(base3.get(m), com3.get(m))
        if g is not None:
            p.diagnostico.setdefault(m, {})["substituicoes"] = round(g, 4)
        p.diagnostico.setdefault(m, {})["substituicoes_n"] = float(
            sum(1 for r in com_post if r["mercado"] == m))
        if g is not None and g > 0.01:
            p.ativos_postura.append(m)
            p.ganhos.setdefault(m, {})["substituicoes"] = round(g, 4)

    def f3(r) -> float:
        return f2(r) * (m3.get(r["mercado"], {}).get(r["post"], 1.0)
                        if r["mercado"] in p.ativos_postura and r["post"] else 1.0)
    # Camada 4: a propria equipa (o erro que costumamos ter com ela).
    m4 = _treinar(treino, "team", f3, k=K_EQUIPA)
    base4 = _metricas(teste, f3)
    com4 = _metricas(teste, lambda r: f3(r) * m4.get(r["mercado"], {}).get(r["team"] or "", 1.0))
    for m in MERCADOS:
        g = _ganho(base4.get(m), com4.get(m))
        if g is not None:
            p.diagnostico.setdefault(m, {})["equipa"] = round(g, 4)
        if g is not None and g > 0.01:
            p.ativos_equipa.append(m)
            p.ganhos.setdefault(m, {})["equipa"] = round(g, 4)

    def f4(r) -> float:
        return f3(r) * (m4.get(r["mercado"], {}).get(r["team"] or "", 1.0)
                        if r["mercado"] in p.ativos_equipa else 1.0)
    # Camada 5: jogadores em campo (so os momentos com um vermelho a mais de um lado).
    m5 = _treinar(treino, "num", f4)
    com_num = [r for r in teste if r.get("num")]
    base5 = _metricas(com_num, f4)
    com5 = _metricas(com_num, lambda r: f4(r) * m5.get(r["mercado"], {}).get(r["num"], 1.0))
    for m in MERCADOS:
        g = _ganho(base5.get(m), com5.get(m))
        if g is not None:
            p.diagnostico.setdefault(m, {})["numero"] = round(g, 4)
        p.diagnostico.setdefault(m, {})["numero_n"] = float(
            sum(1 for r in com_num if r["mercado"] == m))
        if g is not None and g > 0.01:
            p.ativos_numero.append(m)
            p.ganhos.setdefault(m, {})["numero"] = round(g, 4)

    # Multiplicadores finais com todos os dados, pela mesma ordem.
    p.mults = _treinar(regs, "sit", um)
    p.mults_postura = _treinar(regs, "post",
                               lambda r: p.mult(r["mercado"], r["sit"])
                               * (r["treinador"] if r["mercado"] in p.ativos_treinador else 1.0))
    p.mults_equipa = {m: v for m, v in _treinar(
        regs, "team", lambda r: p.fator(r["mercado"], r["sit"], r["treinador"], r["post"]),
        k=K_EQUIPA).items() if m in p.ativos_equipa}
    p.mults_numero = {m: v for m, v in _treinar(
        regs, "num",
        lambda r: p.fator(r["mercado"], r["sit"], r["treinador"], r["post"], r["team"])).items()
        if m in p.ativos_numero}

    def final(r) -> float:
        return p.fator(r["mercado"], r["sit"], r["treinador"], r["post"], r["team"],
                       r.get("num"))
    p.teste = _metricas(teste, final)
    for r in teste:
        lam = r["resto"] * final(r)
        p.por_fase.setdefault(r["mercado"], {}).setdefault(str(r["janela"]), Metricas()).juntar(
            r["atual"] + lam, r["atual"] + r["real"], p_dentro1(r["atual"], lam))
    # Intervalos de 80% dos erros reais (real - previsto), por mercado e fase.
    erros: dict[str, dict[str, list[float]]] = {}
    for r in regs:
        e = r["real"] - r["resto"] * final(r)
        erros.setdefault(r["mercado"], {}).setdefault(str(r["janela"]), []).append(e)
        erros[r["mercado"]].setdefault("todas", []).append(e)
    p.intervalos = {m: {j: [round(_quantil(xs, 0.1), 2), round(_quantil(xs, 0.9), 2)]
                        for j, xs in js.items() if len(xs) >= 30 or j == "todas"}
                    for m, js in erros.items()}
    hoje = agora.date().isoformat()
    p.historia = [h for h in p.historia if h.get("data") != hoje] + [{
        "data": hoje, **{m: {"dentro1": round(x.taxa_dentro1, 4), "mae": round(x.mae, 3),
                             "teto": round(x.teto, 4), "n": x.n}
                         for m, x in p.teste.items()}}]
    p.exemplos = _exemplos(regs, final)
    return p


def _exemplos(regs: list[dict], fator, n: int = 4) -> list[str]:
    """Os jogos mais recentes: o que se previa aos 60' para cada equipa vs o real."""
    por_jogo: dict[tuple, dict[str, dict]] = {}
    for r in regs:
        if r["janela"] != 60 or r["mercado"] != "remates_a_baliza":
            continue
        por_jogo.setdefault((str(r["data"])[:16], r["event_id"]), {})[r["lado"]] = r
    out = []
    for _, lados in sorted(por_jogo.items(), key=lambda x: x[0][0], reverse=True):
        if len(lados) != 2:
            continue
        partes, ok = [], True
        for lado in ("c", "f"):
            r = lados[lado]
            prev = math.floor(r["atual"] + r["resto"] * fator(r) + 0.5)
            real = round(r["atual"] + r["real"])
            ok = ok and abs(prev - real) <= 1
            partes.append(f"{r['equipa']} previ {prev}, foi {real}")
        out.append(("✅ " if ok else "❌ ") + " · ".join(partes))
        if len(out) >= n:
            break
    return out


# ─── Ao vivo ───────────────────────────────────────────────────────────────

def prever_final(estado, prev, precisao: Precisao, mercado: str,
                 treinador: tuple[float, float] = (1.0, 1.0),
                 versoes: dict | None = None) -> dict | None:
    """Previsao final de cada equipa com intervalo: {"casa": (v, lo, hi), "fora": ...}.
    `treinador`: fator do perfil do treinador de cada equipa (2a parte).
    `versoes`: PrevisaoLive de cada versao ("antigo"/"suave"/"equipas"), para
    dividir o total pelas equipas como a medicao mostrou que divide melhor."""
    if mercado not in MERCADOS:
        return None
    from apostas.ingestao.sofascore_live import dados_disponiveis
    if not dados_disponiveis(estado, mercado):
        return None
    total = max(0.0, _n(getattr(prev, LAM[mercado], 0.0)))
    prev_div = (versoes or {}).get(precisao.divisao.get(mercado, ""), prev)
    qc, qf = restantes(prev_div, estado, mercado)
    quota_c = qc / (qc + qf) if qc + qf > 0 else 0.5
    rc, rf = total * quota_c, total * (1 - quota_c)
    mc, mf = _n(estado.marcador_casa), _n(estado.marcador_fora)
    lo, hi = precisao.intervalo(mercado, int(estado.minuto or 0))
    segunda = int(estado.minuto or 0) >= MINUTO_TREINADOR
    out = {}
    for nome, i, resto, sit, equipa in (
            ("casa", 0, rc, situacao(mc, mf), getattr(estado, "home_id", None)),
            ("fora", 1, rf, situacao(mf, mc), getattr(estado, "away_id", None))):
        post = postura(getattr(estado, f"subs_ofensivas_{nome}", None),
                       getattr(estado, f"subs_defensivas_{nome}", None))
        outro = "fora" if nome == "casa" else "casa"
        num = em_campo(getattr(estado, f"red_cards_{nome}", None),
                       getattr(estado, f"red_cards_{outro}", None))
        atual = _n(getattr(estado, ACTUAL[mercado][i], 0))
        r = resto * precisao.fator(mercado, sit, treinador[i] if segunda else 1.0, post, equipa,
                                   num)
        out[nome] = (atual + r, atual + max(0.0, r + lo), atual + max(0.0, r + hi))
    return out


def _intervalo_txt(v: float, lo: float, hi: float) -> str:
    c = math.floor(v + 0.5)
    a, b = min(math.floor(lo + 0.5), c), max(math.floor(hi + 0.5), c)
    return f"{c}" if a == b else f"{c} ({a}-{b})"


def texto_equipas(estado, prev, precisao: Precisao, mercado: str,
                  treinador: tuple[float, float] = (1.0, 1.0),
                  versoes: dict | None = None) -> str:
    """'📏 Ate ao fim: Porto 10 (9-11) · Benfica 3 (2-4) remates a baliza'."""
    pf = prever_final(estado, prev, precisao, mercado, treinador, versoes)
    if not pf:
        return ""
    m = precisao.teste.get(mercado)
    fiab = (f" — acertamos ±1 em {m.taxa_dentro1 * 100:.0f}% das equipas"
            if m is not None and m.n >= MIN_TESTE else "")
    vc, vf = int(_n(getattr(estado, "red_cards_casa", 0))), int(_n(getattr(estado, "red_cards_fora", 0)))
    dez = ""
    if vc != vf:   # quem joga com menos (a divisao entre as equipas ja o conta, se ajudar)
        dez = f" · 🟥 {estado.home if vc > vf else estado.away} com {11 - abs(vc - vf)}"
    return (f"📏 Ate ao fim: {estado.home} {_intervalo_txt(*pf['casa'])} · {estado.away} "
            f"{_intervalo_txt(*pf['fora'])} {NOMES[mercado]}{fiab}{dez}")


# ─── Guardar / ler / relatorio ─────────────────────────────────────────────

def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "precisao_equipas.json"


def gravar(p: Precisao, path: Path | None = None) -> None:
    path = path or caminho()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(p), ensure_ascii=False, indent=1), encoding="utf-8")


def ler(path: Path | None = None) -> Precisao:
    path = path or caminho()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Precisao(
            mults=d.get("mults", {}), ativos=d.get("ativos", []),
            ativos_treinador=d.get("ativos_treinador", []),
            mults_postura=d.get("mults_postura", {}), ativos_postura=d.get("ativos_postura", []),
            mults_equipa=d.get("mults_equipa", {}), ativos_equipa=d.get("ativos_equipa", []),
            mults_numero=d.get("mults_numero", {}), ativos_numero=d.get("ativos_numero", []),
            ganhos=d.get("ganhos", {}), diagnostico=d.get("diagnostico", {}),
            divisao=d.get("divisao", {}), ganho_divisao=d.get("ganho_divisao", {}),
            teste={m: Metricas(**x) for m, x in d.get("teste", {}).items()},
            teste_sem={m: Metricas(**x) for m, x in d.get("teste_sem", {}).items()},
            por_fase={m: {j: Metricas(**x) for j, x in js.items()}
                      for m, js in d.get("por_fase", {}).items()},
            intervalos=d.get("intervalos", {}), historia=d.get("historia", []),
            exemplos=d.get("exemplos", []), calculado_em=d.get("calculado_em", ""))
    except (OSError, ValueError, TypeError, KeyError):
        return Precisao()


def recalcular_e_gravar(pares: list[dict], ritmo, path: Path | None = None) -> Precisao:
    p = calcular(pares, ritmo, anterior=ler(path))
    gravar(p, path)
    return p


_NOME_CAMADA = {"situacao": "situacao (a ganhar/empate/a perder)",
                "treinador": "treinador de cada equipa na 2a parte",
                "substituicoes": "substituicoes ofensivas/defensivas",
                "equipa": "erro proprio de cada equipa",
                "numero": "jogadores em campo (vermelhos)"}


_NOME_VERSAO = {"antigo": "calculo antigo", "suave": "normal generico",
                "equipas": "normal de cada equipa (mini-cerebros)"}


def _diagnostico_txt(p: Precisao, m: str) -> str:
    d = p.diagnostico.get(m, {})
    partes = []
    for camada, nome in (("situacao", "situacao"), ("treinador", "treinador"),
                         ("substituicoes", "substituicoes"), ("equipa", "equipa"),
                         ("numero", "vermelhos")):
        if camada == "substituicoes" and not d.get("substituicoes_n"):
            partes.append("substituicoes: a juntar dados")
            continue
        if camada == "numero" and d.get("numero") is None:
            partes.append(f"vermelhos: a juntar dados ({d.get('numero_n', 0):.0f} momentos)")
            continue
        g = d.get(camada)
        if g is None:
            partes.append(f"{nome}: poucos dados")
            continue
        estado = "✅" if g > 0.01 else "ainda nao ajuda"
        cob = ""
        if camada == "treinador" and "treinador_cobertura" in d:
            cob = f", perfil em {d['treinador_cobertura'] * 100:.0f}% dos momentos da 2a parte"
        partes.append(f"{nome} {-g * 100:+.1f}% {estado}{cob}")
    return " · ".join(partes)


def formatar(p: Precisao) -> str:
    linhas = ["*📏 Precisao por equipa* (quanto cada equipa ia acabar vs o que acabou; "
              "testado nos momentos mais recentes)"]
    if not p.teste:
        return "\n".join(linhas + ["  Ainda sem momentos de jogos acabados suficientes."])
    antes = p.historia[0] if len(p.historia) >= 2 else None
    for m in MERCADOS:
        x = p.teste.get(m)
        if x is None or not x.n:
            continue
        vies = ("previa a mais" if x.vies > 0.25 else "previa a menos" if x.vies < -0.25
                else "sem vies")
        evol = ""
        if antes and m in antes:
            d = (x.taxa_dentro1 - antes[m]["dentro1"]) * 100
            evol = f" · desde {antes['data'][5:]}: {d:+.0f}pp"
        teto = f" (maximo possivel ~{x.teto * 100:.0f}%)" if x.teto else ""
        linhas.append(f"  {NOMES[m]}: dentro de ±1 em *{x.taxa_dentro1 * 100:.0f}%* das equipas"
                      f"{teto} · exato {x.taxa_exato * 100:.0f}% · erro medio {x.mae:.1f} · "
                      f"{vies} ({x.vies:+.1f}) · n={x.n}{evol}")
        fases = p.por_fase.get(m, {})
        if fases:
            linhas.append("      por fase: " + " ".join(
                f"{j}' {f.taxa_dentro1 * 100:.0f}%" + (f"/{f.teto * 100:.0f}" if f.teto else "")
                for j, f in sorted(fases.items(), key=lambda kv: int(kv[0])) if f.n >= 20))
        if m in p.divisao:
            linhas.append(f"      divisao entre as equipas: {_NOME_VERSAO[p.divisao[m]]} "
                          f"(erro -{p.ganho_divisao.get(m, 0) * 100:.0f}%)")
        linhas.append("      camadas: " + _diagnostico_txt(p, m))
        for camada, g in p.ganhos.get(m, {}).items():
            extra = ""
            if camada == "situacao":
                mm = p.mults.get(m, {})
                extra = ": " + " ".join(f"{s} ×{mm[s]:.2f}" for s in SITUACOES if s in mm)
            elif camada == "substituicoes":
                mm = p.mults_postura.get(m, {})
                extra = ": " + " ".join(f"{s} ×{mm[s]:.2f}" for s in POSTURAS if s in mm)
            elif camada == "equipa":
                mm = p.mults_equipa.get(m, {})
                extra = f" ({len(mm)} equipas aprendidas)"
            elif camada == "numero":
                mm = p.mults_numero.get(m, {})
                extra = ": " + " ".join(f"{s} ×{mm[s]:.2f}" for s in NUMEROS if s in mm)
            linhas.append(f"      + {_NOME_CAMADA[camada]} (erro -{g * 100:.0f}%){extra}")
    linhas.append("  (por fase: acerto/maximo possivel. Camadas por ligar ficam a aprender: so "
                  "entram quando erram menos nos momentos que nao viram.)")
    if p.exemplos:
        linhas.append("  Ultimos jogos (remates a baliza, previsao aos 60'):")
        linhas += [f"    {e}" for e in p.exemplos]
    return "\n".join(linhas)
