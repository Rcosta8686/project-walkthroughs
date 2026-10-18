"""Testes para apostas/dados_referencia/ligas_prioritarias.py."""
from __future__ import annotations

from apostas.dados_referencia.ligas_prioritarias import (
    LIGAS_PRIORITARIAS, LigaPrioritaria,
    ligas_de_prioridade, sofascore_ids_prioritarias, todas_ligas,
)


def test_tem_13_ligas():
    """13 ligas confirmadas pelo Ricardo (2026-10-18)."""
    assert len(LIGAS_PRIORITARIAS) == 13


def test_big5_na_prioridade_1():
    nomes_p1 = {l.nome for l in ligas_de_prioridade(1)}
    for n in ("Premier League (ENG)", "LaLiga", "Serie A (ITA)",
              "Bundesliga", "Ligue 1 (FRA)", "Liga Portugal",
              "Eredivisie"):
        assert n in nomes_p1, f"Prioridade 1 falta: {n}"


def test_segundas_divisoes_na_prioridade_2():
    nomes_p2 = {l.nome for l in ligas_de_prioridade(2)}
    for n in ("Championship (ENG)", "LaLiga 2 (Hypermotion)",
              "Serie B (ITA)", "Bundesliga 2", "Liga Portugal 2",
              "Eerste Divisie"):
        assert n in nomes_p2, f"Prioridade 2 falta: {n}"


def test_ids_sofascore_unicos():
    """IDs devem ser todos distintos."""
    ids = sofascore_ids_prioritarias()
    assert len(set(ids)) == len(ids), "IDs duplicados em LIGAS_PRIORITARIAS"


def test_todas_ligas_ordena_por_prioridade():
    todas = todas_ligas()
    prevs = [l.prioridade for l in todas]
    assert prevs == sorted(prevs), "todas_ligas() nao esta ordenada"


def test_mls_e_ligue2_e_brasileirao_nao_incluidos():
    """Ricardo excluiu MLS, Ligue 2 e Brasileirao da lista final."""
    nomes = {l.nome for l in LIGAS_PRIORITARIAS}
    assert "MLS" not in nomes
    assert "Ligue 2" not in nomes
    assert not any("Brasil" in n for n in nomes)


def test_dataclass_frozen():
    """LigaPrioritaria e imutavel."""
    l = LIGAS_PRIORITARIAS[0]
    try:
        l.prioridade = 5  # type: ignore[misc]
        raise AssertionError("Deveria ter falhado por FrozenInstanceError")
    except Exception:
        pass
