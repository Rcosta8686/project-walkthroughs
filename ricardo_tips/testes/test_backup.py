"""Copia de seguranca diaria: BD consistente + tracking, so os ultimos dias."""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime, timedelta

from apostas.utils import backup


def test_copia_bd_e_tracking_e_apaga_as_antigas(tmp_path):
    raiz = tmp_path
    bd = raiz / "dados" / "ricardo_tips.db"
    (raiz / "dados" / "tracking").mkdir(parents=True)
    con = sqlite3.connect(bd)
    con.execute("create table t (x int)")
    con.executemany("insert into t values (?)", [(i,) for i in range(1000)])
    con.commit()
    con.close()
    (raiz / "dados" / "tracking" / "sugestoes_live_v2.csv").write_text("a,b\n1,2\n")
    (raiz / "dados" / "tracking" / "correcao_ritmo.json").write_text("{}")
    (raiz / "dados" / "tracking" / "sugestoes_live_v2.backup-20261105.csv").write_text("x")
    agora = datetime(2026, 11, 8, 0, 15)
    velha = raiz / "dados" / "backups" / (agora - timedelta(days=9)).strftime("%Y-%m-%d")
    velha.mkdir(parents=True)
    recente = raiz / "dados" / "backups" / (agora - timedelta(days=2)).strftime("%Y-%m-%d")
    recente.mkdir(parents=True)

    r = backup.fazer_backup(agora=agora, raiz=raiz, bd=bd)
    assert r.pasta.name == "2026-11-08" and r.ficheiros == 3 and r.apagados == 1
    assert not velha.exists() and recente.exists()
    nomes = sorted(f.name for f in r.pasta.iterdir())
    assert nomes == ["correcao_ritmo.json", "ricardo_tips.db.zip", "sugestoes_live_v2.csv"]
    with zipfile.ZipFile(r.pasta / "ricardo_tips.db.zip") as z:
        z.extract("ricardo_tips.db", tmp_path / "rest")
    con = sqlite3.connect(tmp_path / "rest" / "ricardo_tips.db")
    assert con.execute("select count(*) from t").fetchone()[0] == 1000
    con.close()
    assert "Copia de seguranca: 3 ficheiros" in r.texto()


def test_sem_bd_nao_rebenta(tmp_path):
    r = backup.fazer_backup(agora=datetime(2026, 11, 8), raiz=tmp_path,
                            bd=tmp_path / "nao_existe.db")
    assert r.ficheiros == 0


def test_aviso_quando_o_incremento_nao_corre(tmp_path):
    from datetime import datetime

    from apostas.utils.backup import aviso_sem_copias
    agora = datetime(2026, 11, 7, 20)
    assert "nunca foi feita nenhuma" in aviso_sem_copias(agora, raiz=tmp_path)
    (tmp_path / "dados" / "backups" / "2026-11-01").mkdir(parents=True)
    assert "a ultima e de 01/11" in aviso_sem_copias(agora, raiz=tmp_path)
    (tmp_path / "dados" / "backups" / "2026-11-07").mkdir()
    assert aviso_sem_copias(agora, raiz=tmp_path) == ""
