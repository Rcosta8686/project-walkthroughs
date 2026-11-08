"""Mais golos: "vao sair mais golos neste jogo" (pelo menos mais 1 ate ao fim).

Entre os 55' e os 80', a aposta e OVER na linha actual + 0.5 golos (e, quando
da, + 1.5: mais 2). Como no cartao no fim, para enviar tem de haver:

  1. jogo com pressao: pelo menos um sinal (remates acima do normal, remates a
     baliza a sair, xG acima dos golos - os golos estao atrasados -, grandes
     oportunidades, equipa a perder por 1 obrigada a arriscar, vermelho em
     campo, substituicoes ofensivas);
  2. duas fontes de acordo, as duas >= PROB_MIN:
       - o modelo live corrigido (golos que faltam, ritmo do resto do jogo);
       - a historia: nos jogos guardados, em momentos parecidos (minuto,
         diferenca no marcador, ritmo de remates), quantas vezes saiu mais 1.
A probabilidade mostrada e a mais baixa das duas (conservador). Diz tambem que
equipa e mais provavel marcar o proximo.

A historia e recalculada todas as noites com os jogos novos (aprende sozinha).
Fora do alerta proprio, linha_mais_golos() da uma linha curta para os outros
alertas na 2a parte: "⚽ Mais golos: 74% de sair pelo menos mais 1".
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

MIN_INICIO, MIN_FIM = 55, 80
FASES = (55, 60, 65, 70, 75, 80)
PROB_MIN = 0.70
MIN_HISTORIA = 40            # momentos parecidos para a historia contar
REMATES_ALTOS_POR_90 = 26.0  # as duas equipas juntas
SOT_ALTOS_POR_90 = 9.0
XG_ATRASADO = 0.8            # xG - golos: oportunidades por converter
LINHA_MIN_MINUTO = 46        # a linha curta nos outros alertas: so na 2a parte
LINHA_PROB_MIN = 0.65


def fase_de(minuto: int) -> int | None:
    if minuto < MIN_INICIO or minuto > MIN_FIM:
        return None
    return max(f for f in FASES if minuto >= f)


def _dif(mc: int, mf: int) -> str:
    d = abs(int(mc) - int(mf))
    return "empate" if d == 0 else "1 golo" if d == 1 else "2+ golos"


def _ritmo(rc, rf, minuto: int) -> str:
    if rc is None or rf is None or minuto <= 0:
        return "?"
    return "muitos remates" if (rc + rf) / minuto * 90 >= REMATES_ALTOS_POR_90 else "normal"


def chaves(minuto: int, mc: int, mf: int, rc, rf) -> list[tuple]:
    """Do momento mais especifico ao mais geral (para a historia)."""
    f = fase_de(minuto)
    if f is None:
        return []
    d = _dif(mc, mf)
    return [(f, d, _ritmo(rc, rf, minuto)), (f, d), (f,)]


# ─── Historia: em momentos parecidos, saiu mais 1 / mais 2 golos? ──────────

@dataclass
class Taxa:
    n: int = 0
    mais1: int = 0
    mais2: int = 0

    @property
    def p1(self) -> float:
        return self.mais1 / self.n if self.n else 0.0

    @property
    def p2(self) -> float:
        return self.mais2 / self.n if self.n else 0.0


@dataclass
class Historia:
    taxas: dict[tuple, Taxa] = field(default_factory=dict)

    def procurar(self, minuto: int, mc: int, mf: int, rc, rf) -> tuple[tuple, Taxa] | None:
        for k in chaves(minuto, mc, mf, rc, rf):
            t = self.taxas.get(k)
            if t is not None and t.n >= MIN_HISTORIA:
                return k, t
        return None


def historia_da_bd(sess, agora: datetime | None = None, dias: int = 150) -> Historia:
    """Um momento por (jogo, fase): o 1o snapshot dessa fase, com o final."""
    from sqlalchemy import func, select

    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.tempo import agora_utc

    agora = agora or agora_utc()
    desde = agora - timedelta(days=dias)
    finais = {int(e): float(s) for e, s, n in sess.execute(
        select(EEJ.sofascore_event_id, func.sum(EEJ.golos_marcados), func.count())
        .where(EEJ.data_jogo >= desde - timedelta(days=1))
        .group_by(EEJ.sofascore_event_id)).all() if n == 2 and s is not None}
    hist = Historia()
    if not finais:
        return hist
    ids = sorted(finais)
    vistos: set[tuple[int, int]] = set()
    for i in range(0, len(ids), 900):
        rows = sess.execute(
            select(Snap.event_id, Snap.minuto, Snap.marcador_casa, Snap.marcador_fora,
                   Snap.shots_total_casa, Snap.shots_total_fora)
            .where(Snap.event_id.in_(ids[i:i + 900]), Snap.ts >= desde,
                   Snap.minuto >= MIN_INICIO, Snap.minuto <= MIN_FIM)
            .order_by(Snap.event_id, Snap.minuto)).all()
        for r in rows:
            f = fase_de(int(r.minuto))
            if f is None or (int(r.event_id), f) in vistos:
                continue
            vistos.add((int(r.event_id), f))
            mc, mf = int(r.marcador_casa or 0), int(r.marcador_fora or 0)
            faltam = finais[int(r.event_id)] - (mc + mf)
            if faltam < -0.5:      # final incoerente com o marcador que se via
                continue
            for k in chaves(int(r.minuto), mc, mf, r.shots_total_casa, r.shots_total_fora):
                t = hist.taxas.setdefault(k, Taxa())
                t.n += 1
                t.mais1 += faltam >= 1
                t.mais2 += faltam >= 2
    return hist


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "golos_finais.json"


def gravar(h: Historia, path: Path | None = None) -> None:
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([{"chave": list(k), "n": t.n, "mais1": t.mais1, "mais2": t.mais2}
                             for k, t in h.taxas.items()], ensure_ascii=False), encoding="utf-8")


def ler(path: Path | None = None) -> Historia:
    p = path or caminho()
    if not p.exists():
        return Historia()
    try:
        return Historia({tuple(x["chave"]): Taxa(x["n"], x["mais1"], x["mais2"])
                         for x in json.loads(p.read_text(encoding="utf-8"))})
    except (ValueError, KeyError, TypeError):
        return Historia()


def recalcular_e_gravar(sess, path: Path | None = None) -> Historia:
    h = historia_da_bd(sess)
    gravar(h, path)
    return h


# ─── Decisao para um jogo ao vivo ───────────────────────────────────────

def _p_mais(lam: float, k: int) -> float:
    if lam <= 0:
        return 0.0
    return 1.0 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def proximo_golo(estado, prev) -> tuple[str, float]:
    """Que equipa e mais provavel marcar o proximo (golos que faltam a cada uma)."""
    lc = max(0.0, _f(getattr(prev, "lam_golos_casa_rest", 0)) or 0.0)
    lf = max(0.0, _f(getattr(prev, "lam_golos_fora_rest", 0)) or 0.0)
    if lc + lf <= 0:
        return estado.home, 0.5
    return (estado.home, lc / (lc + lf)) if lc >= lf else (estado.away, lf / (lc + lf))


def razoes_pressao(estado) -> list[str]:
    """Sinais de que o jogo vai dar mais golos (vazio = sem pressao)."""
    minuto = max(1, int(estado.minuto or 0))
    mc, mf = int(estado.marcador_casa or 0), int(estado.marcador_fora or 0)
    out = []
    rc, rf = _f(estado.shots_total_casa), _f(estado.shots_total_fora)
    if rc is not None and rf is not None and (rc + rf) / minuto * 90 >= REMATES_ALTOS_POR_90:
        out.append(f"remates acima do normal ({rc + rf:.0f} ao {minuto}')")
    sc, sf = _f(estado.shots_on_target_casa), _f(estado.shots_on_target_fora)
    if sc is not None and sf is not None and (sc + sf) / minuto * 90 >= SOT_ALTOS_POR_90:
        out.append(f"remates a baliza a sair ({sc + sf:.0f})")
    xc, xf = _f(getattr(estado, "xg_casa", None)), _f(getattr(estado, "xg_fora", None))
    if xc is not None and xf is not None and xc + xf - (mc + mf) >= XG_ATRASADO:
        out.append(f"golos atrasados face as oportunidades (xG {xc + xf:.1f} vs {mc + mf} golos)")
    bc, bf = _f(getattr(estado, "big_chances_casa", None)), _f(getattr(estado, "big_chances_fora", None))
    if bc is not None and bf is not None and bc + bf >= 3:
        out.append(f"{bc + bf:.0f} grandes oportunidades")
    if abs(mc - mf) == 1:
        a_perder = estado.home if mc < mf else estado.away
        out.append(f"{a_perder} a perder por 1: vai ter de arriscar")
    if int(estado.red_cards_casa or 0) + int(estado.red_cards_fora or 0):
        out.append("vermelho em campo: mais espaco")
    ofensivas = (int(getattr(estado, "subs_ofensivas_casa", 0) or 0)
                 + int(getattr(estado, "subs_ofensivas_fora", 0) or 0))
    if ofensivas:
        out.append(f"{ofensivas} substituicao(oes) para atacar")
    return out


@dataclass
class SinalMaisGolos:
    minuto: int
    golos: int
    linha: float              # actual + 0.5
    p1: float                 # min(modelo, historia)
    p1_modelo: float
    p1_historia: float
    p2: float
    n_historia: int
    situacao: str             # descricao do grupo historico usado
    razoes: list[str]
    equipa: str               # mais provavel marcar o proximo
    p_equipa: float
    garantido: float = 0.0    # pior caso estatistico (Wilson) da taxa historica

    @property
    def odd_justa(self) -> float:
        return round(1 / self.p1, 2)


def avaliar(estado, prev, historia: Historia) -> SinalMaisGolos | None:
    """None se o momento nao serve (fora da janela, sem pressao, ou as duas
    fontes nao concordam em pelo menos PROB_MIN)."""
    from apostas.relatorios.prime import wilson_inferior
    minuto = int(estado.minuto or 0)
    if fase_de(minuto) is None:
        return None
    razoes = razoes_pressao(estado)
    if not razoes:
        return None
    mc, mf = int(estado.marcador_casa or 0), int(estado.marcador_fora or 0)
    lam = max(0.0, _f(getattr(prev, "lam_golos_restantes", 0)) or 0.0)
    p1_modelo, p2_modelo = _p_mais(lam, 1), _p_mais(lam, 2)
    achado = historia.procurar(minuto, mc, mf, estado.shots_total_casa, estado.shots_total_fora)
    if achado is None:
        return None
    chave, taxa = achado
    p1 = min(p1_modelo, taxa.p1)
    if p1 < PROB_MIN:
        return None
    equipa, p_eq = proximo_golo(estado, prev)
    descr = {3: "{0}' · {1} · {2}", 2: "{0}' · {1}", 1: "{0}'"}[len(chave)]
    return SinalMaisGolos(
        minuto=minuto, golos=mc + mf, linha=mc + mf + 0.5, p1=p1, p1_modelo=p1_modelo,
        p1_historia=taxa.p1, p2=min(p2_modelo, taxa.p2), n_historia=taxa.n,
        situacao=descr.format(*chave), razoes=razoes, equipa=equipa, p_equipa=p_eq,
        garantido=wilson_inferior(taxa.mais1, taxa.n))


def formatar_telegram(s: SinalMaisGolos, estado) -> str:
    linhas = [
        f"⚽⏱ *VAO SAIR MAIS GOLOS* · {estado.home} {estado.marcador_casa}-"
        f"{estado.marcador_fora} {estado.away}",
        f"🏆 {estado.liga} · {s.minuto}' · {s.golos} golos ate agora",
        "",
        f"🎯 *OVER {s.linha} golos* (sai pelo menos mais 1) · *{s.p1 * 100:.0f}%* "
        f"· odd justa {s.odd_justa:.2f}",
    ]
    if s.p2 >= 0.40:
        linhas.append(f"   bonus: OVER {s.linha + 1} (mais 2) · {s.p2 * 100:.0f}%")
    linhas += [
        "",
        "🔥 *Porque este jogo:* " + "; ".join(s.razoes),
        f"📚 *Historia:* em {s.n_historia} jogos guardados em momento parecido "
        f"({s.situacao}), saiu mais 1 golo em *{s.p1_historia * 100:.0f}%* "
        f"(garantido ≥ {s.garantido * 100:.0f}%)",
        f"🧠 *Modelo:* {s.p1_modelo * 100:.0f}% de sair mais 1 ate ao fim",
        f"👤 Mais provavel marcar o proximo: *{s.equipa}* ({s.p_equipa * 100:.0f}%)",
    ]
    return "\n".join(linhas)


def linha_mais_golos(estado, prev) -> str:
    """Linha curta para os outros alertas (2a parte, modelo >= 65%)."""
    if prev is None or int(estado.minuto or 0) < LINHA_MIN_MINUTO:
        return ""
    lam = max(0.0, _f(getattr(prev, "lam_golos_restantes", 0)) or 0.0)
    p = _p_mais(lam, 1)
    if p < LINHA_PROB_MIN:
        return ""
    equipa, p_eq = proximo_golo(estado, prev)
    golos = int(estado.marcador_casa or 0) + int(estado.marcador_fora or 0)
    return (f"⚽ Mais golos: *{p * 100:.0f}%* de sair pelo menos mais 1 (over {golos + 0.5}) "
            f"· {equipa} mais provavel ({p_eq * 100:.0f}%)")


def formatar_historia(h: Historia) -> str:
    linhas = ["*Mais golos — historia* (sai mais 1 golo ate ao fim, nos jogos guardados)"]
    for f in FASES:
        partes = []
        for d in ("empate", "1 golo", "2+ golos"):
            t = h.taxas.get((f, d))
            if t is not None and t.n >= 10:
                partes.append(f"{d} {t.p1 * 100:.0f}% (mais 2: {t.p2 * 100:.0f}%, n={t.n})")
        if partes:
            linhas.append(f"  {f}': " + " · ".join(partes))
    if len(linhas) == 1:
        linhas.append("  Ainda sem momentos suficientes.")
    return "\n".join(linhas)
