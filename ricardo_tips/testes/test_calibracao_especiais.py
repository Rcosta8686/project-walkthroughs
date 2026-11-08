"""Cartao no fim e mais golos aprendem com o que ja enviaram."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.relatorios import calibracao_especiais as ce

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 8, 0, 15)


def _linhas(tipo, n, acertos, odd=1.25, p_modelo=None, mercado="cartoes"):
    out, totais = [], {}
    for i in range(n):
        out.append({"tipo": tipo, "event_id": str(1000 + i), "mercado": mercado,
                    "lado": "over", "linha": "4.5", "odd": str(odd),
                    "p_modelo": "" if p_modelo is None else str(p_modelo),
                    "ts": AGORA - timedelta(days=1)})
        totais[(1000 + i, mercado)] = 5.0 if i < acertos else 4.0
    return out, totais


def test_optimista_demais_corrige_para_baixo():
    linhas, totais = _linhas("cartoes_fim", 40, 26)       # dava 80%, aconteceu 65%
    r = ce.calcular(linhas, totais)["cartoes_fim"]
    assert (r.n, r.acertos) == (40, 26) and r.p_media == pytest.approx(0.8)
    assert -0.8 <= r.ajuste < -0.4
    assert 0.65 < ce.ajustar(0.8, r.ajuste) < 0.73
    txt = ce.texto_alerta(r)
    assert txt.startswith("📏 Ja enviamos 40 destes: acertamos *65%* (davamos 80%)")
    assert "corrigida (para baixo)" in txt


def test_aprende_sobre_a_probabilidade_antes_da_correccao():
    # enviados ja corrigidos (odd 1.43 = 70%) mas o modelo dava 80%: a correccao fica
    linhas, totais = _linhas("mais_golos", 40, 26, odd=1.43, p_modelo=0.8, mercado="golos")
    r = ce.calcular(linhas, totais)["mais_golos"]
    assert r.ajuste < -0.4 and r.p_media == pytest.approx(0.70, abs=0.01)


def test_poucos_alertas_nao_corrige_e_nao_mostra():
    linhas, totais = _linhas("cartoes_fim", 15, 5)
    r = ce.calcular(linhas, totais)["cartoes_fim"]
    assert r.ajuste == 0.0 and ce.texto_alerta(r) != ""          # 15 >= 10: mostra
    linhas, totais = _linhas("cartoes_fim", 8, 2)
    assert ce.texto_alerta(ce.calcular(linhas, totais)["cartoes_fim"]) == ""
    assert "corrige a partir de 20" in ce.formatar(ce.calcular(linhas, totais))
    assert "Ainda sem alertas" in ce.formatar({})


def test_ignora_outros_tipos_e_sem_resultado():
    linhas, totais = _linhas("cartoes_fim", 5, 5)
    linhas[0]["tipo"] = "projeccao"
    del totais[(1001, "cartoes")]
    r = ce.calcular(linhas, totais)["cartoes_fim"]
    assert r.n == 3


def test_gravar_ler(tmp_path):
    f = tmp_path / "c.json"
    ce.gravar({"mais_golos": ce.Registo(25, 20, 18.0, 0.3)}, f)
    assert ce.ler(f)["mais_golos"].ajuste == 0.3
    assert ce.ler(tmp_path / "nada.json") == {}


@pytest.fixture
def bd(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    yield
    db_mod._engine = None
    db_mod._SessionLocal = None


def test_recalcular_com_a_bd(bd, tmp_path):
    from apostas.relatorios import alertas_enviados as ae
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo
    reg = tmp_path / "env.csv"
    ae.registar(tipo="cartoes_fim", ts=AGORA - timedelta(hours=5), path=reg, event_id=7,
                mercado="cartoes", lado="over", linha=4.5, odd=1.25, p_modelo=0.8)
    ae.registar(tipo="mais_golos", ts=AGORA - timedelta(hours=4), path=reg, event_id=7,
                mercado="golos", lado="over", linha=2.5, odd=1.4)
    ae.registar(tipo="mais_golos", ts=AGORA - timedelta(days=200), path=reg, event_id=8,
                mercado="golos", lado="over", linha=0.5, odd=1.4)       # fora dos 90 dias
    with abrir_sessao() as s:
        s.add_all([EstatisticaEquipaJogo(sofascore_event_id=7, sofascore_team_id=t,
                                         nome_equipa="X", era_casa=t == 1, data_jogo=AGORA,
                                         resultado="D", golos_marcados=1, golos_sofridos=1,
                                         cards_amarelos=c) for t, c in ((1, 3), (2, 2))])
    with abrir_sessao() as s:
        regs = ce.recalcular_e_gravar(s, agora=AGORA, path=tmp_path / "c.json", enviados=reg)
    assert (regs["cartoes_fim"].n, regs["cartoes_fim"].acertos) == (1, 1)    # 5 > 4.5
    assert (regs["mais_golos"].n, regs["mais_golos"].acertos) == (1, 0)      # 2 < 2.5
    assert ce.ler(tmp_path / "c.json")["cartoes_fim"].n == 1


def test_alertas_usam_a_correccao():
    from apostas.modelos import cartoes_finais as cf
    from apostas.modelos import golos_finais as gf
    estado_c = SimpleNamespace(event_id=1, home="Porto", away="Benfica", liga="L", minuto=75,
                               marcador_casa=1, marcador_fora=1, yellow_cards_casa=2,
                               yellow_cards_fora=2, red_cards_casa=0, red_cards_fora=0,
                               fouls_casa=14, fouls_fora=13, arbitro=None)
    hist_c = cf.Historia({(75, "empate", "altas"): cf.Taxa(n=120, mais1=90, mais2=40)})  # 75%
    prev_c = SimpleNamespace(lam_cartoes_amarelos_restantes=2.0)
    s = cf.avaliar(estado_c, prev_c, hist_c)
    assert s is not None and s.p1 == pytest.approx(0.75) and s.p1_bruto == pytest.approx(0.75)
    assert cf.avaliar(estado_c, prev_c, hist_c, ajuste_p=-0.4) is None    # 75% → 67%: nao passa
    mais = cf.avaliar(estado_c, prev_c, hist_c, ajuste_p=0.3)
    assert mais is not None and mais.p1 > 0.79 and mais.p1_bruto == pytest.approx(0.75)

    estado_g = SimpleNamespace(event_id=2, home="Porto", away="Benfica", liga="L", minuto=62,
                               marcador_casa=1, marcador_fora=1, shots_total_casa=14,
                               shots_total_fora=12, shots_on_target_casa=5,
                               shots_on_target_fora=4, xg_casa=None, xg_fora=None,
                               big_chances_casa=None, big_chances_fora=None,
                               red_cards_casa=0, red_cards_fora=0)
    hist_g = gf.Historia({(60, "empate", "muitos remates"): gf.Taxa(150, 113, 60)})
    prev_g = SimpleNamespace(lam_golos_restantes=1.8, lam_golos_casa_rest=1.0,
                             lam_golos_fora_rest=0.8)
    assert gf.avaliar(estado_g, prev_g, hist_g) is not None
    assert gf.avaliar(estado_g, prev_g, hist_g, ajuste_p=-0.5) is None


def test_scanner_mostra_historial_e_regista_p_modelo(monkeypatch):
    import scanner_live_v2 as sc
    from apostas.modelos import golos_finais as gf
    enviadas, registos = [], []
    sc._cooldown.clear()
    hist = gf.Historia({(60, "empate", "muitos remates"): gf.Taxa(150, 117, 60)})
    monkeypatch.setattr(sc, "_historia_golos_live", lambda: hist)
    monkeypatch.setattr(sc, "_calib_especiais_live",
                        lambda: {"mais_golos": ce.Registo(30, 24, 22.8, 0.1)})
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sc.alertas_enviados, "registar", lambda **kw: registos.append(kw))
    monkeypatch.setattr(sc, "_odd_real", lambda *a: None)
    estado = SimpleNamespace(event_id=3, home="Porto", away="Benfica", liga="L", minuto=62,
                             marcador_casa=1, marcador_fora=1, shots_total_casa=14,
                             shots_total_fora=12, shots_on_target_casa=5, shots_on_target_fora=4,
                             xg_casa=None, xg_fora=None, big_chances_casa=None,
                             big_chances_fora=None, red_cards_casa=0, red_cards_fora=0)
    prev = SimpleNamespace(lam_golos_restantes=1.8, lam_golos_casa_rest=1.0,
                           lam_golos_fora_rest=0.8)
    assert sc._alerta_mais_golos(estado, prev) is True
    assert "📏 Ja enviamos 30 destes: acertamos *80%* (davamos 76%)" in enviadas[0]
    assert registos[0]["p_modelo"] == pytest.approx(0.78, abs=0.01)
    assert registos[0]["odd"] < round(1 / 0.78, 2)            # corrigida para cima
    sc._cooldown.clear()
