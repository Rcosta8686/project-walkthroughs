"""Curva de aprendizagem das previsoes por equipa (precisao_equipas.historia)."""
from __future__ import annotations

from datetime import date

import pytest

from apostas.relatorios import grafico_precisao as gp


def _noite(d, **mercados):
    return {"data": d, **{m: {"dentro1": v, "mae": 1.0, "teto": t, "n": 300}
                          for m, (v, t) in mercados.items()}}


HISTORIA = [_noite("2026-11-03", remates_a_baliza=(0.40, 0.55), cantos=(0.42, 0.50)),
            _noite("2026-11-01", remates_a_baliza=(0.38, 0.55)),
            _noite("2026-11-05", remates_a_baliza=(0.47, 0.56), cantos=(0.41, 0.50)),
            {"data": "lixo"}]


def test_series_por_mercado_e_por_data():
    s = gp.series(HISTORIA)
    assert [p.data for p in s["remates_a_baliza"]] == [date(2026, 11, 1), date(2026, 11, 3),
                                                       date(2026, 11, 5)]
    assert len(s["cantos"]) == 2 and "golos" not in s


def test_legenda_com_evolucao_e_maximo():
    leg = gp.legenda(gp.series(HISTORIA))
    assert "_Noites de 01/11/2026 a 05/11/2026_" in leg
    assert "remates a baliza: *47%* (max. ~56%) · 📈 +9pp desde 01/11" in leg
    assert "cantos: *41%* (max. ~50%) · 📉 -1pp desde 03/11" in leg
    assert "Ainda sem noites" in gp.legenda({})


def test_png_e_texto():
    pytest.importorskip("matplotlib")
    g = gp.gerar(HISTORIA)
    assert g.png is not None and g.png[:4] == b"\x89PNG" and not g.vazio
    assert "remates a baliza: 01/11 38% · 03/11 40% · 05/11 47%" in g.texto
    vazio = gp.gerar([])
    assert vazio.vazio
