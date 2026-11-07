"""O normal de cada jogo a partir dos mini-cerebros das duas equipas."""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.modelos import correcao_ritmo as cr
from apostas.modelos import live_predictor
from apostas.modelos.priors_equipas import GENERICO, HistoricoEquipas, PriorsJogo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 12)


def _jogo(ev, casa, fora, dias_atras, cards=(3, 3), cantos=(5, 5), remates=(12, 12),
          golos=(1, 1)):
    d = AGORA - timedelta(days=dias_atras)
    return [SimpleNamespace(sofascore_event_id=ev, sofascore_team_id=casa, data_jogo=d,
                            era_casa=True, golos_marcados=golos[0], golos_sofridos=golos[1],
                            remates=remates[0], sot=4, cantos=cantos[0], cards_amarelos=cards[0]),
            SimpleNamespace(sofascore_event_id=ev, sofascore_team_id=fora, data_jogo=d,
                            era_casa=False, golos_marcados=golos[1], golos_sofridos=golos[0],
                            remates=remates[1], sot=3, cantos=cantos[1], cards_amarelos=cards[1])]


def test_equipas_de_muitos_cartoes_tem_normal_acima_do_generico():
    linhas = []
    for i in range(20):  # equipa 1 e equipa 2: jogos com 7 cartoes no total
        linhas += _jogo(100 + i, 1, 50 + i, i + 1, cards=(4, 3))
        linhas += _jogo(200 + i, 2, 70 + i, i + 1, cards=(3, 4))
    p = HistoricoEquipas(linhas).priors(1, 2)
    assert p is not None and p.n_casa == 20 and p.n_fora == 20
    assert p.cartoes == pytest.approx((20 * 7 + 5 * 4.0) / 25, abs=0.01)
    assert p.cartoes > GENERICO["cartoes"]


def test_poucos_jogos_ficam_perto_do_generico():
    linhas = _jogo(1, 1, 9, 2, cards=(6, 6)) + _jogo(2, 2, 8, 2, cards=(6, 6))
    p = HistoricoEquipas(linhas).priors(1, 2)
    assert p is not None and GENERICO["cartoes"] < p.cartoes < 6.0   # 1 jogo: (12 + 20)/6


def test_remates_misturam_ataque_e_o_que_o_adversario_deixa():
    linhas = []
    for i in range(20):
        linhas += _jogo(100 + i, 1, 50 + i, i + 1, remates=(20, 10))   # casa remata 20
        linhas += _jogo(200 + i, 2, 70 + i, i + 1, remates=(8, 16))    # fora deixa rematar 16
    p = HistoricoEquipas(linhas).priors(1, 2)
    assert p is not None
    atq = (20 * 20 + 5 * 12) / 25
    conc = (20 * 16 + 5 * 12) / 25
    assert p.remates_casa == pytest.approx((atq + conc) / 2, abs=0.01)


def test_sem_batota_com_jogos_futuros():
    linhas = _jogo(1, 1, 9, 10, cards=(2, 2)) + _jogo(2, 1, 8, 1, cards=(9, 9))
    hist = HistoricoEquipas(linhas)
    antes = AGORA - timedelta(days=5)
    assert len(hist.jogos(1, antes=antes)) == 1
    assert hist.priors(1, 9, antes=antes).cartoes < hist.priors(1, 9).cartoes


def test_sem_historico_nao_ha_priors():
    assert HistoricoEquipas([]).priors(1, 2) is None
    assert HistoricoEquipas([]).priors(None, 2) is None


def _estado(minuto=30, cards=(0, 0)):
    from apostas.ingestao.sofascore_live import EstadoLive
    return EstadoLive(event_id=1, home="A", away="B", liga="L", minuto=minuto, estado="1H",
                      marcador_casa=0, marcador_fora=0, shots_total_casa=4, shots_total_fora=3,
                      shots_on_target_casa=1, shots_on_target_fora=1, corners_casa=2,
                      corners_fora=1, yellow_cards_casa=cards[0], yellow_cards_fora=cards[1])


def test_modelo_usa_o_normal_das_equipas_no_ritmo_suavizado():
    est = _estado()
    todos = live_predictor.MERCADOS_SUAVIZAVEIS
    generico = live_predictor.prever(est, ritmo_suavizado=todos)
    pr = PriorsJogo(golos=2.6, cantos=10.5, cartoes=7.0, remates_casa=18, remates_fora=12,
                    n_casa=20, n_fora=20)
    com_equipas = live_predictor.prever(est, ritmo_suavizado=todos, **pr.kwargs())
    assert com_equipas.lam_cartoes_amarelos_restantes > generico.lam_cartoes_amarelos_restantes
    assert com_equipas.projeccao_shots_casa > generico.projeccao_shots_casa
    # sem ritmo suavizado nem priors: igual ao modelo antigo
    assert live_predictor.prever(est).lam_cartoes_amarelos_restantes == 0.0


def test_prever_corrigido_junta_versoes_por_mercado():
    est = _estado()
    pr = PriorsJogo(golos=2.6, cantos=10.5, cartoes=7.0, remates_casa=18, remates_fora=12,
                    n_casa=20, n_fora=20)
    c = cr.CorrecaoRitmo(bases={"cartoes": "equipas", "cantos": "suave"})
    prev = cr.prever_corrigido(est, c, priors=pr)
    todos = live_predictor.MERCADOS_SUAVIZAVEIS
    eq = live_predictor.prever(est, ritmo_suavizado=todos, **pr.kwargs())
    su = live_predictor.prever(est, ritmo_suavizado=todos)
    antigo = live_predictor.prever(est)
    assert prev.lam_cartoes_amarelos_restantes == pytest.approx(eq.lam_cartoes_amarelos_restantes)
    assert prev.lam_cantos_restantes == pytest.approx(su.lam_cantos_restantes)
    assert prev.lam_shots_restantes == pytest.approx(antigo.lam_shots_restantes)  # antigo
    # sem priors para este jogo, a versao 'equipas' cai no normal generico
    sem = cr.prever_corrigido(est, c, priors=None)
    assert sem.lam_cartoes_amarelos_restantes == pytest.approx(su.lam_cartoes_amarelos_restantes)


