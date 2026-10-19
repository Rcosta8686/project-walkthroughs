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
