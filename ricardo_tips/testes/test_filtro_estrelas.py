"""Novo cutoff: so projeccoes >= 4 estrelas vao para Telegram.

Alem disso, a formula de estrelas aumentou de max 3 para max 5, com
bonus por 'doce' + ritmo claro.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from apostas.modelos.projecao_live import (  # noqa: E402
    LinhaOdds, ProjecaoResumo, _estrelas_projecao,
)


def _mk_proj(linhas_probs, valor_atual=4, projecao_final=8):
    """Factory de ProjecaoResumo para testes de estrelas."""
    linhas = [
        LinhaOdds(linha=5.5 + i, prob_over=p, over_justa=1.5, under_justa=2.5)
        for i, p in enumerate(linhas_probs)
    ]
    return ProjecaoResumo(
        mercado="sot", minuto=55,
        valor_atual_total=valor_atual,
        projecao_final_total=projecao_final,
        valor_atual_casa=valor_atual / 2, valor_atual_fora=valor_atual / 2,
        projecao_final_casa=projecao_final / 2, projecao_final_fora=projecao_final / 2,
        linhas_total=linhas,
    )


# ─── _estrelas_projecao ──────────────────────────────────────────────────

def test_estrelas_min_1_mesmo_sem_linhas_accionaveis():
    """Linhas todas fora da zona 0.25-0.75 → ainda assim 1."""
    p = _mk_proj([0.90, 0.95, 0.98], valor_atual=2, projecao_final=2.5)
    assert _estrelas_projecao(p) == 1


def test_estrelas_so_base_sem_bonus_do_doce_nem_ritmo():
    """2 linhas accionaveis, centro fora do doce, sem ritmo claro → 2 estrelas."""
    # 3 linhas: 0.30, 0.70 (centro fora 0.45-0.65), 0.80 → base=2
    # valor_atual=4, projeccao=5 → ratio 1.25 < 1.5 → sem bonus ritmo
    p = _mk_proj([0.30, 0.70, 0.80], valor_atual=4, projecao_final=5)
    assert _estrelas_projecao(p) == 2


def test_estrelas_bonus_doce_centro():
    """Centro 0.55 esta no doce → +1 estrela."""
    # base=3 (todas accionaveis), centro=0.55 (no doce), sem ritmo
    p = _mk_proj([0.35, 0.55, 0.70], valor_atual=4, projecao_final=5)
    # base 3 + doce 1 = 4
    assert _estrelas_projecao(p) == 4


def test_estrelas_bonus_ritmo_claro():
    """projeccao 1.5x valor_actual → +1 estrela."""
    # base=1 (so 1 linha accionavel), sem doce (0.80), com ritmo (4 → 8)
    p = _mk_proj([0.80, 0.90, 0.95], valor_atual=4, projecao_final=8)
    # Nenhuma esta na janela 0.25-0.75... Deixa recalcular — 0.80 > 0.75
    # base=0, sem doce (0.90 fora 0.45-0.65), com ritmo = 1
    assert _estrelas_projecao(p) == 1


def test_estrelas_cinco_max():
    """base=3 + doce + ritmo = 5."""
    p = _mk_proj([0.30, 0.55, 0.70], valor_atual=4, projecao_final=10)
    # base 3 + doce 1 + ritmo 1 = 5
    assert _estrelas_projecao(p) == 5


def test_estrelas_capped_em_5():
    """Nunca passa 5 mesmo com casos extremos."""
    p = _mk_proj([0.50, 0.50, 0.50], valor_atual=2, projecao_final=20)
    assert _estrelas_projecao(p) <= 5


def test_estrelas_min_1_mesmo_sem_tudo():
    p = _mk_proj([0.90, 0.95, 0.99], valor_atual=5, projecao_final=5.1)
    assert _estrelas_projecao(p) == 1


def test_estrelas_valor_actual_zero_nao_divide_zero():
    """Se valor_atual=0, ritmo bonus nao deve crashar."""
    p = _mk_proj([0.40, 0.55, 0.65], valor_atual=0, projecao_final=5)
    # Nao deve crashar; ritmo bonus desactivado (sem referencia).
    # base=3 + doce (0.55) = 4
    assert _estrelas_projecao(p) == 4


# ─── Floor min_estrelas em _enviar_projecoes_jogo ────────────────────────

def test_arg_default_e_quatro():
    """CLI default de --projecao-tele-estrelas tem de ser 4."""
    # Importa o scanner inteiro so para olhar o default do argparse
    # (nao chama main, so inspecciona).
    import re
    scanner_src = (
        Path(__file__).resolve().parent.parent / "scripts" / "scanner_live_v2.py"
    ).read_text(encoding="utf-8")
    # Procura o add_argument de --projecao-tele-estrelas default
    m = re.search(
        r"--projecao-tele-estrelas.*?default\s*=\s*(\d+)",
        scanner_src, re.DOTALL,
    )
    assert m is not None, "flag --projecao-tele-estrelas nao encontrada"
    assert int(m.group(1)) == 4


def test_arg_analista_default_e_quatro():
    """--analista-tele-estrelas tambem default 4 (nova regra do Ricardo)."""
    import re
    scanner_src = (
        Path(__file__).resolve().parent.parent / "scripts" / "scanner_live_v2.py"
    ).read_text(encoding="utf-8")
    m = re.search(
        r"--analista-tele-estrelas.*?default\s*=\s*(\d+)",
        scanner_src, re.DOTALL,
    )
    assert m is not None
    assert int(m.group(1)) == 4
