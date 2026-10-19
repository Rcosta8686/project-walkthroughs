"""Filtro de desempenho — blacklist dinamica por (liga, mercado).

Em vez de blacklists hardcoded ("Brasileirao bloqueado"), le o
historico real do CSV live e bloqueia combinacoes (liga, mercado)
com volume suficiente E ROI claramente negativo.

Vantagens:
  - Nao perde dinheiro em cantos do Brasileirao enquanto preserva
    remates do Brasileirao (+44% ROI nos dados reais).
  - Blacklist aprende sozinha quando puxas novos resolvidos.
  - Mantem o override hardcoded em `analista.liga_na_blacklist` como
    fallback para casos extremos (ROI < -40% em n < 30).

Thresholds — ver constantes no topo. Precisa de n_resolvidos >= 30
para considerar bloqueio (menos que isso e ruido).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


# ─────────── Limiares ─────────────────────────────────────────────

# Volume minimo de alertas resolvidos antes de considerar bloqueio.
# <30 e ruido — nao bloqueia.
MIN_RESOLVIDOS_PARA_BLOQUEAR = 30

# ROI abaixo deste limiar → bloqueia. -5% permite tolerar flutuacao
# estatistica em volumes de 30-50 alertas mas corta sangria clara.
ROI_LIMIAR_BLOQUEAR = -5.0

# Entre -5% e 0% → avisa mas nao bloqueia (stake × 0.5 ja feito por M16
# via perfil_para); isto e so o gate agregado por mercado.
ROI_LIMIAR_METADE = 0.0


# ─────────── Decisao ──────────────────────────────────────────────

@dataclass
class DecisaoFiltro:
    """Resultado da avaliacao do filtro para 1 (liga, mercado)."""
    bloquear: bool
    razao: str          # texto curto para log/debug
    stake_mult: float   # 1.0 | 0.5 | 0.0 — multiplicador se o chamador
                        # quiser respeitar a cautela gradual


def avaliar(
    perfis_mercado: Optional[Mapping],
    liga: str,
    mercado: str,
    *,
    min_resolvidos: int = MIN_RESOLVIDOS_PARA_BLOQUEAR,
    roi_limiar_bloquear: float = ROI_LIMIAR_BLOQUEAR,
    roi_limiar_metade: float = ROI_LIMIAR_METADE,
) -> DecisaoFiltro:
    """Decide se bloquear uma combinacao (liga, mercado) para notificacao.

    Default: nao bloqueia (sem dados → beneficio da duvida).
    Com dados: aplica ROI × volume gate.
    """
    if perfis_mercado is None:
        return DecisaoFiltro(bloquear=False, razao="sem dados", stake_mult=1.0)

    liga_c = liga.strip()
    mercado_c = mercado.strip().lower()
    perfil = perfis_mercado.get((liga_c, mercado_c))
    if perfil is None:
        return DecisaoFiltro(bloquear=False, razao="sem historico", stake_mult=1.0)

    if perfil.n_resolvidos < min_resolvidos:
        return DecisaoFiltro(
            bloquear=False,
            razao=f"volume baixo ({perfil.n_resolvidos}<{min_resolvidos})",
            stake_mult=1.0,
        )

    if perfil.roi is None:
        return DecisaoFiltro(bloquear=False, razao="roi desconhecido", stake_mult=1.0)

    if perfil.roi < roi_limiar_bloquear:
        return DecisaoFiltro(
            bloquear=True,
            razao=f"ROI {perfil.roi:+.1f}% < {roi_limiar_bloquear}% "
                  f"(n={perfil.n_resolvidos})",
            stake_mult=0.0,
        )

    if perfil.roi < roi_limiar_metade:
        return DecisaoFiltro(
            bloquear=False,
            razao=f"ROI {perfil.roi:+.1f}% negativo mas < limiar — stake/2",
            stake_mult=0.5,
        )

    return DecisaoFiltro(
        bloquear=False,
        razao=f"ROI {perfil.roi:+.1f}% (n={perfil.n_resolvidos})",
        stake_mult=1.0,
    )


def bloqueios_atuais(perfis_mercado: Mapping) -> list[tuple[str, str, float, int]]:
    """Para log/debug: lista (liga, mercado, roi, n_resolvidos) actualmente
    a serem bloqueados pelo filtro. Util para o relatorio semanal."""
    out: list[tuple[str, str, float, int]] = []
    for (liga, mercado), p in perfis_mercado.items():
        if (p.roi is not None and p.n_resolvidos >= MIN_RESOLVIDOS_PARA_BLOQUEAR
                and p.roi < ROI_LIMIAR_BLOQUEAR):
            out.append((liga, mercado, p.roi, p.n_resolvidos))
    out.sort(key=lambda t: t[2])  # ROI mais negativo primeiro
    return out
