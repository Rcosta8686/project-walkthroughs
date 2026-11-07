"""Mini-cerebro dos arbitros: media real de amarelos nos nossos jogos."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.modelos import arbitros as arb
from apostas.modelos import cartoes_finais as cf
from apostas.modelos import correcao_ritmo as cr
from apostas.modelos import live_predictor
from apostas.modelos.priors_equipas import PriorsJogo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 12)


def _jogo(ev, arbitro, cards, dias_atras=5):
    d = AGORA - timedelta(days=dias_atras)
    a, b = cards // 2, cards - cards // 2
    return [SimpleNamespace(sofascore_event_id=ev, data_jogo=d, arbitro=arbitro, cards_amarelos=a),
            SimpleNamespace(sofascore_event_id=ev, data_jogo=d, arbitro=arbitro, cards_amarelos=b)]


def _base(n=40, cards=4):
    """n jogos 'normais' de arbitros variados: media geral = cards."""
    return [r for i in range(n) for r in _jogo(1000 + i, f"Normal {i % 10}", cards)]


def test_arbitro_rigoroso_tem_fator_acima_de_1_puxado_para_a_media():
    linhas = _base() + [r for i in range(12) for r in _jogo(i, "Rigoroso", 8)]
    h = arb.HistoricoArbitros(linhas)
    p = h.perfil("Rigoroso")
    assert p is not None and p.n == 12 and p.media == 8.0
    media_geral = h.media_geral
    esperado = (12 * 8 + arb.K_ENCOLHIMENTO * media_geral) / (12 + arb.K_ENCOLHIMENTO) / media_geral
    assert p.fator == pytest.approx(esperado, abs=0.001)
    assert 1.0 < p.fator < 8 / media_geral          # encolhido, nao o valor cru
    assert "8.0 amarelos/jogo nos nossos 12 jogos" in p.texto()


def test_nome_do_live_encontra_o_da_bd():
    h = arb.HistoricoArbitros(_base() + [r for i in range(6) for r in _jogo(i, "João Pinheiro", 7)]
                              + [r for i in range(6) for r in _jogo(50 + i, "A & B", 7)])
    for nome in ("João Pinheiro", "joao pinheiro", "  JOÃO  PINHEIRO "):
        p = h.perfil(nome)
        assert p is not None and p.nome == "João Pinheiro" and p.n == 6
    assert h.perfil("A &amp; B").n == 6     # live sem descodificar


def test_poucos_jogos_nao_ha_perfil():
    h = arb.HistoricoArbitros(_base() + [r for i in range(arb.MIN_JOGOS - 1)
                                         for r in _jogo(i, "Novo", 9)])
    assert h.perfil("Novo") is None and h.perfil(None) is None and h.perfil("") is None


def test_sem_batota_so_jogos_anteriores():
    linhas = _base() + [r for i in range(6) for r in _jogo(i, "X", 8, dias_atras=10 + i)]
    linhas += [r for i in range(6) for r in _jogo(100 + i, "X", 8, dias_atras=1)]
    h = arb.HistoricoArbitros(linhas)
    assert h.perfil("X").n == 12
    assert h.perfil("X", antes=AGORA - timedelta(days=5)).n == 6
    assert h.perfil("X", antes=AGORA - timedelta(days=30)) is None


def test_poucos_jogos_no_total_usa_media_por_defeito():
    h = arb.HistoricoArbitros([r for i in range(10) for r in _jogo(i, "Y", 6)])
    assert h.media_geral == arb.MEDIA_DEFAULT


def test_jogo_incompleto_nao_conta():
    meio = [_jogo(1, "Z", 6)[0]]                       # so uma equipa
    sem_cartoes = _jogo(2, "Z", 6)
    sem_cartoes[0].cards_amarelos = None
    h = arb.HistoricoArbitros(meio + sem_cartoes)
    assert h._jogos == {}


def test_ranking_e_texto_do_relatorio():
    linhas = _base()
    linhas += [r for i in range(10) for r in _jogo(i, "Duro", 8)]
    linhas += [r for i in range(10) for r in _jogo(50 + i, "Brando", 1)]
    h = arb.HistoricoArbitros(linhas)
    r = h.ranking()
    assert r[0].nome == "Duro" and r[-1].nome == "Brando"
    txt = arb.formatar_ranking(h)
    assert "Mais cartoes: Duro 8.0 (10j)" in txt and "Menos cartoes: Brando 1.0 (10j)" in txt
    assert "Ainda sem arbitros" in arb.formatar_ranking(arb.HistoricoArbitros([]))


# --- modelo live -------------------------------------------------------------

def _estado(minuto=30, arbitro=None):
    from apostas.ingestao.sofascore_live import EstadoLive
    return EstadoLive(event_id=1, home="A", away="B", liga="L", minuto=minuto, estado="1H",
                      marcador_casa=0, marcador_fora=0, shots_total_casa=4, shots_total_fora=3,
                      shots_on_target_casa=1, shots_on_target_fora=1, corners_casa=2,
                      corners_fora=1, yellow_cards_casa=1, yellow_cards_fora=1, arbitro=arbitro)


def test_prever_usa_o_fator_do_arbitro_dado():
    est = _estado()
    todos = live_predictor.MERCADOS_SUAVIZAVEIS
    normal = live_predictor.prever(est, ritmo_suavizado=todos, fator_arbitro=1.0)
    duro = live_predictor.prever(est, ritmo_suavizado=todos, fator_arbitro=1.3)
    assert duro.lam_cartoes_amarelos_restantes == pytest.approx(
        normal.lam_cartoes_amarelos_restantes * 1.3, rel=0.01)


def test_priors_levam_o_arbitro_so_quando_conhecido():
    pr = PriorsJogo(2.6, 10.5, 4.0, 12, 12, 20, 20)
    assert "fator_arbitro" not in pr.kwargs()
    pr.fator_arbitro = 1.2
    assert pr.kwargs()["fator_arbitro"] == 1.2


# --- cartao no fim -----------------------------------------------------------

def _estado_fim(**kw):
    base = dict(event_id=321, home="Porto", away="Benfica", liga="Liga Portugal", minuto=75,
                marcador_casa=1, marcador_fora=1, yellow_cards_casa=2, yellow_cards_fora=2,
                red_cards_casa=0, red_cards_fora=0, fouls_casa=14, fouls_fora=13, arbitro=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _historia():
    return cf.Historia({(75, "empate", "altas"): cf.Taxa(n=120, mais1=98, mais2=54)})


def _prev(lam):
    return SimpleNamespace(lam_cartoes_amarelos_restantes=lam)


def test_cartao_no_fim_diz_o_arbitro_real():
    s = cf.avaliar(_estado_fim(), _prev(2.0), _historia(), fator_arbitro=1.25,
                   arbitro_txt="arbitro Duro: 6.0 amarelos/jogo nos nossos 14 jogos")
    assert s is not None
    assert any("Duro: 6.0 amarelos/jogo nos nossos 14 jogos (+25% que o normal)" in r
               for r in s.razoes)


def test_arbitro_permissivo_baixa_a_probabilidade_e_avisa():
    # modelo 1.4 cartoes -> 75%; com arbitro brando (x0.75) -> 65%: nao envia
    assert cf.avaliar(_estado_fim(), _prev(1.4), _historia()) is not None
    assert cf.avaliar(_estado_fim(), _prev(1.4), _historia(), ajuste_lam=0.75) is None
    s = cf.avaliar(_estado_fim(), _prev(3.0), _historia(), fator_arbitro=0.8,
                   arbitro_txt="arbitro Brando: 3.0 amarelos/jogo nos nossos 10 jogos",
                   ajuste_lam=0.8)
    assert s is not None and any(r.startswith("atencao: arbitro Brando") for r in s.razoes)


def test_scanner_troca_o_fator_fixo_pelo_da_bd(monkeypatch):
    import scanner_live_v2 as sc
    from apostas.modelos import referencias
    monkeypatch.setattr(referencias, "fator_arbitro", lambda nome: 1.1)
    monkeypatch.setattr(sc, "_ritmo_live", lambda: cr.CorrecaoRitmo(bases={"cartoes": "suave"}))
    est = SimpleNamespace(event_id=9, home_id=1, away_id=2, arbitro="Duro")
    assert sc._ajuste_arbitro_cartoes(est, 1.32) == pytest.approx(1.2)
    # se o modelo de cartoes ja usa a versao 'equipas' (que leva o arbitro da BD): nada a corrigir
    monkeypatch.setattr(sc, "_ritmo_live", lambda: cr.CorrecaoRitmo(bases={"cartoes": "equipas"}))
    monkeypatch.setattr(sc, "_priors_jogo", lambda e, r: PriorsJogo(2.6, 10.5, 4, 12, 12, 9, 9))
    assert sc._ajuste_arbitro_cartoes(est, 1.32) == 1.0


def test_perfil_live_carrega_uma_vez(monkeypatch):
    chamadas = []
    h = arb.HistoricoArbitros(_base() + [r for i in range(10) for r in _jogo(i, "Duro", 8)])
    monkeypatch.setattr(arb, "_cache", None)
    monkeypatch.setattr(arb, "da_bd", lambda s: chamadas.append(1) or h)

    class _Sess:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False
    from apostas.utils import db as db_mod
    monkeypatch.setattr(db_mod, "abrir_sessao", lambda: _Sess())
    assert arb.perfil_live("Duro").n == 10
    assert arb.perfil_live("Ninguem") is None and arb.perfil_live(None) is None
    assert chamadas == [1]


# --- medicao nos jogos passados ----------------------------------------------

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


def test_medicao_usa_o_arbitro_com_os_jogos_dele_de_antes(bd, monkeypatch):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def eej(ev, team, casa, dias, cards, arbitro):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa=f"T{team}", era_casa=casa,
                                     data_jogo=AGORA - timedelta(days=dias), resultado="D",
                                     golos_marcados=1, golos_sofridos=1, cantos=5,
                                     cards_amarelos=cards, remates=12, sot=4, arbitro=arbitro)
    with abrir_sessao() as s:
        for i in range(40):   # jogos normais (2+2 cartoes) com outros arbitros
            s.add_all([eej(800 + i, 10 if i < 10 else 200 + i, True, 10 + i, 2, f"N{i % 7}"),
                       eej(800 + i, 11 if i < 10 else 300 + i, False, 10 + i, 2, f"N{i % 7}")])
        for i in range(12):   # o arbitro Duro: 10 cartoes por jogo
            s.add_all([eej(900 + i, 400 + i, True, 5 + i, 5, "Duro"),
                       eej(900 + i, 500 + i, False, 5 + i, 5, "Duro")])
        s.add_all([eej(1, 10, True, 1, 0, "Duro"), eej(1, 11, False, 1, 1, "Duro")])
        s.add(EstadoLiveSnapshot(event_id=1, ts=AGORA - timedelta(days=1), home="T10",
                                 away="T11", liga="L", minuto=30, marcador_casa=0,
                                 marcador_fora=0, shots_total_casa=4, shots_total_fora=3,
                                 shots_on_target_casa=1, shots_on_target_fora=1,
                                 corners_casa=2, corners_fora=1, yellow_casa=0, yellow_fora=0,
                                 arbitro="Duro"))

    def cartoes():
        with abrir_sessao() as s:
            return next(p for p in cr.pares_da_bd(s, agora=AGORA) if p["mercado"] == "cartoes")
    com_arbitro = cartoes()
    monkeypatch.setattr(arb.HistoricoArbitros, "perfil", lambda self, nome, antes=None: None)
    sem_arbitro = cartoes()
    assert com_arbitro["previsto_equipas"] > sem_arbitro["previsto_equipas"] * 1.2
    assert com_arbitro["previsto_suave"] == sem_arbitro["previsto_suave"]   # so a versao equipas
