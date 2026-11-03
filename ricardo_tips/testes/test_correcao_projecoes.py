"""Correccao das projeccoes 40'/55'/70' com o total final real."""
from __future__ import annotations

from datetime import datetime, timedelta

from apostas.modelos.correcao_projecoes import (
    MIN_PARES, formatar_resumo, multiplicador, pares_projecao_real, treinar_correcoes,
)

AGORA = datetime(2026, 11, 3, 20, 0)


def _pares(mercado, janela, actual, proj, real, n, dias_atras=0):
    return [{"mercado": mercado, "janela": janela, "actual": actual, "proj": proj,
             "real": real, "data": AGORA - timedelta(days=dias_atras)}] * n


def test_resto_subestimado_da_multiplicador_acima_de_1():
    """Cantos ao 55': modelo dizia +3 ate ao fim, sairam +4.5."""
    c = treinar_correcoes(_pares("cantos", 55, 5, 8, 9.5, 100))
    m = multiplicador(c, "cantos", 55)
    w = 100 / 130
    assert abs(m - (w * 1.5 + (1 - w))) < 0.01


def test_poucos_pares_nao_corrige():
    c = treinar_correcoes(_pares("sot", 40, 3, 7, 10, MIN_PARES - 1))
    assert multiplicador(c, "sot", 40) == 1.0


def test_multiplicador_limitado():
    c = treinar_correcoes(_pares("sot", 70, 4, 5, 15, 500))
    assert multiplicador(c, "sot", 70) == 1.8


def test_janela_sem_dados_usa_o_mercado_todo():
    c = treinar_correcoes(_pares("cantos", 40, 3, 8, 10, 15) + _pares("cantos", 55, 5, 8, 10, 15))
    # nenhuma janela tem 20, mas o mercado junto tem 30
    assert multiplicador(c, "cantos", 70) > 1.0


def test_pares_recentes_pesam_mais():
    antigos = _pares("cantos", 55, 5, 8, 8, 200, dias_atras=60)     # modelo acertava
    recentes = _pares("cantos", 55, 5, 8, 9.5, 100, dias_atras=0)   # agora falta +50%
    m = multiplicador(treinar_correcoes(antigos + recentes), "cantos", 55)
    assert m > 1.3  # sem pesos daria ~1.12


def test_pares_projecao_real_le_da_bd(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod, db as db_mod
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo, ProjecaoLive
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()

    def proj(ev, actual, total):
        return ProjecaoLive(sofascore_event_id=ev, minuto=55, score_casa=0, score_fora=0,
                            nome_casa="A", nome_fora="B", mercado="cantos",
                            valor_atual_total=actual, projecao_final_total=total)

    def stat(ev, team, cantos):
        return EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                     nome_equipa="X", era_casa=team == 1,
                                     data_jogo=AGORA, resultado="W", cantos=cantos)
    with abrir_sessao() as s:
        s.add_all([proj(1, 5, 8), stat(1, 1, 6), stat(1, 2, 4),   # ok: real 10
                   proj(2, 5, 8), stat(2, 1, 6),                  # so 1 equipa
                   proj(3, 9, 11), stat(3, 1, 3), stat(3, 2, 2)])  # real 5 < actual 9
    with abrir_sessao() as s:
        pares = pares_projecao_real(s)
    db_mod._engine = None
    db_mod._SessionLocal = None
    assert len(pares) == 1
    assert pares[0]["real"] == 10.0 and pares[0]["janela"] == 55


def test_resumo_sem_dados():
    assert "Ainda nao ha" in formatar_resumo({})


def test_resumo_com_dados():
    txt = formatar_resumo(treinar_correcoes(_pares("cantos", 55, 5, 8, 9.5, 100)))
    assert "🚩 cantos 55'" in txt and "real +4.5" in txt


def test_scanner_grava_bruta_e_usa_corrigida(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import scanner_live_v2 as sc

    gravadas, usadas = [], []
    monkeypatch.setattr(sc, "_ambas_fichas_fortes", lambda *a: True)
    monkeypatch.setattr(sc, "_mult_projecao", lambda m, j: 1.5)
    monkeypatch.setattr(sc, "_gravar_projecao_db",
                        lambda ev, j, est, proj: gravadas.append(proj.projecao_final_total))
    orig = sc.projecao_live.tem_valor_actionavel
    monkeypatch.setattr(sc.projecao_live, "tem_valor_actionavel",
                        lambda p: usadas.append(p.projecao_final_total) or orig(p) or True)
    sc._projecoes_enviadas.clear()
    sc._primeiro_minuto_visto.clear()
    estado = SimpleNamespace(event_id=99, minuto=56, liga="Serie A", home="Lecce", away="Genoa",
                             home_id=1, away_id=2, marcador_casa=0, marcador_fora=0,
                             shots_on_target_casa=3, shots_on_target_fora=2,
                             corners_casa=4, corners_fora=3)
    prev = SimpleNamespace(projeccao_sot_casa=5.0, projeccao_sot_fora=4.0,
                           projeccao_cantos_casa=7.0, projeccao_cantos_fora=5.0)
    sc._enviar_projecoes_jogo(estado, prev, enviar_telegram=False)
    # sot: actual 5, resto bruto 4 -> 9; corrigido resto 6 -> 11
    assert abs(gravadas[0] - 9.0) < 0.01
    assert abs(usadas[0] - 11.0) < 0.01
