"""M17 — Dual Signal: requer DUAS confirmacoes independentes.

Antes de uma sugestao sair como "forte", exige que duas fontes
indepentes concordem. Isto corta falsos positivos (o modelo acerta
na direcao mas a magnitude e ruido vs. mercado) e marca os sinais
onde as evidencias se juntam.

Fontes de confirmacao (independentes):
  A) Edge contra mercado: EV >= limiar (prob_modelo × odd − 1 > 0.05).
     Para sinais ANALISTA (sem odds) e prob_modelo >= limiar_prob (0.70).
  B) Historico M16 do tipo (liga, mercado, lado): perfil forte
     (categoria_forte=True) OU stake_mult >= 1.0 com volume minimo
     (n_alertas >= 20).

E "dual" quando ambos A e B passam. Nao substitui os filtros existentes
(M18/M19 continuam a bloquear coisas inviaveis) — apenas carimba um
flag no sinal para o telegram_live mostrar e para rankear primeiro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


# Limiares — matcham o scanner_live_v2.py por default. Chamador pode
# passar outros via `avaliar_dual_sinal`.
EV_LIMIAR_DEFAULT = 0.05
PROB_LIMIAR_ANALISTA_DEFAULT = 0.70
MIN_ALERTAS_HISTORICO = 20


@dataclass
class ResultadoDualSinal:
    """Resultado da avaliacao."""
    e_dual: bool
    passa_edge: bool          # A — tem edge contra mercado (ou prob >= limiar)
    passa_historico: bool     # B — perfil historico forte
    razao: str                # texto curto para o telegram ("edge+historico")
    detalhe_edge: str
    detalhe_historico: str


def _perfil_para(perfis: Optional[Mapping], liga: str, mercado: str, lado: str):
    """Lookup robusto no dict de perfis M16 (chaves case-insensitive)."""
    if not perfis:
        return None
    try:
        from apostas.alertas.historico_alertas import perfil_para
        return perfil_para(perfis, liga, mercado, lado)
    except Exception:  # noqa: BLE001
        return None


def avaliar_dual_sinal(
    sugestao: dict,
    *,
    perfis_historicos: Optional[Mapping] = None,
    ev_limiar: float = EV_LIMIAR_DEFAULT,
    prob_limiar_analista: float = PROB_LIMIAR_ANALISTA_DEFAULT,
) -> ResultadoDualSinal:
    """Avalia se `sugestao` tem duas confirmacoes independentes.

    `sugestao` e o mesmo dict que ja circula no scanner (chaves do CSV
    + campos extra em memoria). Nunca modifica a sugestao — devolve
    um ResultadoDualSinal.
    """
    casa = (sugestao.get("casa") or "").strip()
    liga = (sugestao.get("liga") or "").strip()
    mercado = (sugestao.get("mercado") or "").strip()
    lado = (sugestao.get("lado") or "").strip()

    try:
        prob = float(sugestao.get("prob_modelo") or 0.0)
    except (TypeError, ValueError):
        prob = 0.0

    # A) Edge contra mercado
    passa_edge = False
    detalhe_edge = ""
    if casa == "ANALISTA":
        # Sem odds reais — pede prob alta
        passa_edge = prob >= prob_limiar_analista
        detalhe_edge = f"prob {prob*100:.0f}% ≥ {prob_limiar_analista*100:.0f}%"
    else:
        try:
            ev = float(sugestao.get("ev") or 0.0)
        except (TypeError, ValueError):
            ev = 0.0
        passa_edge = ev >= ev_limiar
        detalhe_edge = f"EV {ev*100:+.1f}% ≥ {ev_limiar*100:.0f}%"

    # B) Historico M16
    passa_historico = False
    detalhe_historico = "sem histórico"
    perfil = _perfil_para(perfis_historicos, liga, mercado, lado)
    if perfil is not None:
        if perfil.categoria_forte:
            passa_historico = True
            detalhe_historico = f"categoria forte · {perfil.n_alertas} alertas"
        elif perfil.stake_mult >= 1.0 and perfil.n_alertas >= MIN_ALERTAS_HISTORICO:
            # Volume minimo + sem bloqueio por CLV
            clv = perfil.clv_medio or 0.0
            if clv >= 0:
                passa_historico = True
                detalhe_historico = (
                    f"{perfil.n_alertas} alertas · CLV {clv:+.1f}%"
                )

    e_dual = passa_edge and passa_historico
    if e_dual:
        razao = "edge+histórico"
    elif passa_edge:
        razao = "só edge"
    elif passa_historico:
        razao = "só histórico"
    else:
        razao = "nenhum"

    return ResultadoDualSinal(
        e_dual=e_dual,
        passa_edge=passa_edge,
        passa_historico=passa_historico,
        razao=razao,
        detalhe_edge=detalhe_edge,
        detalhe_historico=detalhe_historico,
    )
