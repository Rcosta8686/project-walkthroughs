"""Cartao no fim e mais golos aprendem com o que ja enviaram.

Cada um destes alertas diz uma probabilidade ("sai mais 1: 78%"). Todas as
noites junta-se o que foi enviado nos ultimos DIAS dias e ja tem resultado:

  - quantos foram, quantos acertaram e a probabilidade media que demos;
  - a correccao (em logit, a mesma conta das multiplas): se damos 80% e
    acontecem 65%, as proximas probabilidades descem e so passam os sinais
    mais fortes (o limiar de 70% fica igual); se acontece mais do que damos,
    sobem. Prudente com poucos alertas (K) e so a partir de MIN_N.

Aprende-se sempre sobre a probabilidade ANTES da correccao (p_modelo no
registo), para a correccao nao se desfazer a si propria; os registos antigos,
sem p_modelo, usam a odd justa enviada (1/p).

O alerta mostra tambem o historial: "📏 Ja enviamos 41: acertamos 78% (davamos 76%)".
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

TIPOS = {"cartoes_fim": "cartao no fim", "mais_golos": "mais golos"}
DIAS = 90
K = 3.0             # prudencia: com poucos alertas a correccao fica perto de zero
MIN_N = 20          # so corrige com 20+ alertas resolvidos
MIN_N_TEXTO = 10    # o historial aparece no alerta a partir de 10
MAX_AJUSTE = 0.8    # em logit


@dataclass
class Registo:
    n: int = 0
    acertos: int = 0
    soma_p: float = 0.0       # probabilidade enviada (o que o Ricardo viu)
    ajuste: float = 0.0       # correccao aplicada a partir de agora (logit)

    @property
    def taxa(self) -> float:
        return self.acertos / self.n if self.n else 0.0

    @property
    def p_media(self) -> float:
        return self.soma_p / self.n if self.n else 0.0


def ajustar(p: float, ajuste: float) -> float:
    if not ajuste or not 0 < p < 1:
        return p
    return 1 / (1 + math.exp(-(math.log(p / (1 - p)) + ajuste)))


def _float(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def calcular(linhas: list[dict], totais: dict[tuple[int, str], float]) -> dict[str, Registo]:
    """linhas: registos de alertas_enviados (com 'ts'); totais: final por (evento, mercado)."""
    out: dict[str, Registo] = {}
    soma: dict[str, list[float]] = {}
    for r in linhas:
        tipo = r.get("tipo")
        if tipo not in TIPOS:
            continue
        try:
            chave = (int(r.get("event_id") or 0), r.get("mercado") or "")
        except ValueError:
            continue
        total, linha, odd = totais.get(chave), _float(r.get("linha")), _float(r.get("odd"))
        if total is None or linha is None or odd is None or odd <= 1.0:
            continue
        p_env = 1 / odd
        p_mod = _float(r.get("p_modelo")) or p_env
        if not 0 < p_mod < 1:
            continue
        ganhou = total > linha if (r.get("lado") or "over").lower() == "over" else total < linha
        reg = out.setdefault(tipo, Registo())
        reg.n += 1
        reg.acertos += ganhou
        reg.soma_p += p_env
        a = soma.setdefault(tipo, [0.0, 0.0])
        a[0] += ganhou - p_mod
        a[1] += p_mod * (1 - p_mod)
    for tipo, reg in out.items():
        if reg.n >= MIN_N:
            num, den = soma[tipo]
            reg.ajuste = round(max(-MAX_AJUSTE, min(MAX_AJUSTE, num / (den + K))), 4)
    return out


def caminho() -> Path:
    from apostas.utils.config import project_root
    return project_root() / "dados" / "tracking" / "calibracao_especiais.json"


def gravar(regs: dict[str, Registo], path: Path | None = None) -> None:
    p = path or caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({t: asdict(r) for t, r in regs.items()}, ensure_ascii=False),
                 encoding="utf-8")


def ler(path: Path | None = None) -> dict[str, Registo]:
    p = path or caminho()
    try:
        return {t: Registo(**r) for t, r in json.loads(p.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, TypeError):
        return {}


def recalcular_e_gravar(sess, agora: datetime | None = None, path: Path | None = None,
                        enviados: Path | None = None) -> dict[str, Registo]:
    from apostas.relatorios import alertas_enviados
    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    desde = agora - timedelta(days=DIAS)
    linhas = [r for r in alertas_enviados.carregar(enviados)
              if r["tipo"] in TIPOS and desde <= r["ts"] < agora]
    chaves = {(int(r["event_id"]), r["mercado"]) for r in linhas
              if str(r.get("event_id") or "").isdigit()}
    totais = alertas_enviados.totais_finais_bd(sess, chaves) if chaves else {}
    regs = calcular(linhas, totais)
    gravar(regs, path)
    return regs


def texto_alerta(reg: Registo | None) -> str:
    """'📏 Ja enviamos 41 destes: acertamos 78% (davamos 76%)' - vazio com poucos."""
    if reg is None or reg.n < MIN_N_TEXTO:
        return ""
    txt = (f"📏 Ja enviamos {reg.n} destes: acertamos *{reg.taxa * 100:.0f}%* "
           f"(davamos {reg.p_media * 100:.0f}%)")
    if abs(reg.ajuste) >= 0.05:
        txt += f" · probabilidade corrigida ({'para baixo' if reg.ajuste < 0 else 'para cima'})"
    return txt


def formatar(regs: dict[str, Registo]) -> str:
    linhas = [f"*📏 Cartao no fim e mais golos* (os enviados nos ultimos {DIAS} dias)"]
    if not regs:
        return "\n".join(linhas + ["  Ainda sem alertas resolvidos."])
    for tipo, nome in TIPOS.items():
        r = regs.get(tipo)
        if r is None or not r.n:
            continue
        corr = (f" · correccao {ajustar(0.75, r.ajuste) * 100 - 75:+.0f}pp num sinal de 75%"
                if r.ajuste else (f" · corrige a partir de {MIN_N}" if r.n < MIN_N else ""))
        linhas.append(f"  {nome}: {r.acertos}/{r.n} acertados ({r.taxa * 100:.0f}%) · "
                      f"davamos {r.p_media * 100:.0f}%{corr}")
    return "\n".join(linhas)
