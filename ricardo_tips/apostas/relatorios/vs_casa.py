"""Nos vs casas: o nosso modelo preve melhor do que as casas de apostas?

Quando o scanner grava uma dica nova, guarda tambem a odd da casa para a mesma
linha, no mesmo minuto (Sofascore ao vivo): dados/tracking/odds_casa.csv.
Depois do jogo (resolver), cada dica tem a nossa probabilidade, a da casa (sem
a margem) e o resultado. Todas as noites mede:

  - quem previu melhor: erro medio (Brier; mais baixo = melhor);
  - quando discordamos da casa, quem acertou, e o lucro a odd da casa
    (e so ai que ha dinheiro: concordar com a casa nao paga a margem);
  - mistura aprendida (regressao logistica nos + casa): se o peso do nosso
    modelo e positivo com confianca, acrescentamos informacao que a casa
    nao tem.

So informacao: a odd nunca bloqueia uma dica.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

CAMPOS = ["ts_scan", "event_id", "minuto", "liga", "mercado", "lado", "linha",
          "prob_nos", "odd_casa", "odd_outro", "p_casa"]
MARGEM_UM_LADO = 1.05   # sem a odd do outro lado: margem tipica de uma casa
FAIXAS_DIF = ((-1.01, -0.03, "abaixo da casa"), (-0.03, 0.03, "igual a casa"),
              (0.03, 0.10, "3 a 10pp acima da casa"), (0.10, 1.01, "10pp+ acima da casa"))
MIN_SEGMENTO = 30
MIN_MISTURA = 200
_OPOSTO = {"over": "under", "under": "over", "sim": "nao", "nao": "sim"}


def caminho_odds() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "odds_casa.csv"


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "vs_casa.json"


def p_sem_margem(odd: float | None, odd_outro: float | None = None) -> float | None:
    """Probabilidade que a casa da, tirando a margem (com os 2 lados se houver)."""
    if not odd or odd <= 1.0:
        return None
    if odd_outro and odd_outro > 1.0:
        a, b = 1 / odd, 1 / odd_outro
        return a / (a + b)
    return min(0.99, (1 / odd) / MARGEM_UM_LADO)


def _linha_txt(linha) -> str:
    try:
        return f"{float(linha):g}"
    except (TypeError, ValueError):
        return ""


def anexar_odds(s: dict, odds) -> bool:
    """Junta a odd da casa (e do outro lado) a uma dica. True se encontrou."""
    from apostas.ingestao import odds_live
    lado = str(s.get("lado") or "").lower()
    odd = odds_live.procurar(odds, s.get("mercado") or "", lado, s.get("linha"))
    if not odd:
        return False
    outro = odds_live.procurar(odds, s.get("mercado") or "", _OPOSTO.get(lado, ""),
                               s.get("linha"))
    s["odd_casa"], s["odd_outro"] = odd, outro
    s["p_casa"] = p_sem_margem(odd, outro)
    return s["p_casa"] is not None


def registar(sugs: list[dict], path: Path | None = None) -> int:
    path = path or caminho_odds()
    path.parent.mkdir(parents=True, exist_ok=True)
    novo = not path.exists()
    n = 0
    with path.open("a", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=CAMPOS, extrasaction="ignore")
        if novo:
            w.writeheader()
        for s in sugs:
            if s.get("p_casa") is None:
                continue
            w.writerow({**s, "linha": _linha_txt(s.get("linha")),
                        "prob_nos": round(float(s["prob_modelo"]), 4),
                        "p_casa": round(float(s["p_casa"]), 4)})
            n += 1
    return n


def _chave(row: dict) -> tuple:
    return (str(row.get("event_id") or "").strip(), str(row.get("ts_scan") or "")[:19],
            str(row.get("mercado") or "").strip().lower(),
            str(row.get("lado") or "").strip().lower(), _linha_txt(row.get("linha")))


def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def carregar_pares(csv_sugestoes: Path | None = None, csv_odds: Path | None = None,
                   desde: datetime | None = None) -> list[dict]:
    """Dicas resolvidas (W/L) que tem odd da casa: nossa prob, da casa, resultado."""
    from apostas.alertas.corte_mercados import canonico
    from apostas.utils.config import project_root
    csv_sugestoes = csv_sugestoes or (project_root() / "dados" / "tracking"
                                      / "sugestoes_live_v2.csv")
    csv_odds = csv_odds or caminho_odds()
    if not csv_odds.exists() or not csv_sugestoes.exists():
        return []
    casa: dict[tuple, dict] = {}
    with csv_odds.open(encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            casa[_chave(r)] = r
    out = []
    with csv_sugestoes.open(encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            res = (r.get("resultado") or "").strip()
            if res not in ("W", "L"):
                continue
            c = casa.get(_chave(r))
            if c is None:
                continue
            p_nos, p_casa, odd = _f(c.get("prob_nos")), _f(c.get("p_casa")), _f(c.get("odd_casa"))
            if p_nos is None or p_casa is None or odd is None:
                continue
            ts = str(r.get("ts_scan") or "")[:19]
            if desde is not None and ts < desde.isoformat(timespec="seconds"):
                continue
            out.append({"mercado": canonico(r.get("mercado") or ""),
                        "lado": (r.get("lado") or "").strip().lower(),
                        "minuto": _f(r.get("minuto")), "p_nos": p_nos, "p_casa": p_casa,
                        "odd": odd, "ganhou": res == "W", "ts": ts})
    return out


@dataclass
class Comparacao:
    n: int = 0
    erro_nos: float = 0.0     # soma dos erros ao quadrado
    erro_casa: float = 0.0

    def juntar(self, p_nos: float, p_casa: float, y: float) -> None:
        self.n += 1
        self.erro_nos += (p_nos - y) ** 2
        self.erro_casa += (p_casa - y) ** 2

    @property
    def brier_nos(self) -> float:
        return self.erro_nos / self.n if self.n else 0.0

    @property
    def brier_casa(self) -> float:
        return self.erro_casa / self.n if self.n else 0.0

    @property
    def vantagem(self) -> float:
        """> 0: erramos menos do que a casa (fraccao)."""
        return 1 - self.brier_nos / self.brier_casa if self.brier_casa else 0.0


@dataclass
class Segmento:
    n: int = 0
    ganhos: int = 0
    soma_p_nos: float = 0.0
    soma_p_casa: float = 0.0
    lucro: float = 0.0
    lucro2: float = 0.0

    def juntar(self, p: dict) -> None:
        lucro = (p["odd"] - 1) if p["ganhou"] else -1.0
        self.n += 1
        self.ganhos += int(p["ganhou"])
        self.soma_p_nos += p["p_nos"]
        self.soma_p_casa += p["p_casa"]
        self.lucro += lucro
        self.lucro2 += lucro * lucro

    @property
    def acerto(self) -> float:
        return self.ganhos / self.n if self.n else 0.0

    @property
    def p_casa_media(self) -> float:
        return self.soma_p_casa / self.n if self.n else 0.0

    @property
    def p_nos_media(self) -> float:
        return self.soma_p_nos / self.n if self.n else 0.0

    @property
    def roi(self) -> float:
        return self.lucro / self.n if self.n else 0.0

    @property
    def roi_pior_caso(self) -> float:
        if self.n < 2:
            return -1.0
        var = max(0.0, self.lucro2 / self.n - self.roi ** 2) * self.n / (self.n - 1)
        return self.roi - 1.96 * math.sqrt(var / self.n)


@dataclass
class Mistura:
    """p = sigmoide(b + w_nos*logit(nos) + w_casa*logit(casa)), aprendida."""
    n: int
    b: float
    w_nos: float
    w_casa: float
    ep_nos: float          # erro padrao do peso do nosso modelo

    @property
    def acrescenta(self) -> bool:
        return self.w_nos - 1.96 * self.ep_nos > 0

    def prob(self, p_nos: float, p_casa: float) -> float:
        z = self.b + self.w_nos * _logit(p_nos) + self.w_casa * _logit(p_casa)
        return 1 / (1 + math.exp(-z))


def _logit(p: float) -> float:
    p = min(0.99, max(0.01, p))
    return math.log(p / (1 - p))


def faixa_de(dif: float) -> str:
    for ini, fim, nome in FAIXAS_DIF:
        if ini <= dif < fim:
            return nome
    return FAIXAS_DIF[-1][2]


def ajustar_mistura(pares: list[dict]) -> Mistura | None:
    if len(pares) < MIN_MISTURA:
        return None
    import numpy as np
    x = np.array([[1.0, _logit(p["p_nos"]), _logit(p["p_casa"])] for p in pares])
    y = np.array([1.0 if p["ganhou"] else 0.0 for p in pares])
    w = np.zeros(3)
    reg = np.diag([0.0, 1e-3, 1e-3]) * len(pares)
    h = reg
    for _ in range(25):  # Newton-Raphson
        p = 1 / (1 + np.exp(-(x @ w)))
        g = x.T @ (p - y) + reg @ w
        h = (x * (p * (1 - p))[:, None]).T @ x + reg
        passo = np.linalg.solve(h, g)
        w -= passo
        if float(np.abs(passo).max()) < 1e-7:
            break
    try:
        ep = float(np.sqrt(np.linalg.inv(h)[1, 1]))
    except np.linalg.LinAlgError:
        return None
    return Mistura(n=len(pares), b=float(w[0]), w_nos=float(w[1]), w_casa=float(w[2]),
                   ep_nos=ep)


@dataclass
class VsCasa:
    geral: Comparacao = field(default_factory=Comparacao)
    por_mercado: dict[str, Comparacao] = field(default_factory=dict)
    segmentos: dict[str, dict[str, Segmento]] = field(default_factory=dict)  # mercado|todos
    mistura: Mistura | None = None
    calculado_em: str = ""

    def segmento(self, mercado: str, dif: float) -> tuple[str, Segmento] | None:
        """O grupo historico desta situacao: o do mercado se tiver casos, senao todos."""
        from apostas.alertas.corte_mercados import canonico
        faixa = faixa_de(dif)
        for grupo in (canonico(mercado), "todos"):
            seg = self.segmentos.get(grupo, {}).get(faixa)
            if seg is not None and seg.n >= MIN_SEGMENTO:
                return ("" if grupo == "todos" else grupo), seg
        return None


def calcular(pares: list[dict], agora: datetime | None = None) -> VsCasa:
    vs = VsCasa(calculado_em=(agora or datetime.now()).isoformat(timespec="seconds"))
    for p in pares:
        y = 1.0 if p["ganhou"] else 0.0
        vs.geral.juntar(p["p_nos"], p["p_casa"], y)
        vs.por_mercado.setdefault(p["mercado"], Comparacao()).juntar(p["p_nos"], p["p_casa"], y)
        faixa = faixa_de(p["p_nos"] - p["p_casa"])
        for grupo in (p["mercado"], "todos"):
            vs.segmentos.setdefault(grupo, {}).setdefault(faixa, Segmento()).juntar(p)
    vs.mistura = ajustar_mistura(pares)
    return vs


def gravar(vs: VsCasa, path: Path | None = None) -> None:
    path = path or caminho()
    path.parent.mkdir(parents=True, exist_ok=True)
    dados = {"calculado_em": vs.calculado_em, "geral": asdict(vs.geral),
             "por_mercado": {m: asdict(c) for m, c in vs.por_mercado.items()},
             "segmentos": {g: {f: asdict(s) for f, s in fs.items()}
                           for g, fs in vs.segmentos.items()},
             "mistura": asdict(vs.mistura) if vs.mistura else None}
    path.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def ler(path: Path | None = None) -> VsCasa:
    path = path or caminho()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return VsCasa()
    try:
        return VsCasa(
            geral=Comparacao(**d.get("geral", {})),
            por_mercado={m: Comparacao(**c) for m, c in d.get("por_mercado", {}).items()},
            segmentos={g: {f: Segmento(**s) for f, s in fs.items()}
                       for g, fs in d.get("segmentos", {}).items()},
            mistura=Mistura(**d["mistura"]) if d.get("mistura") else None,
            calculado_em=d.get("calculado_em", ""))
    except TypeError:
        return VsCasa()


def recalcular_e_gravar(csv_sugestoes: Path | None = None, csv_odds: Path | None = None,
                        path: Path | None = None) -> VsCasa:
    vs = calcular(carregar_pares(csv_sugestoes, csv_odds))
    gravar(vs, path)
    return vs


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def texto_alerta(vs: VsCasa, mercado: str, odd_casa: float, p_casa: float,
                 p_nos: float) -> str:
    """Linha para o alerta: o que a casa da, o que nos damos e, se ja houver
    historia, o que aconteceu quando discordamos assim."""
    dif = p_nos - p_casa
    txt = (f"Casa *{odd_casa:.2f}* (da {_pct(p_casa)}) · nos *{_pct(p_nos)}* "
           f"({dif * 100:+.0f}pp)")
    achado = vs.segmento(mercado, dif)
    if achado is not None:
        grupo, seg = achado
        txt += (f"\n📚 Quando estivemos {faixa_de(dif)}{' em ' + grupo if grupo else ''}: "
                f"acertamos {_pct(seg.acerto)} (casa dava {_pct(seg.p_casa_media)}) · "
                f"lucro {seg.roi * 100:+.0f}% a odd da casa em {seg.n} dicas")
    return txt


def formatar(vs: VsCasa) -> str:
    linhas = ["*🏦 Nos vs Casas* (dicas com a odd da casa no mesmo minuto, ja resolvidas)"]
    g = vs.geral
    if not g.n:
        return "\n".join(linhas + [
            "  Ainda sem dicas resolvidas com odd da casa. O scanner guarda a odd da casa "
            "em cada dica nova; os numeros aparecem a partir das proximas noites."])
    quem = "batemos a casa" if g.vantagem > 0 else "a casa ainda preve melhor"
    linhas.append(f"  Erro medio (mais baixo = melhor): nos {g.brier_nos:.3f} · casa "
                  f"{g.brier_casa:.3f} → {quem} ({abs(g.vantagem) * 100:.1f}%) em {g.n} dicas")
    for m, c in sorted(vs.por_mercado.items(), key=lambda x: -x[1].n):
        if c.n < 20:
            continue
        marca = "✅" if c.vantagem > 0 else "❌"
        linhas.append(f"    {marca} {m}: nos {c.brier_nos:.3f} vs casa {c.brier_casa:.3f} "
                      f"(n={c.n})")
    linhas.append("  Lucro a odd da casa, conforme discordamos dela:")
    for _, _, faixa in FAIXAS_DIF:
        s = vs.segmentos.get("todos", {}).get(faixa)
        if s is None or not s.n:
            continue
        prova = " 💎 lucro provado" if s.n >= 50 and s.roi_pior_caso > 0 else ""
        linhas.append(f"    {faixa}: acerto {_pct(s.acerto)} (casa dava {_pct(s.p_casa_media)}, "
                      f"nos {_pct(s.p_nos_media)}) · ROI {s.roi * 100:+.1f}% (n={s.n}){prova}")
    mx = vs.mistura
    if mx is None:
        linhas.append(f"  Mistura aprendida: precisa de {MIN_MISTURA}+ dicas (ha {g.n}).")
    else:
        veredicto = ("✅ o nosso modelo acrescenta informacao que a casa nao tem"
                     if mx.acrescenta else
                     "ainda nao prova que acrescenta informacao a casa")
        linhas.append(f"  Mistura aprendida: peso nosso {mx.w_nos:.2f} (±{1.96 * mx.ep_nos:.2f}) "
                      f"· peso casa {mx.w_casa:.2f} → {veredicto}")
    return "\n".join(linhas)
