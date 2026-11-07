"""Correccao do ritmo do resto do jogo: medir, testar fora da amostra, aplicar."""
from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from apostas.modelos import correcao_ritmo as cr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 12, 0)


def _poisson(rng: random.Random, lam: float) -> int:
    limite, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limite:
            return k
        k += 1


def _pares(mercado, fator_real, n_jogos=300, seed=1, previsto=4.0):
    """O modelo preve `previsto`; na realidade sai Poisson(previsto * fator_real)."""
    rng = random.Random(seed)
    out = []
    for e in range(n_jogos):
        for j in cr.JANELAS:
            out.append({"mercado": mercado, "janela": j, "event_id": e,
                        "previsto": previsto, "real": float(_poisson(rng, previsto * fator_real)),
                        "data": AGORA - timedelta(hours=n_jogos - e)})
    return out


def test_treinar_mede_quanto_falta_a_mais():
    pares = [{"mercado": "cantos", "janela": 60, "event_id": i, "previsto": 3.0, "real": 4.0,
              "data": AGORA} for i in range(100)]
    m = cr.treinar(pares)[("cantos", 60)]
    enc = 100 / 130
    assert abs(m.mult - (enc * 4 / 3 + 1 - enc)) < 0.001
    assert (m.previsto, m.real) == (3.0, 4.0)


def test_poucos_pares_nao_treina():
    pares = [{"mercado": "golos", "janela": 30, "event_id": i, "previsto": 1.0, "real": 2.0,
              "data": AGORA} for i in range(cr.MIN_PARES - 1)]
    assert ("golos", 30) not in cr.treinar(pares)


def test_multiplicador_limitado():
    pares = [{"mercado": "golos", "janela": 30, "event_id": i, "previsto": 0.5, "real": 5.0,
              "data": AGORA} for i in range(500)]
    assert cr.treinar(pares)[("golos", 30)].mult == cr.MULT_MAX


def test_liga_so_o_mercado_em_que_a_correccao_melhora_nos_jogos_novos():
    pares = _pares("cartoes", 1.3, seed=2) + _pares("golos", 1.0, seed=3, previsto=1.2)
    c = cr.calcular(pares, agora=AGORA)
    assert "cartoes" in c.ativos and "golos" not in c.ativos
    a = c.avaliacao["cartoes"]
    assert a["erro_corrigido"] < a["erro_bruto"] and a["n_teste"] >= cr.MIN_TESTE
    assert 1.2 < c.mult("cartoes", 60) < 1.35
    assert c.mult("golos", 60) == 1.0          # desligado: sem efeito


def test_janela_de():
    assert [cr.janela_de(m) for m in (5, 15, 29, 44, 45, 70, 88)] == [15, 15, 15, 30, 45, 60, 75]


def _estado(minuto=60, mc=0, mf=0):
    from apostas.ingestao.sofascore_live import EstadoLive
    return EstadoLive(event_id=1, home="A", away="B", liga="Serie A", minuto=minuto, estado="2H",
                      marcador_casa=mc, marcador_fora=mf, xg_casa=0.9, xg_fora=0.6,
                      shots_total_casa=8, shots_total_fora=5, shots_on_target_casa=3,
                      shots_on_target_fora=2, corners_casa=4, corners_fora=2,
                      yellow_cards_casa=1, yellow_cards_fora=2, possession_casa=0.55,
                      possession_fora=0.45)


def _correcao(**mults):
    c = cr.CorrecaoRitmo(ativos=set(mults))
    for m, v in mults.items():
        c.mults[(m, None)] = cr.Mult(n=100, previsto=1, real=v, mult=v)
    return c


