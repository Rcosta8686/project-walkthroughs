"""Testes dos helpers puros do health_check."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from apostas.alertas.health_check import (
    _check_bd, _check_csv_vivo, correr_health_check, formatar_telegram,
)


def test_csv_inexistente_devolve_erro(tmp_path):
    ok, erro, _ = _check_csv_vivo(tmp_path / "nao_existe.csv")
    assert ok is False
    assert "nao existe" in erro.lower()


def test_csv_recente_e_ok(tmp_path):
    p = tmp_path / "sug.csv"
    p.write_text("ts_scan\n2026-10-19T20:00:00\n")
    agora = datetime.utcnow()
    ok, _, det = _check_csv_vivo(p, agora=agora, minutos_limite=60)
    assert ok is True
    assert "csv_mtime" in det


def test_csv_antigo_detectado(tmp_path):
    p = tmp_path / "sug.csv"
    p.write_text("ts_scan\n")
    # Simula CSV antigo via st_mtime no passado
    import os
    agora = datetime.utcnow()
    passado = agora - timedelta(hours=2)
    os.utime(p, (passado.timestamp(), passado.timestamp()))
    ok, erro, det = _check_csv_vivo(p, agora=agora, minutos_limite=30)
    assert ok is False
    assert "sem update" in erro
    assert det["minutos_desde_update"] > 30


def test_bd_inexistente_devolve_erro(tmp_path):
    ok, erro = _check_bd(tmp_path / "nao_existe.db")
    assert ok is False
    assert "nao existe" in erro.lower()


def test_bd_valida_passa(tmp_path):
    import sqlite3
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.close()
    ok, _ = _check_bd(db)
    assert ok is True


def test_formatar_telegram_vazio_quando_ok():
    from apostas.alertas.health_check import ResultadoHealthCheck
    r = ResultadoHealthCheck(tudo_ok=True)
    assert "OK" in formatar_telegram(r)


def test_formatar_telegram_mostra_problemas():
    from apostas.alertas.health_check import ResultadoHealthCheck
    r = ResultadoHealthCheck(
        tudo_ok=False,
        problemas=["❌ Flaresolverr: timeout"],
        detalhes={"minutos_desde_update": 42},
    )
    t = formatar_telegram(r)
    assert "problemas" in t.lower()
    assert "Flaresolverr" in t
    assert "42" in t


def test_correr_health_check_devolve_resultado(tmp_path, monkeypatch):
    """Smoke test — nao mockamos tudo, mas confirmamos que corre."""
    from apostas.utils import config as config_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    # Flaresolverr e BD vao falhar — queremos que o helper nao crashe
    r = correr_health_check()
    assert isinstance(r.tudo_ok, bool)
    assert isinstance(r.problemas, list)


def test_fichas_check_vazio_detectado(tmp_path, monkeypatch):
    """BD sem jogos de equipa → alerta 'fichas vazias'."""
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    from apostas.alertas.health_check import _check_fichas_desactualizadas
    ok, erro, det = _check_fichas_desactualizadas()
    assert ok is False
    assert "vazias" in erro
    db_mod._engine = None
    db_mod._SessionLocal = None


def test_fichas_check_pct_fracas_detectado(tmp_path, monkeypatch):
    """Mais de 50% das equipas com <5 jogos → alerta."""
    from datetime import datetime
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    from apostas.utils.schema import EstatisticaEquipaJogo
    from apostas.utils.db import abrir_sessao
    with abrir_sessao() as sess:
        # 2 equipas com so 2 jogos cada, 1 equipa com 6 jogos
        for eid in (1, 2):
            for i in range(2):
                sess.add(EstatisticaEquipaJogo(
                    sofascore_event_id=100 + eid*10 + i,
                    sofascore_team_id=eid,
                    nome_equipa=f"E{eid}", era_casa=True,
                    data_jogo=datetime(2026, 1, 1),
                    resultado="W",
                ))
        for i in range(6):
            sess.add(EstatisticaEquipaJogo(
                sofascore_event_id=200 + i,
                sofascore_team_id=99,
                nome_equipa="E99", era_casa=True,
                data_jogo=datetime(2026, 1, 1),
                resultado="W",
            ))
    from apostas.alertas.health_check import _check_fichas_desactualizadas
    ok, erro, det = _check_fichas_desactualizadas()
    assert ok is False
    assert "desactualizadas" in erro
    assert det["fichas_fracas"] == 2
    assert det["fichas_total_equipas"] == 3
    db_mod._engine = None
    db_mod._SessionLocal = None


def test_fichas_check_ok_quando_maioria_tem_dados(tmp_path, monkeypatch):
    from datetime import datetime
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    from apostas.utils.schema import EstatisticaEquipaJogo
    from apostas.utils.db import abrir_sessao
    with abrir_sessao() as sess:
        # 3 equipas com 10 jogos cada
        for eid in (1, 2, 3):
            for i in range(10):
                sess.add(EstatisticaEquipaJogo(
                    sofascore_event_id=eid*100 + i,
                    sofascore_team_id=eid,
                    nome_equipa=f"E{eid}", era_casa=True,
                    data_jogo=datetime(2026, 1, 1),
                    resultado="W",
                ))
    from apostas.alertas.health_check import _check_fichas_desactualizadas
    ok, erro, det = _check_fichas_desactualizadas()
    assert ok is True
    assert erro == ""
    db_mod._engine = None
    db_mod._SessionLocal = None
