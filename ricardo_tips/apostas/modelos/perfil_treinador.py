"""Mini-cerebro do treinador: o que cada equipa faz na 2a parte.

Cada equipa (o seu treinador) reage ao resultado a sua maneira: ha quem, a
ganhar, recue para segurar e deixe de rematar; ha quem continue a atacar em
forca; ha quem, a perder, carregue tudo. Nos jogos guardados temos a 1a e a 2a
parte de cada equipa e o resultado ao intervalo, por isso mede-se:

  fator(equipa, mercado, situacao) = o que a equipa faz na 2a parte nessa
  situacao / o que uma equipa media faria (razao 2a/1a parte de todas as
  equipas na mesma situacao, aplicada a 1a parte tipica desta equipa).

1.30 = nessa situacao esta equipa faz 30% mais do que o normal na 2a parte.
Usa os ultimos N_JOGOS da equipa (o treinador actual, mais ou menos), puxado
para 1 com poucos jogos. Com `antes`, so jogos anteriores (medir sem batota).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

N_JOGOS = 20
MIN_JOGOS = 5
K_ENCOLHIMENTO = {"golos": 15.0, "cantos": 6.0, "cartoes": 6.0, "remates": 6.0,
                  "remates_a_baliza": 6.0}
FATOR_MIN, FATOR_MAX = 0.5, 1.8
_PARTES = {"golos": None, "cantos": ("cantos_1t", "cantos_2t"),
           "cartoes": ("cards_amarelos_1t", "cards_amarelos_2t"),
           "remates": ("remates_1t", "remates_2t"), "remates_a_baliza": ("sot_1t", "sot_2t")}
COLUNAS = ("sofascore_event_id", "sofascore_team_id", "data_jogo", "golos_marcados",
           "golos_sofridos", "golos_marcados_1t", "golos_sofridos_1t", "resultado_1t",
           "cantos_1t", "cantos_2t", "cards_amarelos_1t", "cards_amarelos_2t",
           "remates_1t", "remates_2t", "sot_1t", "sot_2t")
_NOMES = {"golos": "golos", "cantos": "cantos", "cartoes": "amarelos", "remates": "remates",
          "remates_a_baliza": "remates a baliza"}


@dataclass
class FatorTreinador:
    fator: float      # nesta situacao, vs uma equipa media
    n: int            # jogos desta equipa nesta situacao ao intervalo
    geral: float      # 2a parte em geral (todas as situacoes)


def _sit_intervalo(r) -> str | None:
    res = (getattr(r, "resultado_1t", None) or "").upper()
    if res in ("W", "D", "L"):
        return {"W": "a ganhar", "D": "empate", "L": "a perder"}[res]
    a, b = getattr(r, "golos_marcados_1t", None), getattr(r, "golos_sofridos_1t", None)
    if a is None or b is None:
        return None
    return "a ganhar" if a > b else "empate" if a == b else "a perder"


def _partes(r, mercado: str) -> tuple[float, float] | None:
    if mercado == "golos":
        g, g1 = getattr(r, "golos_marcados", None), getattr(r, "golos_marcados_1t", None)
        if g is None or g1 is None:
            return None
        return float(g1), float(g) - float(g1)
    colunas = _PARTES[mercado]
    if colunas is None:
        return None
    x1, x2 = getattr(r, colunas[0], None), getattr(r, colunas[1], None)
    if x1 is None or x2 is None:
        return None
    return float(x1), float(x2)


class PerfisTreinador:
    def __init__(self, linhas):
        self._jogos: dict[int, list[tuple[datetime, str, object]]] = {}
        somas: dict[tuple[str, str], list[float]] = {}
        for r in linhas:
            sit = _sit_intervalo(r)
            if sit is None or r.data_jogo is None:
                continue
            self._jogos.setdefault(int(r.sofascore_team_id), []).append((r.data_jogo, sit, r))
            for m in _PARTES:
                pt = _partes(r, m)
                if pt is not None:
                    acc = somas.setdefault((m, sit), [0.0, 0.0])
                    acc[0] += pt[0]
                    acc[1] += pt[1]
        for lista in self._jogos.values():
            lista.sort(key=lambda x: x[0], reverse=True)
        # Razao 2a/1a parte de uma equipa media em cada situacao ao intervalo.
        self.razao = {k: (v[1] / v[0] if v[0] > 0 else 1.0) for k, v in somas.items()}

    def fator(self, equipa: int | None, mercado: str, sit: str,
              antes: datetime | None = None, n: int = N_JOGOS) -> FatorTreinador | None:
        if equipa is None or mercado not in _PARTES:
            return None
        jogos = [(s, r) for d, s, r in self._jogos.get(int(equipa), [])
                 if antes is None or d < antes][:n]
        dados = [(s, pt) for s, r in jogos if (pt := _partes(r, mercado)) is not None]
        if len(dados) < MIN_JOGOS:
            return None
        base = sum(pt[0] for _, pt in dados) / len(dados)       # 1a parte tipica
        if base <= 0:
            base = 0.5
        k = K_ENCOLHIMENTO[mercado]
        esperado = [(s, self.razao.get((mercado, s), 1.0) * base, pt[1]) for s, pt in dados]
        e_med = sum(e for _, e, _ in esperado) / len(esperado)
        geral = (sum(y for _, _, y in esperado) + k * e_med) / (
            sum(e for _, e, _ in esperado) + k * e_med)
        nesta = [(e, y) for s, e, y in esperado if s == sit]
        if nesta:
            e_s = sum(e for e, _ in nesta) / len(nesta)
            f = (sum(y for _, y in nesta) + k * geral * e_s) / (sum(e for e, _ in nesta) + k * e_s)
        else:
            f = geral
        clamp = (lambda x: round(min(FATOR_MAX, max(FATOR_MIN, x)), 3))
        return FatorTreinador(fator=clamp(f), n=len(nesta), geral=clamp(geral))


def carregar_do_bd(sess, dias: int = 400) -> PerfisTreinador:
    """Todas as equipas (a razao media precisa de todas para comparar)."""
    from datetime import timedelta

    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.tempo import agora_utc
    desde = agora_utc() - timedelta(days=dias)
    cols = [getattr(EEJ, c) for c in COLUNAS]
    return PerfisTreinador(sess.execute(select(*cols).where(EEJ.data_jogo >= desde)).all())


def texto(nome: str, sit: str, mercado: str, f: FatorTreinador | None) -> str:
    """Frase curta quando o treinador desta equipa foge do normal."""
    if f is None or f.n < 4 or abs(f.fator - 1) < 0.12:
        return ""
    pct = (f.fator - 1) * 100
    if sit == "a ganhar":
        acao = "continua a atacar" if pct > 0 else "recua para segurar"
    elif sit == "a perder":
        acao = "carrega tudo" if pct > 0 else "desliga"
    else:
        acao = "acelera" if pct > 0 else "abranda"
    estado = {"empate": "empatado"}.get(sit, sit)
    return (f"{nome}, quando esta {estado} ao intervalo, {acao}: {pct:+.0f}% {_NOMES[mercado]} "
            f"na 2a parte vs uma equipa normal ({f.n} jogos)")
