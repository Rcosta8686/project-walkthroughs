"""Cartao nos minutos finais: jogo tenso + modelo e historia de acordo."""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.modelos import cartoes_finais as cf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 22, 0)


def _estado(minuto=75, mc=1, mf=1, yc=2, yf=2, fc=14, ff=13, reds=0):
    return SimpleNamespace(event_id=321, home="Porto", away="Benfica", liga="Liga Portugal",
                           minuto=minuto, marcador_casa=mc, marcador_fora=mf,
                           yellow_cards_casa=yc, yellow_cards_fora=yf, red_cards_casa=reds,
                           red_cards_fora=0, fouls_casa=fc, fouls_fora=ff, arbitro=None)


def _historia(p1=0.82, p2=0.45, n=120, chave=(75, "empate", "altas")):
    return cf.Historia({chave: cf.Taxa(n=n, mais1=round(p1 * n), mais2=round(p2 * n))})


def _prev(lam):
    return SimpleNamespace(lam_cartoes_amarelos_restantes=lam)


def test_jogo_tenso_com_modelo_e_historia_de_acordo_da_sinal():
    s = cf.avaliar(_estado(), _prev(2.0), _historia())
    assert s is not None
    assert s.linha == 4.5 and s.amarelos == 4
    assert s.p1 == pytest.approx(min(1 - math.exp(-2.0), 98 / 120), abs=0.001)
    assert s.n_historia == 120 and s.situacao == "75' · empate · faltas altas"
    assert any("faltas acima do normal" in r for r in s.razoes)
    assert s.odd_minima == round(1.05 / s.p1, 2)


def test_jogo_decidido_nao_interessa():
    assert cf.avaliar(_estado(mc=3, mf=1), _prev(2.0), _historia()) is None


def test_sem_tensao_alem_do_marcador_nao_envia():
    calmo = _estado(yc=0, yf=1, fc=8, ff=7)   # empate mas faltas e cartoes normais
    assert cf.avaliar(calmo, _prev(2.0), _historia(chave=(75, "empate", "normais"))) is None


def test_as_duas_fontes_tem_de_concordar():
    assert cf.avaliar(_estado(), _prev(0.6), _historia()) is None            # modelo 45%
    assert cf.avaliar(_estado(), _prev(2.0), _historia(p1=0.55)) is None     # historia 55%
    assert cf.avaliar(_estado(), _prev(2.0), _historia(n=10)) is None        # pouca historia


def test_fora_da_janela_nao_envia():
    assert cf.avaliar(_estado(minuto=60), _prev(2.0), _historia()) is None
    assert cf.avaliar(_estado(minuto=85), _prev(2.0), _historia()) is None


def test_historia_cai_para_o_grupo_mais_geral():
    h = cf.Historia({(75, "1 golo", "altas"): cf.Taxa(5, 5, 3),
                     (75, "1 golo"): cf.Taxa(200, 160, 80)})
    achado = h.procurar(76, 2, 1, 15, 14)
    assert achado is not None
    chave, taxa = achado
    assert chave == (75, "1 golo") and taxa.p1 == 0.8


def test_equipa_com_mais_faltas_e_cartoes_e_a_mais_provavel():
    est = _estado(mc=0, mf=1, yc=3, yf=1, fc=18, ff=9)   # casa a perder, mais faltas
    s = cf.avaliar(est, _prev(2.0), _historia(chave=(75, "1 golo", "altas")))
    assert s is not None and s.equipa == "Porto" and s.p_equipa > 0.5
    assert any("Porto a perder" in r for r in s.razoes)


def test_mensagem_diz_a_aposta_e_o_fundamento():
    s = cf.avaliar(_estado(), _prev(2.0), _historia())
    assert s is not None
    txt = cf.formatar_telegram(s, _estado())
    assert "CARTAO NO FIM" in txt and "OVER 4.5 amarelos" in txt
    assert "em 120 jogos guardados em momento parecido (75' · empate · faltas altas)" in txt
    assert "bonus: OVER 5.5" in txt and "Mais provavel levar o proximo" in txt
    assert "so se a casa pagar" not in txt


