"""Testes do módulo de aliases de equipas (resolução entre fontes)."""

from __future__ import annotations

from apostas.ingestao import aliases as _aliases


def test_nome_canonico_de_resolve_football_data():
    assert _aliases.nome_canonico_de("Man United", "football_data") == "Manchester United"
    assert _aliases.nome_canonico_de("Paris SG", "football_data") == "Paris Saint Germain"
    assert _aliases.nome_canonico_de("Milan", "football_data") == "AC Milan"


def test_nome_canonico_de_devolve_none_para_desconhecido():
    assert _aliases.nome_canonico_de("Arsenal", "football_data") is None
    assert _aliases.nome_canonico_de("qualquer coisa", "sportmonks") is None


def test_aliases_de_devolve_lista():
    assert "Man United" in _aliases.aliases_de("Manchester United", "football_data")
    assert _aliases.aliases_de("Arsenal", "football_data") == []
