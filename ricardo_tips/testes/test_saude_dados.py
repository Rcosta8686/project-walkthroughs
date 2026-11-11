"""Saude dos dados: o historico esta a crescer? E a noite esta a correr?"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apostas.relatorios import saude_dados as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

AGORA = datetime(2026, 11, 11, 0, 40)


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


def _encher(dias_com_dados):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstadoLiveSnapshot, EstatisticaEquipaJogo
    from apostas.utils.schema import EstatisticaJogadorJogo
    with abrir_sessao() as s:
        for d in dias_com_dados:
            quando = datetime(2026, 11, 11, 20) - timedelta(days=d)     # d dias antes, 20h
            for ev in (100 * d + 1, 100 * d + 2):
                for team in (1, 2):
                    s.add(EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                                nome_equipa="X", era_casa=team == 1,
                                                data_jogo=quando, resultado="D",
                                                data_ingestao=quando))
                for pid in range(3):
                    s.add(EstatisticaJogadorJogo(sofascore_player_id=pid, sofascore_event_id=ev,
                                                 sofascore_team_id=1, nome_jogador="J",
                                                 data_jogo=quando, era_casa=1,
                                                 data_ingestao=quando))
            for minuto in (20, 40, 60, 80, 90):
                s.add(EstadoLiveSnapshot(event_id=100 * d + 1, ts=quando, home="A", away="B",
                                         minuto=minuto))


def test_conta_o_que_entrou_por_dia_e_o_total(bd, tmp_path):
    from apostas.relatorios import alertas_enviados as ae
    from apostas.utils.db import abrir_sessao
    _encher([1, 2, 4])                       # 3 e hoje sem nada
    env = tmp_path / "env.csv"
    ae.registar(5, datetime(2026, 11, 10, 21), env)
    (tmp_path / "dados" / "backups" / "2026-11-11").mkdir(parents=True)
    sd.gravar_noite(AGORA - timedelta(minutes=25), AGORA, 4, "abc", raiz=tmp_path)
    with abrir_sessao() as s:
        r = sd.calcular(s, AGORA, enviados=env, raiz=tmp_path)
    assert [d.data.day for d in r.dias] == [5, 6, 7, 8, 9, 10, 11]
    ontem = r.dias[-2]
    assert (ontem.jogos, ontem.jogadores, ontem.momentos, ontem.dicas) == (2, 6, 5, 1)
    assert r.dias[-4].jogos == 0                                   # 08/11 vazio
    assert (r.total_jogos, r.total_jogadores, r.total_momentos) == (6, 18, 15)
    assert r.problemas() == []
    txt = sd.formatar(r)
    assert "✅ Noite: correu 11/11 as 00:40 UTC (25 min, +4 jogos novos)" in txt
    assert "✅ Copia de seguranca: 11/11/2026" in txt
    assert "10/11     2 ·      6 ·      5 ·   1" in txt
    assert "Total guardado: 6 jogos · 18 fichas de jogadores · 15 momentos ao vivo" in txt
    assert "✅ O historico esta a crescer." in txt
    assert "📡 Ultimo momento ao vivo guardado: 10/11 20:00 UTC" in txt
    assert "Scanner: ultimo ciclo" not in txt                     # ainda sem marca
    assert ontem.jogados == 2


def test_problemas_quando_nada_entra_e_a_noite_falha(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    _encher([3])
    sd.gravar_noite(AGORA - timedelta(days=3), AGORA - timedelta(days=3) + timedelta(minutes=20),
                    0, raiz=tmp_path)
    with abrir_sessao() as s:
        r = sd.calcular(s, AGORA, enviados=tmp_path / "nada.csv", raiz=tmp_path)
    probs = r.problemas()
    assert any("nao corre desde 08/11" in p for p in probs)
    assert any("copia de seguranca: a ultima e de nunca" in p for p in probs)
    assert "⚠ Noite: correu 08/11" in sd.formatar(r)
    # ontem sem jogos e sem momentos: pausa (nao e problema do scanner)
    assert r.sem_jogos_ontem() and not any("scanner" in p for p in probs)
    assert "ℹ Ontem nao houve jogos das nossas ligas" in sd.formatar(r)


def test_scanner_parado_com_jogos_ontem(bd, tmp_path):
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo
    with abrir_sessao() as s:     # 4 jogos de ontem guardados, nenhum momento ao vivo
        for ev in range(4):
            for team in (1, 2):
                s.add(EstatisticaEquipaJogo(sofascore_event_id=500 + ev, sofascore_team_id=team,
                                            nome_equipa="X", era_casa=team == 1,
                                            data_jogo=datetime(2026, 11, 10, 20), resultado="D",
                                            data_ingestao=AGORA))
    sd.gravar_noite(AGORA - timedelta(minutes=20), AGORA, 4, raiz=tmp_path)
    with abrir_sessao() as s:
        r = sd.calcular(s, AGORA, enviados=tmp_path / "nada.csv", raiz=tmp_path)
    assert any("ontem houve 4 jogos das nossas ligas e o scanner nao guardou" in p
               for p in r.problemas())
    assert not r.sem_jogos_ontem()


def test_aviso_do_scanner_so_quando_a_noite_falha(tmp_path):
    assert sd.aviso_noite(AGORA, tmp_path) == ""                    # sem registo: espera
    sd.gravar_noite(AGORA - timedelta(hours=25), AGORA - timedelta(hours=24), 3, raiz=tmp_path)
    assert sd.aviso_noite(AGORA, tmp_path) == ""                    # ontem: tudo bem
    sd.gravar_noite(AGORA - timedelta(hours=49), AGORA - timedelta(hours=48), 3, raiz=tmp_path)
    aviso = sd.aviso_noite(AGORA, tmp_path)
    assert "nao corre desde 09/11 00:40 UTC" in aviso and "RicardoTips_IncrementoDiario" in aviso


def test_scanner_avisa_no_maximo_de_12_em_12_horas(monkeypatch):
    import scanner_live_v2 as sc
    enviadas = []
    monkeypatch.setattr(sc, "telegram_cliente", lambda: SimpleNamespace(enviar=enviadas.append))
    monkeypatch.setattr(sd, "aviso_noite", lambda: "⚠ noite parada")
    relogio = [1_000_000.0]
    monkeypatch.setattr(sc.time, "time", lambda: relogio[0])
    sc._vigia_noite.update(visto=0.0, avisado=0.0)
    assert sc._vigiar_noite() is True and enviadas == ["⚠ noite parada"]
    relogio[0] += 600
    assert sc._vigiar_noite() is False                              # so de hora a hora
    relogio[0] += 3600 * 5
    assert sc._vigiar_noite() is False and len(enviadas) == 1       # nao repete antes de 12h
    relogio[0] += 3600 * 8
    assert sc._vigiar_noite() is True and len(enviadas) == 2
    sc._vigia_noite.update(visto=0.0, avisado=0.0)


def test_noite_manda_a_saude_uma_vez(bd, tmp_path, monkeypatch):
    import incremento_diario as script
    from apostas.alertas import telegram_cliente as tc
    from apostas.relatorios import grafico_acertos as ga
    monkeypatch.setattr(ga, "project_root", lambda: tmp_path)
    enviadas = []
    monkeypatch.setattr(tc, "cliente", lambda modo=None: SimpleNamespace(enviar=enviadas.append))
    _encher([1])
    script._saude(AGORA)
    script._saude(AGORA + timedelta(minutes=10))
    assert len(enviadas) == 1 and enviadas[0].startswith("*🩺 Saude dos dados* (11/11/2026)")


def test_marca_do_scanner(tmp_path, bd):
    from apostas.utils.db import abrir_sessao
    assert sd.ler_scanner_vivo(tmp_path) is None
    sd.marcar_scanner_vivo(3, AGORA - timedelta(minutes=3), raiz=tmp_path)
    assert sd.ler_scanner_vivo(tmp_path) == AGORA - timedelta(minutes=3)
    with abrir_sessao() as s:
        r = sd.calcular(s, AGORA, enviados=tmp_path / "nada.csv", raiz=tmp_path)
    assert "✅ Scanner: ultimo ciclo 11/11 00:37 UTC" in sd.formatar(r)
    sd.marcar_scanner_vivo(0, AGORA - timedelta(hours=2), raiz=tmp_path)
    with abrir_sessao() as s:
        r = sd.calcular(s, AGORA, enviados=tmp_path / "nada.csv", raiz=tmp_path)
    assert "⚠ Scanner: ultimo ciclo 10/11 22:40 UTC" in sd.formatar(r)