def test_gravar_ler_e_resumo(tmp_path):
    f = tmp_path / "h.json"
    cf.gravar(_historia(), f)
    assert cf.ler(f).taxas[(75, "empate", "altas")].n == 120
    h = cf.Historia({(75, "empate"): cf.Taxa(50, 41, 20)})
    assert "75': empate 82% (mais 2: 40%, n=50)" in cf.formatar_historia(h)


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


def test_historia_da_bd(bd):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def snap(ev, minuto, y):
        return EstadoLiveSnapshot(event_id=ev, ts=AGORA - timedelta(days=2), home="A",
                                  away="B", liga="L", minuto=minuto, marcador_casa=1,
                                  marcador_fora=1, yellow_casa=y, yellow_fora=1,
                                  fouls_casa=12, fouls_fora=12)

    def fim(ev, team, cards):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa="X", era_casa=team == 1,
                                     data_jogo=AGORA - timedelta(days=2), resultado="D",
                                     cards_amarelos=cards)
    with abrir_sessao() as s:
        s.add_all([snap(1, 76, 2), snap(1, 78, 3), fim(1, 1, 3), fim(1, 2, 2),  # +2 depois dos 76'
                   snap(2, 75, 1), fim(2, 1, 1), fim(2, 2, 1)])                 # +0
    with abrir_sessao() as s:
        h = cf.historia_da_bd(s, agora=AGORA)
    t = h.taxas[(75, "empate")]
    assert (t.n, t.mais1, t.mais2) == (2, 1, 1)                # 1 momento por jogo e fase
    assert h.taxas[(75, "empate", "altas")].n == 2             # 24 faltas aos 75' = 28.8/90


def test_scanner_envia_uma_vez_e_regista(monkeypatch):
    import scanner_live_v2 as sc
    from apostas.modelos import referencias
    enviadas, registos = [], []
    sc._cooldown.clear()
    monkeypatch.setattr(sc, "_historia_cartoes_live", lambda: _historia())
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sc.alertas_enviados, "registar", lambda **kw: registos.append(kw))
    monkeypatch.setattr(referencias, "fator_arbitro", lambda nome: 1.0)
    monkeypatch.setattr(sc, "_odd_real", lambda *a: None)
    assert sc._alerta_cartoes_fim(_estado(), _prev(2.0)) is True
    assert sc._alerta_cartoes_fim(_estado(minuto=78), _prev(2.0)) is False   # ja enviado
    assert len(enviadas) == 1
    assert len(registos) == 1 and registos[0]["tipo"] == "cartoes_fim"
    assert (registos[0]["mercado"], registos[0]["lado"], registos[0]["linha"]) == \
        ("cartoes", "over", 4.5)
    sc._cooldown.clear()


def test_cartao_no_fim_resolve_no_relatorio_do_dia(tmp_path):
    from apostas.relatorios import alertas_enviados as ae
    reg = tmp_path / "env.csv"
    ae.registar(tipo="cartoes_fim", ts=AGORA, path=reg, event_id=321, mercado="cartoes",
                lado="over", linha=4.5, odd=1.25)
    inicio = AGORA.replace(hour=0)
    fim = inicio + timedelta(days=1)
    assert ae.projeccoes_no_periodo(inicio, fim, reg) == {(321, "cartoes")}
    dicas = ae.listar(inicio, fim, tmp_path / "x.csv", reg, totais_proj={(321, "cartoes"): 6.0})
    assert dicas[0].resultado == "W" and dicas[0].lucro == pytest.approx(0.25)
    assert "(fim)" in ae.formatar_lista(dicas, {321: ("Porto", "Benfica")})
    assert "cartão no fim" in dicas[0].grupo
