"""PRIME: so o tipo de dica que JA PROVOU dar lucro.

O objectivo e lucro, nao percentagem de acerto (acertar muito a odds baixas nao
paga). Agrupa as sugestoes ja resolvidas por (mercado, lado, fase do jogo, faixa
de probabilidade do modelo) e, em cada grupo, calcula o pior caso estatistico do
ROI (media - 1.96 desvios-padrao / raiz(n)). PRIME = pelo menos MIN_N casos e
esse pior caso acima de ROI_MINIMO: o lucro mantem-se mesmo descontando a sorte.

So contam sugestoes calculadas com o modelo actual (depois da ultima mudanca do
ritmo nesse mercado): a historia do modelo antigo nao diz nada do novo.

O lucro e medido a odd indicada na dica (1 unidade): so vale se a casa pagar
essa odd ou mais.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

ROI_MINIMO = 0.0     # pior caso do ROI tem de ficar acima disto
MIN_N = 50
Z = 1.96
FAIXAS = (0.55, 0.65, 0.75, 0.85, 0.95)   # inicio de cada faixa de probabilidade
FASES = (0, 15, 30, 45, 60, 75)


def wilson_inferior(ganhos: int, n: int, z: float = Z) -> float:
    if n <= 0:
        return 0.0
    p = ganhos / n
    centro = p + z * z / (2 * n)
    margem = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centro - margem) / (1 + z * z / n)


def chave(mercado: str, lado: str, minuto, prob) -> tuple | None:
    try:
        p, m = float(prob), int(float(minuto))
    except (TypeError, ValueError):
        return None
    if p < FAIXAS[0]:
        return None
    from apostas.alertas.corte_mercados import canonico
    faixa = max(f for f in FAIXAS if p >= f)
    fase = max(f for f in FASES if m >= f)
    return (canonico(mercado), (lado or "").lower(), fase, faixa)


@dataclass
class Grupo:
    n: int = 0
    ganhos: int = 0
    lucro: float = 0.0
    n_lucro: int = 0
    lucro2: float = 0.0   # soma dos quadrados (para o desvio-padrao)

    @property
    def taxa(self) -> float:
        return self.ganhos / self.n if self.n else 0.0

    @property
    def roi(self) -> float | None:
        return self.lucro / self.n_lucro if self.n_lucro else None

    @property
    def roi_pior_caso(self) -> float | None:
        if self.n_lucro < 2:
            return None
        media = self.lucro / self.n_lucro
        var = max(0.0, (self.lucro2 - self.n_lucro * media * media) / (self.n_lucro - 1))
        return media - Z * math.sqrt(var / self.n_lucro)

    @property
    def e_prime(self) -> bool:
        pior = self.roi_pior_caso
        return self.n_lucro >= MIN_N and pior is not None and pior > ROI_MINIMO


@dataclass
class Prime:
    grupos: dict[tuple, Grupo] = field(default_factory=dict)

    def grupo_prime(self, mercado: str, lado: str, minuto, prob) -> Grupo | None:
        k = chave(mercado, lado, minuto, prob)
        g = self.grupos.get(k) if k is not None else None
        return g if g is not None and g.e_prime else None


def calcular(rows: list[dict]) -> Prime:
    """rows: carregar_resolvidos (com minuto), ja filtradas pelo modelo actual."""
    pr = Prime()
    for r in rows:
        k = chave(r.get("mercado", ""), r.get("lado", ""), r.get("minuto"), r.get("prob"))
        if k is None:
            continue
        g = pr.grupos.setdefault(k, Grupo())
        g.n += 1
        g.ganhos += bool(r.get("ganhou"))
        if r.get("lucro") is not None:
            g.lucro += float(r["lucro"])
            g.lucro2 += float(r["lucro"]) ** 2
            g.n_lucro += 1
    return pr


def selo(g: Grupo) -> str:
    return (f"lucro confirmado: ROI {(g.roi or 0) * 100:+.0f}% em {g.n_lucro} casos iguais "
            f"(pior caso {(g.roi_pior_caso or 0) * 100:+.0f}%) · acerto {g.taxa * 100:.0f}% · "
            "so a odd indicada ou acima")


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "prime.json"


def gravar(pr: Prime, path: Path | None = None) -> None:
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([{"chave": list(k), "n": g.n, "ganhos": g.ganhos, "lucro": g.lucro,
                              "n_lucro": g.n_lucro, "lucro2": g.lucro2}
                             for k, g in pr.grupos.items()], ensure_ascii=False), encoding="utf-8")


def ler(path: Path | None = None) -> Prime:
    p = path or caminho()
    if not p.exists():
        return Prime()
    try:
        return Prime({tuple(x["chave"]): Grupo(x["n"], x["ganhos"], x["lucro"], x["n_lucro"],
                                               x.get("lucro2", 0.0))
                      for x in json.loads(p.read_text(encoding="utf-8"))})
    except (ValueError, KeyError, TypeError):
        return Prime()


_NOMES = {"remates": "💥 remates", "remates_a_baliza": "🎯 remates a baliza",
          "cantos": "🚩 cantos", "cartoes": "🟨 cartoes", "golos": "⚽ golos",
          "btts": "🔃 BTTS", "1x2": "🏆 1X2"}


def _descr(k: tuple) -> str:
    m, lado, fase, faixa = k
    return (f"{_NOMES.get(m, m)} {lado} · {fase}'-{fase + 15}' · modelo "
            f"{faixa * 100:.0f}-{min(100, faixa * 100 + 5):.0f}%")


def formatar(pr: Prime) -> str:
    linhas = [f"*💎 PRIME* (lucro confirmado: pior caso do ROI acima de 0 com {MIN_N}+ casos, "
              "so com o modelo actual)"]

    def linha(k, g, prefixo):
        pior = g.roi_pior_caso
        return (f"{prefixo} {_descr(k)}: ROI {(g.roi or 0) * 100:+.0f}% em {g.n_lucro} "
                f"(pior caso {(pior or 0) * 100:+.0f}%) · acerto {g.taxa * 100:.0f}%")
    primes = sorted(((k, g) for k, g in pr.grupos.items() if g.e_prime),
                    key=lambda kg: -(kg[1].roi_pior_caso or 0))
    for k, g in primes:
        linhas.append(linha(k, g, "  💎"))
    if not primes:
        linhas.append("  Ainda nenhum tipo de dica provou lucro com o modelo actual.")
    candidatos = sorted(((k, g) for k, g in pr.grupos.items()
                         if not g.e_prime and g.n_lucro >= 10 and (g.roi or 0) > 0),
                        key=lambda kg: -(kg[1].roi_pior_caso or -9))[:5]
    if candidatos:
        linhas.append("  A caminho (lucro na media, ainda sem casos para o confirmar):")
        for k, g in candidatos:
            linhas.append(linha(k, g, "   ·"))
    return "\n".join(linhas)


def recalcular_e_gravar(csv_path: Path | None = None, path: Path | None = None) -> Prime:
    from apostas.modelos import correcao_ritmo
    from apostas.relatorios.calibracao_alertas import carregar_resolvidos
    from apostas.utils.config import project_root
    csv_path = csv_path or project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    rows = correcao_ritmo.filtrar_desde_mudanca(carregar_resolvidos(csv_path),
                                                correcao_ritmo.ler())
    pr = calcular(rows)
    gravar(pr, path)
    return pr
