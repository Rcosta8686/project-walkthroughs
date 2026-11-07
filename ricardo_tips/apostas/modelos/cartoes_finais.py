"""Cartao nos minutos finais — o mercado de ouro do Ricardo.

Entre os 65' e os 82', num jogo tenso, a aposta e simples: OVER na linha actual
+ 0.5 amarelos (sai pelo menos mais 1 ate ao fim), e quando da, + 1.5 (mais 2).
Para enviar tem de haver:

  1. jogo tenso: diferenca <= 1 golo e pelo menos mais um sinal (faltas acima
     do normal, cartoes ja a sair, arbitro rigoroso, derbi, vermelho, equipa a
     perder obrigada a arriscar);
  2. duas fontes de acordo, as duas >= PROB_MIN:
       - o modelo live corrigido (ritmo do resto do jogo);
       - a historia: nos jogos guardados, em momentos parecidos (minuto,
         diferenca no marcador, nivel de faltas), quantas vezes saiu mais 1.
A probabilidade mostrada e a mais baixa das duas (conservador). Diz tambem que
equipa e mais provavel levar o proximo cartao (faltas e cartoes de cada uma).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

MIN_INICIO, MIN_FIM = 65, 82
FASES = (65, 70, 75, 80)
PROB_MIN = 0.70
MIN_HISTORIA = 40           # jogos parecidos para a historia contar
FALTAS_ALTAS_POR_90 = 27.0  # ~0.30 por minuto
CARTOES_QUENTES_POR_90 = 4.5
_MARGEM_ODD = 1.05          # so vale se a casa pagar 5% acima da odd justa


def fase_de(minuto: int) -> int | None:
    if minuto < MIN_INICIO or minuto > MIN_FIM:
        return None
    return max(f for f in FASES if minuto >= f)


def _dif(mc: int, mf: int) -> str:
    d = abs(int(mc) - int(mf))
    return "empate" if d == 0 else "1 golo" if d == 1 else "resolvido"


def _faltas(fc, ff, minuto: int) -> str:
    if fc is None or ff is None or minuto <= 0:
        return "?"
    return "altas" if (fc + ff) / minuto * 90 >= FALTAS_ALTAS_POR_90 else "normais"


def chaves(minuto: int, mc: int, mf: int, fc, ff) -> list[tuple]:
    """Do momento mais especifico ao mais geral (para a historia)."""
    f = fase_de(minuto)
    if f is None:
        return []
    d = _dif(mc, mf)
    return [(f, d, _faltas(fc, ff, minuto)), (f, d), (f,)]


# ─── Historia: em momentos parecidos, saiu mais 1 / mais 2? ─────────────

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

    def procurar(self, minuto: int, mc: int, mf: int, fc, ff) -> tuple[tuple, Taxa] | None:
        for k in chaves(minuto, mc, mf, fc, ff):
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
    finais = {int(e): float(s) for e, s, n, c in sess.execute(
        select(EEJ.sofascore_event_id, func.sum(EEJ.cards_amarelos), func.count(),
               func.count(EEJ.cards_amarelos))
        .where(EEJ.data_jogo >= desde - timedelta(days=1))
        .group_by(EEJ.sofascore_event_id)).all() if n == 2 and c == 2 and s is not None}
    hist = Historia()
    if not finais:
        return hist
    ids = sorted(finais)
    vistos: set[tuple[int, int]] = set()
    for i in range(0, len(ids), 900):
        rows = sess.execute(
            select(Snap.event_id, Snap.minuto, Snap.marcador_casa, Snap.marcador_fora,
                   Snap.yellow_casa, Snap.yellow_fora, Snap.fouls_casa, Snap.fouls_fora)
            .where(Snap.event_id.in_(ids[i:i + 900]), Snap.ts >= desde,
                   Snap.minuto >= MIN_INICIO, Snap.minuto <= MIN_FIM)
            .order_by(Snap.event_id, Snap.minuto)).all()
        for r in rows:
            f = fase_de(int(r.minuto))
            if f is None or (int(r.event_id), f) in vistos:
                continue
            vistos.add((int(r.event_id), f))
            agora_amarelos = int(r.yellow_casa or 0) + int(r.yellow_fora or 0)
            faltam = finais[int(r.event_id)] - agora_amarelos
            if faltam < -0.5:
                continue
            for k in chaves(int(r.minuto), int(r.marcador_casa or 0), int(r.marcador_fora or 0),
                            r.fouls_casa, r.fouls_fora):
                t = hist.taxas.setdefault(k, Taxa())
                t.n += 1
                t.mais1 += faltam >= 1
                t.mais2 += faltam >= 2
    return hist


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "cartoes_finais.json"


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


# ─── Decisao para um jogo ao vivo ───────────────────────────────────────

def _p_mais(lam: float, k: int) -> float:
    if lam <= 0:
        return 0.0
    return 1.0 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))


@dataclass
class SinalCartoesFim:
    minuto: int
    amarelos: int
    linha: float              # actual + 0.5
    p1: float                 # min(modelo, historia)
    p1_modelo: float
    p1_historia: float
    p2: float
    n_historia: int
    situacao: str             # descricao do grupo historico usado
    razoes: list[str]
    equipa: str               # mais provavel levar o proximo cartao
    p_equipa: float
    garantido: float = 0.0    # pior caso estatistico (Wilson) da taxa historica

    @property
    def odd_justa(self) -> float:
        return round(1 / self.p1, 2)

    @property
    def odd_minima(self) -> float:
        return round(_MARGEM_ODD / self.p1, 2)


def avaliar(estado, prev, historia: Historia, fator_arbitro: float = 1.0,
            derbi: bool = False) -> SinalCartoesFim | None:
    """None se o momento nao serve (fora da janela, jogo decidido, pouca tensao
    ou as duas fontes nao concordam)."""
    from apostas.relatorios.prime import wilson_inferior
    minuto = int(estado.minuto or 0)
    mc, mf = int(estado.marcador_casa or 0), int(estado.marcador_fora or 0)
    if fase_de(minuto) is None or abs(mc - mf) > 1:
        return None
    yc, yf = int(estado.yellow_cards_casa or 0), int(estado.yellow_cards_fora or 0)
    fc, ff = estado.fouls_casa, estado.fouls_fora
    vermelhos = int(estado.red_cards_casa or 0) + int(estado.red_cards_fora or 0)

    razoes = ["marcador apertado" if mc != mf else "empate — ninguem desiste"]
    extra = []
    if fc is not None and ff is not None and (fc + ff) / minuto * 90 >= FALTAS_ALTAS_POR_90:
        extra.append(f"faltas acima do normal ({fc + ff} ao {minuto}')")
    if (yc + yf) / minuto * 90 >= CARTOES_QUENTES_POR_90:
        extra.append(f"cartoes ja a sair ({yc + yf} amarelos)")
    if fator_arbitro >= 1.05:
        extra.append(f"arbitro rigoroso (+{(fator_arbitro - 1) * 100:.0f}% cartoes)")
    if derbi:
        extra.append("derbi")
    if vermelhos:
        extra.append("ja ha vermelho em campo")
    if mc != mf:
        a_perder = estado.home if mc < mf else estado.away
        extra.append(f"{a_perder} a perder: vai ter de arriscar")
    if not extra:
        return None
    razoes += extra

    lam = float(prev.lam_cartoes_amarelos_restantes)
    p1_modelo, p2_modelo = _p_mais(lam, 1), _p_mais(lam, 2)
    achado = historia.procurar(minuto, mc, mf, fc, ff)
    if achado is None:
        return None
    chave, taxa = achado
    p1 = min(p1_modelo, taxa.p1)
    if p1 < PROB_MIN:
        return None

    # Quem leva o proximo cartao: faltas e cartoes de cada equipa (+1 a cada).
    peso_c = (fc or 0) + 2 * yc + 1
    peso_f = (ff or 0) + 2 * yf + 1
    if (mc < mf):
        peso_c *= 1.15  # quem perde arrisca mais
    elif (mf < mc):
        peso_f *= 1.15
    share_c = peso_c / (peso_c + peso_f)
    pc, pf = _p_mais(lam * share_c, 1), _p_mais(lam * (1 - share_c), 1)
    equipa, p_eq = (estado.home, pc) if pc >= pf else (estado.away, pf)

    descr = {2: "{0}' · {1}", 3: "{0}' · {1} · faltas {2}", 1: "{0}'"}[len(chave)]
    return SinalCartoesFim(
        minuto=minuto, amarelos=yc + yf, linha=yc + yf + 0.5, p1=p1, p1_modelo=p1_modelo,
        p1_historia=taxa.p1, p2=min(p2_modelo, taxa.p2), n_historia=taxa.n,
        situacao=descr.format(*chave), razoes=razoes, equipa=equipa, p_equipa=p_eq,
        garantido=wilson_inferior(taxa.mais1, taxa.n))


def formatar_telegram(s: SinalCartoesFim, estado) -> str:
    linhas = [
        f"🟨⏱ *CARTAO NO FIM* · {estado.home} {estado.marcador_casa}-{estado.marcador_fora} "
        f"{estado.away}",
        f"🏆 {estado.liga} · {s.minuto}' · {s.amarelos} amarelos ate agora",
        "",
        f"🎯 *OVER {s.linha} amarelos* (sai pelo menos mais 1) · *{s.p1 * 100:.0f}%*",
    ]
    if s.p2 >= 0.40:
        linhas.append(f"   bonus: OVER {s.linha + 1} (mais 2) · {s.p2 * 100:.0f}%")
    linhas += [
        "",
        "🔥 *Porque este jogo:* " + "; ".join(s.razoes),
        f"📚 *Historia:* em {s.n_historia} jogos guardados em momento parecido "
        f"({s.situacao}), saiu mais 1 cartao em *{s.p1_historia * 100:.0f}%* "
        f"(garantido ≥ {s.garantido * 100:.0f}%)",
        f"🧠 *Modelo:* {s.p1_modelo * 100:.0f}% de sair mais 1 ate ao fim",
        f"👤 Mais provavel levar o proximo: *{s.equipa}* ({s.p_equipa * 100:.0f}%)",
    ]
    return "\n".join(linhas)


def formatar_historia(h: Historia) -> str:
    linhas = ["*Cartao no fim — historia* (sai mais 1 amarelo ate ao fim, nos jogos guardados)"]
    for f in FASES:
        partes = []
        for d in ("empate", "1 golo", "resolvido"):
            t = h.taxas.get((f, d))
            if t is not None and t.n >= 10:
                partes.append(f"{d} {t.p1 * 100:.0f}% (mais 2: {t.p2 * 100:.0f}%, n={t.n})")
        if partes:
            linhas.append(f"  {f}': " + " · ".join(partes))
    if len(linhas) == 1:
        linhas.append("  Ainda sem momentos suficientes.")
    return "\n".join(linhas)


def recalcular_e_gravar(sess, path: Path | None = None) -> Historia:
    h = historia_da_bd(sess)
    gravar(h, path)
    return h
