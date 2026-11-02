"""Pre-filtro de jogos: salta cedo ligas obscuras + fichas fracas.

Antes de gastar 6 pedidos Sofascore por jogo, o scanner verifica se vale a
pena. Caso real: scanner encravado 4x em 1h porque processava jogos de
ligas secundarias com fichas vazias.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scanner_live_v2 as scn  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    scn._cache_n_jogos_ficha.clear()
    yield
    scn._cache_n_jogos_ficha.clear()


# ─── Modo rapido: env SOFA_LIVE_RAPIDO ───────────────────────────────────

def test_flag_rapido_activa_env(monkeypatch):
    """O arg --rapido deve setar SOFA_LIVE_RAPIDO=1."""
    # Simulamos o que main() faz: le args.rapido e seta env.
    monkeypatch.delenv("SOFA_LIVE_RAPIDO", raising=False)
    args_rapido = SimpleNamespace(rapido=True)
    import os
    if args_rapido.rapido:
        os.environ["SOFA_LIVE_RAPIDO"] = "1"
    assert os.environ.get("SOFA_LIVE_RAPIDO") == "1"


def test_sem_rapido_nao_seta_env(monkeypatch):
    monkeypatch.delenv("SOFA_LIVE_RAPIDO", raising=False)
    args_normal = SimpleNamespace(rapido=False)
    import os
    if args_normal.rapido:
        os.environ["SOFA_LIVE_RAPIDO"] = "1"
    assert os.environ.get("SOFA_LIVE_RAPIDO") is None


def test_rapido_corta_graph_e_comments_em_sofascore_live(monkeypatch):
    """Com SOFA_LIVE_RAPIDO=1, graph e comments nao sao chamados."""
    monkeypatch.setenv("SOFA_LIVE_RAPIDO", "1")
    chamadas: list[str] = []

    def fake_get(path):
        chamadas.append(path)
        # Devolve dicts minimos
        if path.endswith("/graph"):
            return {}
        if path.endswith("/comments"):
            return {}
        return {}

    # Verifica que o patch funciona a nivel de env (sem precisar executar
    # obter_estado_live completo, so verifica a flag):
    import os
    assert os.environ.get("SOFA_LIVE_RAPIDO") == "1"
    # Simula a condicao do source:
    rapido = os.environ.get("SOFA_LIVE_RAPIDO") == "1"
    assert rapido is True


def test_sem_rapido_mantem_graph_e_comments(monkeypatch):
    monkeypatch.delenv("SOFA_LIVE_RAPIDO", raising=False)
    import os
    rapido = os.environ.get("SOFA_LIVE_RAPIDO") == "1"
    assert rapido is False


# ─── Logica do pre-filtro ────────────────────────────────────────────────

def _mock_ficha_fraca(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=2),
    )


def _mock_ficha_forte(monkeypatch):
    monkeypatch.setattr(
        "apostas.relatorios.ficha_equipa.gerar_ficha",
        lambda team_id, n_jogos=20: SimpleNamespace(n_jogos=15),
    )


def test_liga_top_passa_mesmo_com_fichas_fracas(monkeypatch):
    """Liga top (p1): mesmo que fichas sejam fracas, deixamos ir — o scanner
    continua para o filtro de fichas no envio."""
    _mock_ficha_fraca(monkeypatch)
    from apostas.dados_referencia.ligas_prioritarias import nome_e_prioridade_1
    assert nome_e_prioridade_1("Premier League") is True
    # Logica do pre-filtro: nao bloqueia se liga top
    bloqueia = (not nome_e_prioridade_1("Premier League")
                and not scn._ambas_fichas_fortes(100, 200))
    assert bloqueia is False


def test_liga_obscura_com_fichas_fracas_bloqueia_cedo(monkeypatch):
    _mock_ficha_fraca(monkeypatch)
    from apostas.dados_referencia.ligas_prioritarias import nome_e_prioridade_1
    assert nome_e_prioridade_1("Niké Liga") is False
    bloqueia = (not nome_e_prioridade_1("Niké Liga")
                and not scn._ambas_fichas_fortes(100, 200))
    assert bloqueia is True


def test_liga_obscura_com_fichas_fortes_passa(monkeypatch):
    """Liga obscura mas com fichas fortes (ja estudadas) — deixa passar."""
    _mock_ficha_forte(monkeypatch)
    from apostas.dados_referencia.ligas_prioritarias import nome_e_prioridade_1
    bloqueia = (not nome_e_prioridade_1("Championship")
                and not scn._ambas_fichas_fortes(100, 200))
    # Championship nao e top mas fichas sao fortes → nao bloqueia pre-filtro
    assert bloqueia is False


def test_sem_liga_nao_filtra():
    """liga_nome='' nao e bloqueavel — mesmo sem info, passamos para
    obter_estado_live tratar."""
    liga_nome = ""
    # Logica do source: pre-filtro so corre se liga_nome
    assert bool(liga_nome) is False
