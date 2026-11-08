"""Vao sair mais golos: jogo com pressao + modelo e historia de acordo."""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.modelos import golos_finais as gf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 7, 22, 0)


def _estado(minuto=62, mc=1, mf=1, rc=14, rf=12, sc=5, sf=4, xc=None, xf=None,
            reds=0, ofensivas=0):
    return SimpleNamespace(event_id=654, home="Porto", away="Benfica", liga="Liga Portugal",
                           minuto=minuto, marcador_casa=mc, marcador_fora=mf,
                           shots_total_casa=rc, shots_total_fora=rf,
                           shots_on_target_casa=sc, shots_on_target_fora=sf,
                           xg_casa=xc, xg_fora=xf, big_chances_casa=None, big_chances_fora=None,
                           red_cards_casa=reds, red_cards_fora=0,
                           subs_ofensivas_casa=ofensivas, subs_ofensivas_fora=0)


def _historia(p1=0.78, p2=0.40, n=150, chave=(60, "empate", "muitos remates")):
    return gf.Historia({chave: gf.Taxa(n=n, mais1=round(p1 * n), mais2=round(p2 * n))})


def _prev(lam, casa=None):
    casa = lam * 0.6 if casa is None else casa
    return SimpleNamespace(lam_golos_restantes=lam, lam_golos_casa_rest=casa,
                           lam_golos_fora_rest=lam - casa)


def test_jogo_com_pressao_modelo_e_historia_de_acordo_da_sinal():
    s = gf.avaliar(_estado(), _prev(1.8), _historia())
    assert s is not None
    assert s.linha == 2.5 and s.golos == 2
    assert s.p1 == pytest.approx(min(1 - math.exp(-1.8), 117 / 150), abs=0.001)
    assert s.situacao == "60' · empate · muitos remates" and s.n_historia == 150
    assert any("remates acima do normal" in r for r in s.razoes)
    assert s.equipa == "Porto" and s.p_equipa == pytest.approx(0.6)
    txt = gf.formatar_telegram(s, _estado())
    assert "*VAO SAIR MAIS GOLOS*" in txt and "*OVER 2.5 golos* (sai pelo menos mais 1)" in txt
    assert "Mais provavel marcar o proximo: *Porto* (60%)" in txt


def test_sem_pressao_nao_envia():
    calmo = _estado(rc=6, rf=5, sc=1, sf=1)        # empate, poucos remates, nada mais
    assert gf.razoes_pressao(calmo) == []
    assert gf.avaliar(calmo, _prev(1.8), _historia(chave=(60, "empate", "normal"))) is None


def test_outros_sinais_de_pressao():
    r = gf.razoes_pressao(_estado(mc=0, mf=1, rc=6, rf=5, sc=1, sf=1, xc=1.4, xf=0.6,
                                  reds=1, ofensivas=2))
    assert any("Porto a perder por 1" in x for x in r)
    assert any("xG 2.0 vs 1 golos" in x for x in r)
    assert any("vermelho" in x for x in r) and any("2 substituicao" in x for x in r)


def test_as_duas_fontes_tem_de_concordar_e_janela():
    assert gf.avaliar(_estado(), _prev(0.7), _historia()) is None          # modelo 50%
    assert gf.avaliar(_estado(), _prev(1.8), _historia(p1=0.6)) is None    # historia 60%
    assert gf.avaliar(_estado(), _prev(1.8), _historia(n=20)) is None      # pouca historia
    assert gf.avaliar(_estado(minuto=40), _prev(1.8), _historia()) is None
    assert gf.avaliar(_estado(minuto=85), _prev(1.8), _historia()) is None
    # historia geral da fase quando a especifica tem poucos
    geral = gf.Historia({(60, "empate", "muitos remates"): gf.Taxa(10, 9, 5),
                         (60, "empate"): gf.Taxa(200, 160, 80)})
    s = gf.avaliar(_estado(), _prev(1.8), geral)
    assert s is not None and s.situacao == "60' · empate" and s.n_historia == 200


def test_linha_curta_nos_outros_alertas():
    assert gf.linha_mais_golos(_estado(minuto=30), _prev(2.5)) == ""        # 1a parte
    assert gf.linha_mais_golos(_estado(), _prev(0.6)) == ""                 # 45%
    linha = gf.linha_mais_golos(_estado(), _prev(1.5))
    assert linha.startswith("⚽ Mais golos: *78%* de sair pelo menos mais 1 (over 2.5)")
    assert "Porto mais provavel (60%)" in linha


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