def test_aplicar_corrige_lambdas_probabilidades_e_projeccoes():
    from apostas.modelos import live_predictor
    est = _estado()
    prev = live_predictor.prever(est)
    novo = cr.aplicar(prev, est, _correcao(golos=1.5, remates=1.3))
    assert novo.lam_golos_restantes == pytest.approx(prev.lam_golos_restantes * 1.5)
    assert novo.p_over_total(1.5) > prev.p_over_total(1.5)
    assert novo.p_empate < prev.p_empate                       # mais golos esperados
    assert novo.p_casa_vence + novo.p_empate + novo.p_fora_vence == pytest.approx(1.0, abs=0.02)
    assert novo.p_btts_final > prev.p_btts_final
    assert novo.lam_shots_restantes == pytest.approx(prev.lam_shots_restantes * 1.3)
    assert novo.projeccao_shots_casa == pytest.approx(8 + (prev.projeccao_shots_casa - 8) * 1.3)
    assert novo.lam_cantos_restantes == prev.lam_cantos_restantes   # nao ligado


def test_aplicar_sem_mercados_ligados_devolve_a_mesma_previsao():
    from apostas.modelos import live_predictor
    est = _estado()
    prev = live_predictor.prever(est)
    assert cr.aplicar(prev, est, cr.CorrecaoRitmo()) is prev


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


def test_pares_da_bd_um_por_fase_e_so_com_o_final(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def snap(ev, minuto, cantos_c):
        return EstadoLiveSnapshot(event_id=ev, ts=AGORA - timedelta(days=1), home="A", away="B",
                                  liga="Serie A", minuto=minuto, marcador_casa=0, marcador_fora=0,
                                  shots_total_casa=5, shots_total_fora=4, shots_on_target_casa=2,
                                  shots_on_target_fora=1, corners_casa=cantos_c, corners_fora=1,
                                  yellow_casa=1, yellow_fora=0)

    def final(ev, team, golos, cantos):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team, nome_equipa="X",
                                     era_casa=team == 1, data_jogo=AGORA - timedelta(days=1),
                                     resultado="D", golos_marcados=golos, cantos=cantos,
                                     cards_amarelos=2, remates=12, sot=4)
    with abrir_sessao() as s:
        s.add_all([snap(1, 20, 2), snap(1, 25, 3), snap(1, 50, 4),   # 2 fases (15' e 45')
                   final(1, 1, 1, 6), final(1, 2, 0, 3),
                   snap(2, 50, 3)])                                   # jogo 2 sem final
    with abrir_sessao() as s:
        pares = cr.pares_da_bd(s, agora=AGORA)
    assert {(p["event_id"], p["janela"]) for p in pares} == {(1, 15), (1, 45)}
    cantos = {p["janela"]: p["real"] for p in pares if p["mercado"] == "cantos"}
    assert cantos == {15: 9 - 3, 45: 9 - 5}
    golos = [p for p in pares if p["mercado"] == "golos"]
    assert all(p["real"] == 1 and p["previsto"] > 0 for p in golos)


def test_gravar_ler_e_manter_a_data_de_activacao(tmp_path):
    f = tmp_path / "ritmo.json"
    c = cr.calcular(_pares("cartoes", 1.3, seed=4), agora=AGORA)
    cr.gravar(c, f)
    lido = cr.ler(f)
    assert lido.ativos == {"cartoes"} and lido.mult("cartoes", 60) == c.mult("cartoes", 60)
    depois = cr.calcular(_pares("cartoes", 1.3, seed=5), anterior=lido,
                         agora=AGORA + timedelta(days=3))
    assert depois.datas_ativacao["cartoes"] == AGORA   # continua a contar desde a 1a vez
    assert "Ritmo do resto do jogo" in cr.formatar(depois) and "✅ ligado" in cr.formatar(depois)


def test_calibrador_esquece_alertas_de_antes_do_ritmo():
    import scanner_live_v2 as sc
    ritmo = cr.CorrecaoRitmo(ativos={"golos"}, datas_ativacao={"golos": AGORA})
    rows = [{"mercado": "golos", "ts": "2026-11-01T20:00:00"},
            {"mercado": "btts", "ts": "2026-11-01T20:00:00"},     # depende dos golos
            {"mercado": "golos", "ts": "2026-11-08T20:00:00"},
            {"mercado": "cantos", "ts": "2026-11-01T20:00:00"}]
    assert sc._sem_alertas_antes_do_ritmo(rows, ritmo) == [rows[2], rows[3]]


