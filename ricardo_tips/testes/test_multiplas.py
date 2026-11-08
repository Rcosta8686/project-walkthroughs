"""Multiplas enviadas: registo, resultado de cada selecao com a BD e calibracao."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from apostas.relatorios import multiplas as mp

AGORA = datetime(2026, 11, 8, 0, 15)
ENVIO = datetime(2026, 11, 7, 16, 30)


def _sel(sujeito, mercado, linha, p, eid=7, sujeito_id=None, desc=""):
    return SimpleNamespace(jogo="Porto–Benfica 20:00", event_id=eid, jogador=sujeito,
                           player_id=-1, descricao=desc or f"{linha}+ {mercado}", p=p,
                           mercado=mercado, linha=linha, sujeito_id=sujeito_id)


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


def _jogo_na_bd():
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    with abrir_sessao() as s:
        s.add_all([
            EJJ(sofascore_player_id=1, sofascore_event_id=7, sofascore_team_id=100,
                nome_jogador="Avancado", posicao="ST", data_jogo=ENVIO, era_casa=1,
                minutos=90, remates=4, sot=1),
            EJJ(sofascore_player_id=2, sofascore_event_id=7, sofascore_team_id=200,
                nome_jogador="Trinco", posicao="CDM", data_jogo=ENVIO, era_casa=0,
                minutos=90, remates=0, sot=0, cards_amarelos=0, minuto_amarelo=70),
            EJJ(sofascore_player_id=3, sofascore_event_id=7, sofascore_team_id=100,
                nome_jogador="Suplente", posicao="CM", data_jogo=ENVIO, era_casa=1,
                minutos=0),
            EEJ(sofascore_event_id=7, sofascore_team_id=100, nome_equipa="Porto", era_casa=1,
                data_jogo=ENVIO, resultado="W", remates=15, sot=6, cantos=7, cards_amarelos=2),
            EEJ(sofascore_event_id=7, sofascore_team_id=200, nome_equipa="Benfica",
                era_casa=0, data_jogo=ENVIO, resultado="L", remates=9, sot=2, cantos=3,
                cards_amarelos=3),
        ])


def test_registo_e_resultado_de_cada_selecao(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    f = tmp_path / "multiplas.csv"
    segura = [_sel("Avancado", "remates", 3, 0.8, sujeito_id=1),         # 4 remates: W
              _sel("Avancado", "sot", 2, 0.6, sujeito_id=1),             # 1 a baliza: L
              _sel("Porto", "cantos_equipa", 5, 0.7, sujeito_id=100)]    # 7 cantos: W
    maluca = [_sel("Trinco", "cartao", 1, 0.3, sujeito_id=2),            # amarelo (incidente): W
              _sel("Suplente", "remates", 1, 0.5, sujeito_id=3),         # nao jogou: nula
              _sel("Jogo", "cartoes_jogo", 5, 0.5, sujeito_id=7),        # 2+3 = 5: W
              _sel("Benfica", "sot_equipa", 3, 0.5, sujeito_id=200),     # 2: L
              _sel("Outro", "remates", 1, 0.6, eid=99, sujeito_id=5)]    # jogo sem dados
    assert mp.registar("segura", segura, ENVIO, f) == "20261107163000-segura"
    mp.registar("maluca", maluca, ENVIO, f)
    assert mp.registar("maluca", [], ENVIO, f) is None
    _jogo_na_bd()
    with abrir_sessao() as s:
        ms = mp.carregar_resolvidas(s, path=f, agora=AGORA)
    seg, mal = ms
    assert [x.resultado for x in seg.selecoes] == ["W", "L", "W"]
    assert seg.resultado == "L" and seg.acertadas == 2 and seg.decididas == 3
    assert [x.resultado for x in mal.selecoes] == ["W", "nula", "W", "L", "pendente"]
    assert mal.resultado == "L"                 # ja perdeu, mesmo com uma por decidir
    assert mal.p == pytest.approx(0.3 * 0.5 * 0.5 * 0.6)  # a nula sai
    with abrir_sessao() as s:
        tarde = mp.carregar_resolvidas(s, path=f, agora=AGORA + timedelta(days=4))
    assert tarde[1].selecoes[-1].resultado == "SD"   # 4 dias sem estatisticas


def test_multipla_ganha_com_nulas_e_por_decidir():
    def m(*res):
        return mp.MultiplaResolvida("x", "segura", ENVIO, [
            mp.SelecaoResolvida("j", "s", "remates", "", 1, 0.7, r) for r in res])
    assert m("W", "W", "nula").resultado == "W"
    assert m("W", "pendente").resultado == "pendente"
    assert m("nula", "nula").resultado == "nula"
    assert m("W", "SD").resultado == "SD"


def test_calibracao_e_texto(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    f = tmp_path / "multiplas.csv"
    for i in range(12):   # remates do jogador: o modelo dava 80%, acertou 4+ em 12 (= 100%)
        mp.registar("segura", [_sel("Avancado", "remates", 3, 0.8, sujeito_id=1)],
                    ENVIO + timedelta(minutes=i), f)
    _jogo_na_bd()
    with abrir_sessao() as s:
        ms = mp.carregar_resolvidas(s, path=f, agora=AGORA)
    cal = mp.calibracao(ms)
    assert cal[0].mercado == "remates" and cal[0].n == 12
    assert cal[0].esperado == pytest.approx(0.8) and cal[0].real == 1.0
    txt = mp.formatar(ms)
    assert "12/12 ganhas" in txt and "🔵 remates do jogador: 80% → 100% (12/12)" in txt
    dia = mp.texto_multiplas(ms[:1], "Multiplas de 07/11")
    assert "✅ *🎯 Segura*" in dia and "Avancado (Porto–Benfica 20:00) · 3+ remates → 4" in dia


def test_sem_registo():
    assert "Ainda nenhuma" in mp.formatar([])
    assert mp.texto_multiplas([], "x") == ""


def test_selecoes_da_multipla_levam_linha_e_sujeito():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import multipla_jogadores as mj
    pr = SimpleNamespace(cantos=10.0, cantos_casa=6.0, cantos_fora=4.0, remates_casa=14.0,
                         remates_fora=10.0, sot_casa=5.0, sot_fora=3.5, cartoes=4.5)
    sel = mj.candidatas_equipas(pr, ("Porto", "Benfica"), (100, 200), "Porto–Benfica", 7)
    cantos_porto = [s for s in sel if s.mercado == "cantos_equipa" and s.jogador == "Porto"]
    assert cantos_porto and all(s.sujeito_id == 100 for s in cantos_porto)
    assert all(s.descricao.startswith(f"{s.linha}+") for s in cantos_porto)
    jogo = [s for s in sel if s.mercado == "cartoes_jogo"]
    assert jogo and all(s.sujeito_id == 7 for s in jogo)


def _resolvidas(mercado, p, resultados, p_modelo=None):
    return [mp.MultiplaResolvida(f"m{i}", "segura", ENVIO, [mp.SelecaoResolvida(
        "j", "s", mercado, "", 1, p, r, p_modelo=p_modelo)]) for i, r in enumerate(resultados)]


def test_ajuste_aprende_quando_o_modelo_e_optimista():
    # o modelo dava 80% e so 60% aconteceram em 30 selecoes
    ms = _resolvidas("remates", 0.8, ["W"] * 18 + ["L"] * 12)
    aj = mp.ajustes(ms)
    assert -1.0 < aj["remates"] < -0.5
    assert 0.6 < mp.ajustar_p(0.8, aj["remates"]) < 0.7
    assert "remates do jogador 80%→6" in mp.texto_ajustes(aj)


def test_ajuste_so_com_selecoes_suficientes_e_sem_se_desfazer():
    assert mp.ajustes(_resolvidas("cantos_equipa", 0.8, ["L"] * 14)) == {}   # 14 < 15
    # enviadas ja corrigidas (p=0.65) mas o modelo dava 0.8: aprende sobre 0.8
    ms = _resolvidas("remates", 0.65, ["W"] * 18 + ["L"] * 12, p_modelo=0.8)
    assert mp.ajustes(ms)["remates"] < -0.5
    assert mp.ajustar_p(0.8, 0.0) == 0.8
    assert 0 < mp.ajustar_p(0.8, -5) < 0.8 and mp.ajustar_p(1.0, -1) == 1.0


def test_cabecalho_antigo_e_actualizado(tmp_path):
    import csv
    f = tmp_path / "multiplas.csv"
    antigos = mp.CAMPOS[:-1]
    with f.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(antigos)
        w.writerow(["a-segura", "2026-11-07T16:00:00", "segura", 7, "A–B", "remates", 1, "X",
                    2, 0.7, "2+ remates"])
    mp.registar("maluca", [_sel("Y", "sot", 1, 0.5, sujeito_id=2)], ENVIO, f)
    linhas = mp.carregar(f)
    assert [r["multipla_id"] for r in linhas] == ["a-segura", "20261107163000-maluca"]
    assert linhas[0]["p_modelo"] == "" and linhas[1]["p_modelo"] == "0.5"
