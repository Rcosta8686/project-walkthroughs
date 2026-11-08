"""Tarefa semanal (popular_cerebros): salta o que ja esta na BD e para antes das 4h."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture
def pc(monkeypatch):
    import popular_cerebros as pc
    from apostas.modelos import historico_equipas, incremento_diario
    from apostas.utils import db as db_mod
    monkeypatch.setattr(pc, "_season_actual", lambda tid: 99)
    monkeypatch.setattr(pc, "_equipas_da_liga", lambda tid, sid: [(1, "Porto"), (2, "Benfica")])
    monkeypatch.setattr(pc, "_ultimos_eventos_equipa", lambda team, n: [10 + team, 20 + team])
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)

    @contextmanager
    def _sessao():
        yield None
    monkeypatch.setattr(db_mod, "abrir_sessao", _sessao)
    # 11 e 22 ja estao guardados com as 2 equipas
    monkeypatch.setattr(incremento_diario, "ja_guardados",
                        lambda s, ids: ({e for e in ids if e in (11, 22)}, set()))
    gravados = []
    monkeypatch.setattr(historico_equipas, "gravar_jogo_terminado",
                        lambda eid: gravados.append(eid) or 2)
    pc.gravados = gravados
    return pc


def _liga():
    from apostas.dados_referencia.ligas_prioritarias import LIGAS_PRIORITARIAS
    return LIGAS_PRIORITARIAS[0]


def test_nao_volta_a_pedir_jogos_ja_guardados(pc):
    stats = pc._popular_liga(_liga(), 15)
    assert pc.gravados == [21, 12]
    assert stats["ja_tinha"] == 2 and stats["eventos_processados"] == 2


def test_para_quando_acaba_o_tempo(pc, capsys):
    from apostas.utils import prazo
    prazo.definir(datetime(2000, 1, 1))
    try:
        stats = pc._popular_liga(_liga(), 15)
    finally:
        prazo.definir(None)
    assert pc.gravados == [] and stats["eventos_processados"] == 0
    assert "Acabou o tempo" in capsys.readouterr().out