def test_scanner_le_a_correccao_gravada(tmp_path, monkeypatch):
    import scanner_live_v2 as sc
    f = tmp_path / "ritmo.json"
    cr.gravar(cr.calcular(_pares("cartoes", 1.3, seed=6), agora=AGORA), f)
    monkeypatch.setattr(cr, "caminho", lambda: f)
    monkeypatch.setattr(sc, "_ritmo_cache", None)
    assert sc._ritmo_live().ativos == {"cartoes"}


def test_conclusao_do_alerta_de_remates_diz_o_que_tem_de_acontecer():
    """Antes: 'modelo da 84% ao OVER 20.5 — sinal ha mas sem forma concreta'."""
    import scanner_live_v2 as sc
    from apostas.alertas.mega_narrativa import narrativa_final
    from apostas.modelos import live_predictor
    est = _estado(minuto=40)
    prev = live_predictor.prever(est)
    s = {"mercado": "remates", "lado": "over", "linha": "20.5", "minuto": 40, "marcador": "0-0",
         "prob_modelo": 0.84, **sc._extras_narrativa(est, prev, "remates")}
    txt = narrativa_final(s)
    assert "sem forma concreta" not in txt
    assert "OVER 20.5 precisa de pelo menos 8 remates nos 50 min que faltam" in txt


def test_ritmo_suavizado_nao_preve_zero_cartoes_por_haver_zero_aos_30():
    from apostas.modelos import live_predictor
    est = _estado(minuto=30)
    est.yellow_cards_casa = est.yellow_cards_fora = 0
    antigo = live_predictor.prever(est)
    suave = live_predictor.prever(est, ritmo_suavizado=("cartoes",))
    assert antigo.lam_cartoes_amarelos_restantes == 0.0
    assert suave.lam_cartoes_amarelos_restantes > 1.0
    assert suave.p_over_cartoes_total(2.5) > 0.2 > antigo.p_over_cartoes_total(2.5)
    # sem o mercado na lista fica tudo como antes
    assert live_predictor.prever(est, ritmo_suavizado=("cantos",)) \
        .lam_cartoes_amarelos_restantes == 0.0


def test_escolhe_o_ritmo_suavizado_so_onde_erra_menos():
    rng = random.Random(7)
    pares = []
    for e in range(300):
        real = float(_poisson(rng, 3.0))
        for j in cr.JANELAS:
            d = AGORA - timedelta(hours=300 - e)
            # cartoes: antigo preve 0.2 (como 0 aos 30'), suavizado ~3 -> ganha o suavizado
            pares.append({"mercado": "cartoes", "janela": j, "event_id": e, "previsto": 0.2,
                          "previsto_suave": 3.0, "real": real, "data": d})
            # cantos: o antigo ja acerta; o suavizado erra -> fica o antigo
            pares.append({"mercado": "cantos", "janela": j, "event_id": e, "previsto": 3.0,
                          "previsto_suave": 1.0, "real": real, "data": d})
    c = cr.calcular(pares, agora=AGORA)
    assert c.suavizados == {"cartoes"}
    assert "cartoes" in c.mudados and "cantos" not in c.suavizados
    assert "ritmo suavizado" in cr.formatar(c)


def test_prever_corrigido_usa_o_ritmo_suavizado_ligado():
    from apostas.modelos import live_predictor
    est = _estado(minuto=30)
    est.yellow_cards_casa = est.yellow_cards_fora = 0
    c = cr.CorrecaoRitmo(bases={"cartoes": "suave"})
    assert cr.prever_corrigido(est, c).lam_cartoes_amarelos_restantes == pytest.approx(
        live_predictor.prever(est, ritmo_suavizado=("cartoes",)).lam_cartoes_amarelos_restantes)
