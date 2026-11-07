"""So alertas de remates nas ligas onde a casa tem esse mercado."""
from __future__ import annotations

from apostas.dados_referencia.mercados_casa import mercado_disponivel


def test_remates_so_nas_ligas_principais():
    assert mercado_disponivel("remates", "Bundesliga")
    assert mercado_disponivel("remates_a_baliza", "UEFA Champions League")
    assert mercado_disponivel("sot", "Serie A (ITA)")
    assert mercado_disponivel("remates", "Liga Portugal Betclic")
    assert not mercado_disponivel("remates", "2. Bundesliga")
    assert not mercado_disponivel("remates", "Championship")
    assert not mercado_disponivel("remates_a_baliza", "Serie B")
    assert not mercado_disponivel("sot", "Liga Portugal 2")
    assert not mercado_disponivel("remates", "Brasileirão Série A")
    assert not mercado_disponivel("remates", "FA Cup")


def test_outros_mercados_passam_em_todo_o_lado():
    for m in ("cantos", "cartoes", "golos", "btts"):
        assert mercado_disponivel(m, "2. Bundesliga")


def test_projeccao_de_remates_a_baliza_na_segunda_divisao_nao_sai(monkeypatch):
    from types import SimpleNamespace
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import scanner_live_v2 as sc
    from apostas.alertas.corte_mercados import Cortes
    from apostas.modelos.calibrador_mercado import Calibrador
    enviadas = []
    monkeypatch.setattr(sc, "_ambas_fichas_fortes", lambda *a: True)
    monkeypatch.setattr(sc, "_mult_projecao", lambda m, j: 1.0)
    monkeypatch.setattr(sc, "_gravar_projecao_db", lambda *a: None)
    monkeypatch.setattr(sc, "_calibrador_live", lambda: Calibrador())
    monkeypatch.setattr(sc, "_cortes_live", lambda: Cortes())
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sc.alertas_enviados, "registar", lambda **kw: None)
    sc._projecoes_enviadas.clear()
    sc._primeiro_minuto_visto.clear()
    estado = SimpleNamespace(event_id=78, minuto=56, liga="2. Bundesliga", home="Osnabrück",
                             away="Kaiserslautern", home_id=None, away_id=None,
                             marcador_casa=0, marcador_fora=0,
                             shots_on_target_casa=3, shots_on_target_fora=2,
                             corners_casa=4, corners_fora=3)
    prev = SimpleNamespace(projeccao_sot_casa=5.5, projeccao_sot_fora=4.0,
                           projeccao_cantos_casa=6.5, projeccao_cantos_fora=5.0)
    sc._enviar_projecoes_jogo(estado, prev, enviar_telegram=True, min_estrelas=1)
    assert len(enviadas) == 1 and "Cantos" in enviadas[0]
