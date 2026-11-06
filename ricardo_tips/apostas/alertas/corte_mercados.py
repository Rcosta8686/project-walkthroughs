"""Corte automatico de mercados que estao a perder dinheiro.

Com os dados das ultimas semanas (13316 alertas resolvidos) so os remates
over, remates a baliza over, BTTS nao e golos over davam lucro; os UNDER, os
cartoes, os cantos over e o 1X2 perdiam. O modelo ja se corrige na
probabilidade (calibrador), mas a pergunta que interessa ao Ricardo e outra:
este mercado, ultimamente, ganha dinheiro?

Por (mercado, lado), com os alertas resolvidos dos ultimos JANELA_DIAS dias e
os mais recentes a pesar mais (meia-vida MEIA_VIDA_DIAS):
  - ROI ponderado <= ROI_CORTE com n efectivo >= MIN_N_EFF -> nao vai ao
    Telegram;
  - excepcao: uma liga com MIN_N_LIGA+ alertas e ROI >= ROI_LIGA_OK nesse
    (mercado, lado) continua a passar.

O scanner continua a gravar e o resolver a resolver TODAS as sugestoes (nao
so as enviadas): um mercado cortado continua a ser medido e volta sozinho
quando o ROI recente recuperar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

JANELA_DIAS = 30
MEIA_VIDA_DIAS = 14.0
MIN_N_EFF = 50.0
ROI_CORTE = -0.05
MIN_N_LIGA = 30
ROI_LIGA_OK = 0.05
_CANONICO = {"cards": "cartoes", "sot": "remates_a_baliza"}


def canonico(mercado: str) -> str:
    m = (mercado or "").strip().lower()
    return _CANONICO.get(m, m)


@dataclass
class Desempenho:
    n: int = 0
    n_eff: float = 0.0
    soma_lucro: float = 0.0  # ponderada

    @property
    def roi(self) -> float | None:
        return self.soma_lucro / self.n_eff if self.n_eff > 0 else None


@dataclass
class Cortes:
    por_mercado: dict[tuple[str, str], Desempenho] = field(default_factory=dict)
    por_liga: dict[tuple[str, str, str], Desempenho] = field(default_factory=dict)

    def cortado(self, mercado: str, lado: str) -> bool:
        d = self.por_mercado.get((canonico(mercado), (lado or "").lower()))
        return (d is not None and d.n_eff >= MIN_N_EFF and d.roi is not None
                and d.roi <= ROI_CORTE)

    def bloqueia(self, mercado: str, lado: str, liga: str) -> tuple[bool, str]:
        """(bloqueia, razao). A razao vai para o log."""
        m, l = canonico(mercado), (lado or "").lower()
        if not self.cortado(m, l):
            return False, ""
        dl = self.por_liga.get(((liga or "").strip().lower(), m, l))
        if dl is not None and dl.n >= MIN_N_LIGA and (dl.roi or 0) >= ROI_LIGA_OK:
            return False, f"excepcao: {liga} da ROI {dl.roi:+.0%} em {dl.n}"
        d = self.por_mercado[(m, l)]
        return True, f"{m} {l} a perder: ROI {d.roi:+.0%} nos ultimos {JANELA_DIAS} dias (n={d.n})"


def _data(r: dict) -> datetime | None:
    try:
        return datetime.fromisoformat((r.get("ts") or "")[:19])
    except ValueError:
        return None


def calcular(rows: list[dict], agora: datetime | None = None) -> Cortes:
    """rows: alertas resolvidos (carregar_resolvidos) com mercado, lado, lucro, ts, liga."""
    datas = [d for r in rows if (d := _data(r)) is not None]
    ref = agora or (max(datas) if datas else None)
    cortes = Cortes()
    if ref is None:
        return cortes
    inicio = ref - timedelta(days=JANELA_DIAS)
    for r in rows:
        d = _data(r)
        lucro = r.get("lucro")
        if d is None or lucro is None or d < inicio:
            continue
        w = 0.5 ** (max(0.0, (ref - d).total_seconds() / 86400) / MEIA_VIDA_DIAS)
        m, l = canonico(r.get("mercado", "")), (r.get("lado") or "").lower()
        liga = (r.get("liga") or "").strip().lower()
        for dsp in (cortes.por_mercado.setdefault((m, l), Desempenho()),
                    cortes.por_liga.setdefault((liga, m, l), Desempenho())):
            dsp.n += 1
            dsp.n_eff += w
            dsp.soma_lucro += w * float(lucro)
    return cortes


_NOMES = {"remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza",
          "cantos": "🚩 cantos", "cartoes": "🟨 cartoes", "golos": "⚽ golos",
          "btts": "🔃 BTTS", "1x2": "🏆 1X2"}


def formatar(cortes: Cortes) -> str:
    linhas = [f"*Corte automatico* (ultimos {JANELA_DIAS} dias, recentes pesam mais; "
              "o que esta cortado nao vai ao Telegram mas continua a ser medido):"]
    itens = sorted(((k, d) for k, d in cortes.por_mercado.items() if d.n >= 10),
                   key=lambda kd: -(kd[1].roi or 0))
    if not itens:
        return "\n".join(linhas + ["  Ainda sem dados recentes suficientes."])
    for (m, l), d in itens:
        if cortes.cortado(m, l):
            estado = "⛔ cortado"
        elif d.n_eff < MIN_N_EFF:
            estado = "· poucos dados"
        else:
            estado = "✅ passa"
        linhas.append(f"  {estado} {_NOMES.get(m, m)} {l}: ROI {d.roi:+.0%} (n={d.n})")
    return "\n".join(linhas)