def test_historia_da_bd_e_ficheiro(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo

    def snap(ev, minuto, mc, mf, remates=10):
        return EstadoLiveSnapshot(event_id=ev, ts=AGORA - timedelta(days=2), home="A",
                                  away="B", liga="L", minuto=minuto, marcador_casa=mc,
                                  marcador_fora=mf, shots_total_casa=remates,
                                  shots_total_fora=remates)

    def fim(ev, team, golos):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa="X", era_casa=team == 1,
                                     data_jogo=AGORA - timedelta(days=2), resultado="D",
                                     golos_marcados=golos, golos_sofridos=0)
    with abrir_sessao() as s:
        s.add_all([snap(1, 61, 1, 1), snap(1, 63, 1, 1), fim(1, 1, 2), fim(1, 2, 2),  # +2
                   snap(2, 60, 0, 0, remates=4), fim(2, 1, 0), fim(2, 2, 0),            # +0
                   snap(3, 62, 2, 0), fim(3, 1, 1), fim(3, 2, 0)])                      # incoerente
    with abrir_sessao() as s:
        h = gf.historia_da_bd(s, agora=AGORA)
    t = h.taxas[(60, "empate")]
    assert (t.n, t.mais1, t.mais2) == (2, 1, 1)               # 1 momento por jogo e fase
    assert h.taxas[(60, "empate", "muitos remates")].n == 1    # 20 remates aos 61' = 29.5/90
    assert (60, "2+ golos") not in h.taxas                     # final abaixo do marcador
    f = tmp_path / "golos.json"
    gf.gravar(h, f)
    assert gf.ler(f).taxas[(60, "empate")].mais1 == 1
    assert "60': empate 50%" not in gf.formatar_historia(h)    # n < 10: nao mostra
    assert gf.ler(tmp_path / "nao_existe.json").taxas == {}


def test_scanner_envia_uma_vez_e_regista(monkeypatch):
    import scanner_live_v2 as sc
    enviadas, registos = [], []
    sc._cooldown.clear()
    monkeypatch.setattr(sc, "_historia_golos_live", lambda: _historia())
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sc.alertas_enviados, "registar", lambda **kw: registos.append(kw))
    monkeypatch.setattr(sc, "_odd_real", lambda *a: None)
    assert sc._alerta_mais_golos(_estado(), _prev(1.8)) is True
    assert sc._alerta_mais_golos(_estado(minuto=66), _prev(1.8)) is False   # ja enviado
    assert len(enviadas) == 1 and "VAO SAIR MAIS GOLOS" in enviadas[0]
    assert len(registos) == 1 and registos[0]["tipo"] == "mais_golos"
    assert (registos[0]["mercado"], registos[0]["lado"], registos[0]["linha"]) == \
        ("golos", "over", 2.5)
    sc._cooldown.clear()


def test_mais_golos_resolve_e_aparece_no_grafico(tmp_path, bd):
    from apostas.relatorios import alertas_enviados as ae
    from apostas.relatorios import grafico_acertos as ga
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo
    reg = tmp_path / "env.csv"
    ae.registar(tipo="mais_golos", ts=AGORA - timedelta(hours=2), path=reg, event_id=654,
                mercado="golos", lado="over", linha=2.5, odd=1.3)
    ae.registar(tipo="mais_golos", ts=AGORA - timedelta(hours=1), path=reg, event_id=655,
                mercado="golos", lado="over", linha=1.5, odd=1.35)
    with abrir_sessao() as s:
        s.add_all([EstatisticaEquipaJogo(sofascore_event_id=e, sofascore_team_id=t,
                                         nome_equipa="X", era_casa=t == 1, data_jogo=AGORA,
                                         resultado="D", golos_marcados=g, golos_sofridos=0)
                   for e, t, g in ((654, 1, 2), (654, 2, 1), (655, 1, 1), (655, 2, 0))])
    inicio = AGORA.replace(hour=0)
    fim = inicio + timedelta(days=1)
    chaves = ae.projeccoes_no_periodo(inicio, fim, reg)
    with abrir_sessao() as s:
        totais = ae.totais_finais_bd(s, chaves)
    assert totais == {(654, "golos"): 3.0, (655, "golos"): 1.0}
    dicas = ae.listar(inicio, fim, tmp_path / "sug.csv", reg, totais_proj=totais)
    assert [d.resultado for d in dicas] == ["W", "L"]
    assert dicas[0].grupo == "⚽ golos over (mais golos)"
    assert "(mais golos)" in ae.formatar_lista(dicas)
    _, mercados = ga.agrupar(dicas, "dia", fim)
    assert [(m.nome, m.acertos, m.falhas) for m in mercados] == \
        [("Golos over (mais golos)", 1, 1)]
