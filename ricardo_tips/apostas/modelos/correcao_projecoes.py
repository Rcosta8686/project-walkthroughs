"""Correccao das projeccoes live (40'/55'/70') com o resultado final real.

Cada projeccao enviada fica em ProjecaoLive com o valor no momento e o total
projectado (bruto). Quando o jogo acaba, EstatisticaEquipaJogo tem o total
real de cada equipa. Por (mercado, janela) aprende-se:

    mult = soma(real - actual) / soma(projectado - actual)

ou seja, quanto o modelo erra nos eventos QUE FALTAM. mult > 1 significa que
o resto do jogo produz mais do que o modelo espera (o que a calibracao dos
alertas indicou: UNDER sobrestimados). O scanner multiplica o ritmo restante
por mult antes de projectar.

Pares recentes pesam mais (meia-vida de 14 dias) e mult e encolhido para 1
com poucos pares: mult = w*r + (1-w), w = n_eff / (n_eff + 30).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from apostas.utils.schema import EstatisticaEquipaJogo, ProjecaoLive

MIN_PARES = 20
K_ENCOLHIMENTO = 30
MULT_MIN, MULT_MAX = 0.6, 1.8
MEIA_VIDA_DIAS = 14.0
_COLUNA = {
    "sot": EstatisticaEquipaJogo.sot,
    "cantos": EstatisticaEquipaJogo.cantos,
    "remates": EstatisticaEquipaJogo.remates,
    "cartoes": EstatisticaEquipaJogo.cards_amarelos,  # resolve o 'cartao no fim'
}


@dataclass
class CorrecaoProjecao:
    mercado: str
    janela: int | None      # 40/55/70; None = todas as janelas juntas
    n: int
    resto_projectado: float  # media de eventos que faltavam, segundo o modelo
    resto_real: float        # media de eventos que faltavam, na realidade
    mult: float


def pares_projecao_real(sess) -> list[dict]:
    """Projeccoes cujo jogo ja tem as estatisticas finais das 2 equipas."""
    pares: list[dict] = []
    for mercado, coluna in _COLUNA.items():
        q = (
            select(ProjecaoLive.minuto, ProjecaoLive.valor_atual_total,
                   ProjecaoLive.projecao_final_total, ProjecaoLive.data_envio,
                   func.sum(coluna), func.count(coluna))
            .join(EstatisticaEquipaJogo,
                  EstatisticaEquipaJogo.sofascore_event_id == ProjecaoLive.sofascore_event_id)
            .where(ProjecaoLive.mercado == mercado)
            .group_by(ProjecaoLive.id)
        )
        for janela, actual, proj, data, real, n_equipas in sess.execute(q).all():
            if n_equipas != 2 or real is None:
                continue
            if real < actual - 0.5:  # estatistica final incoerente com o que ja se via
                continue
            pares.append({"mercado": mercado, "janela": janela, "actual": actual,
                          "proj": proj, "real": float(real), "data": data})
    return pares


def _peso(data: datetime | None, ref: datetime | None) -> float:
    if data is None or ref is None:
        return 1.0
    return 0.5 ** (max(0.0, (ref - data).total_seconds() / 86400) / MEIA_VIDA_DIAS)


def treinar_correcoes(pares: list[dict]) -> dict[tuple[str, int | None], CorrecaoProjecao]:
    datas = [p["data"] for p in pares if p.get("data") is not None]
    ref = max(datas) if datas else None
    grupos: dict[tuple[str, int | None], list[dict]] = {}
    for p in pares:
        grupos.setdefault((p["mercado"], p["janela"]), []).append(p)
        grupos.setdefault((p["mercado"], None), []).append(p)

    out: dict[tuple[str, int | None], CorrecaoProjecao] = {}
    for chave, g in grupos.items():
        if len(g) < MIN_PARES:
            continue
        pesos = [_peso(p.get("data"), ref) for p in g]
        resto_proj = sum(w * max(0.0, p["proj"] - p["actual"]) for w, p in zip(pesos, g))
        resto_real = sum(w * max(0.0, p["real"] - p["actual"]) for w, p in zip(pesos, g))
        if resto_proj <= 0:
            continue
        n_eff = sum(pesos)
        w = n_eff / (n_eff + K_ENCOLHIMENTO)
        mult = min(MULT_MAX, max(MULT_MIN, w * (resto_real / resto_proj) + (1 - w)))
        out[chave] = CorrecaoProjecao(
            mercado=chave[0], janela=chave[1], n=len(g),
            resto_projectado=resto_proj / n_eff, resto_real=resto_real / n_eff,
            mult=round(mult, 3),
        )
    return out


def multiplicador(correcoes: dict, mercado: str, janela: int) -> float:
    c = correcoes.get((mercado, janela)) or correcoes.get((mercado, None))
    return c.mult if c is not None else 1.0


_NOME = {"sot": "🎯 remates a baliza", "cantos": "🚩 cantos", "remates": "💥 remates"}


def formatar_resumo(correcoes: dict) -> str:
    linhas = ["*Projeccoes 40'/55'/70'* (o que faltava jogar: projectado vs real)"]
    por_janela = sorted((c for c in correcoes.values() if c.janela is not None),
                        key=lambda c: (c.mercado, c.janela))
    if not por_janela:
        return "\n".join(linhas + [
            f"  Ainda nao ha {MIN_PARES}+ projeccoes com resultado final por mercado."])
    for c in por_janela:
        linhas.append(
            f"  {_NOME.get(c.mercado, c.mercado)} {c.janela}': projectava +{c.resto_projectado:.1f}"
            f" · real +{c.resto_real:.1f} → correccao x{c.mult:.2f} (n={c.n})")
    return "\n".join(linhas)