def _pares(previstos: dict, n=300, seed=3):
    rng = random.Random(seed)
    out = []
    for e in range(n):
        real = float(sum(1 for _ in range(12) if rng.random() < 0.25))  # ~3
        for j in cr.JANELAS:
            out.append({"mercado": "cartoes", "janela": j, "event_id": e, "real": real,
                        "data": AGORA - timedelta(hours=n - e), **previstos})
    return out


def test_escolhe_a_versao_das_equipas_quando_erra_menos():
    c = cr.calcular(_pares({"previsto": 0.3, "previsto_suave": 2.0, "previsto_equipas": 3.0}),
                    agora=AGORA)
    assert c.bases == {"cartoes": "equipas"} and c.com_equipas == {"cartoes"}
    assert "normal das duas equipas" in cr.formatar(c)


def test_nao_troca_de_versao_por_uma_diferenca_minima():
    pares = _pares({"previsto": 0.3, "previsto_suave": 3.0, "previsto_equipas": 2.99})
    anterior = cr.CorrecaoRitmo(bases={"cartoes": "suave"},
                                datas_ativacao={"cartoes": AGORA - timedelta(days=9)})
    c = cr.calcular(pares, anterior=anterior, agora=AGORA)
    assert c.bases["cartoes"] == "suave"
    assert c.datas_ativacao["cartoes"] == AGORA - timedelta(days=9)


def test_mudar_de_versao_recomeca_a_data_para_o_calibrador():
    pares = _pares({"previsto": 0.3, "previsto_suave": 1.5, "previsto_equipas": 3.0})
    anterior = cr.CorrecaoRitmo(bases={"cartoes": "suave"},
                                datas_ativacao={"cartoes": AGORA - timedelta(days=9)})
    c = cr.calcular(pares, anterior=anterior, agora=AGORA)
    assert c.bases["cartoes"] == "equipas" and c.datas_ativacao["cartoes"] == AGORA


def test_ficheiro_antigo_so_com_suavizados_continua_a_ler(tmp_path):
    f = tmp_path / "ritmo.json"
    f.write_text('{"ativos": [], "suavizados": ["cantos"], "mults": []}', encoding="utf-8")
    assert cr.ler(f).bases == {"cantos": "suave"}


def test_scanner_so_vai_a_bd_buscar_priors_quando_sao_precisos(monkeypatch):
    import scanner_live_v2 as sc
    est = SimpleNamespace(event_id=4242, home_id=1, away_id=2)
    sc._priors_cache.clear()
    assert sc._priors_jogo(est, cr.CorrecaoRitmo(bases={"cantos": "suave"})) is None
    chamadas = []

    class _Hist:
        def priors(self, a, b):
            chamadas.append((a, b))
            return PriorsJogo(2.6, 10.5, 5.0, 12, 12, 10, 10)
    monkeypatch.setattr(sc.priors_equipas, "carregar_do_bd", lambda s, eqs: _Hist())
    ritmo = cr.CorrecaoRitmo(bases={"cartoes": "equipas"})
    assert sc._priors_jogo(est, ritmo).cartoes == 5.0
    sc._priors_jogo(est, ritmo)
    assert chamadas == [(1, 2)]            # uma vez por jogo
    sc._priors_cache.clear()


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


def test_medicao_usa_so_jogos_anteriores_das_duas_equipas(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def eej(ev, team, casa, dias, cards):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa=f"T{team}", era_casa=casa,
                                     data_jogo=AGORA - timedelta(days=dias), resultado="D",
                                     golos_marcados=1, golos_sofridos=1, cantos=5,
                                     cards_amarelos=cards, remates=12, sot=4)
    with abrir_sessao() as s:
        for i in range(10):  # equipas 10 e 11: historico de jogos com 8 cartoes
            s.add_all([eej(500 + i, 10, True, 10 + i, 4), eej(500 + i, 90 + i, False, 10 + i, 4),
                       eej(600 + i, 11, True, 10 + i, 4), eej(600 + i, 70 + i, False, 10 + i, 4)])
        # o jogo medido (1 dia atras)
        s.add_all([eej(1, 10, True, 1, 0), eej(1, 11, False, 1, 1)])
        s.add(EstadoLiveSnapshot(event_id=1, ts=AGORA - timedelta(days=1), home="T10",
                                 away="T11", liga="L", minuto=30, marcador_casa=0,
                                 marcador_fora=0, shots_total_casa=4, shots_total_fora=3,
                                 shots_on_target_casa=1, shots_on_target_fora=1,
                                 corners_casa=2, corners_fora=1, yellow_casa=0, yellow_fora=0))
    with abrir_sessao() as s:
        pares = cr.pares_da_bd(s, agora=AGORA)
    cartoes = [p for p in pares if p["mercado"] == "cartoes"]
    assert len(cartoes) == 1
    p = cartoes[0]
    assert p["previsto"] == 0.0                       # antigo: 0 cartoes aos 30' -> 0
    assert p["previsto_equipas"] > p["previsto_suave"] > 0
    assert p["real"] == 1.0
