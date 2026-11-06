"""Sessao do flaresolverr: uma por programa, renovada quando encrava, e
trabalhos em lote que param quando o Sofascore bloqueia."""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pytest

from apostas.ingestao import _flaresolverr as fs
from apostas.ingestao import sofascore_live as sl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def circuito_limpo(monkeypatch):
    sl._circuit_falhas_consecutivas = 0
    sl._circuit_aberto_ate = 0.0
    sl._circuit_aberturas_seguidas = 0
    renovacoes: list[str] = []
    monkeypatch.setattr(fs, "reiniciar_sessao", lambda motivo="": renovacoes.append(motivo) or True)
    yield renovacoes
    sl._circuit_falhas_consecutivas = 0
    sl._circuit_aberto_ate = 0.0
    sl._circuit_aberturas_seguidas = 0


def test_cada_programa_tem_o_seu_navegador():
    assert fs._nome_sessao("scanner_live_v2") == "ricardo_tips_sofascore"
    assert fs._nome_sessao("") == "ricardo_tips_sofascore"
    assert fs._nome_sessao("resolver_sugestoes_live") == "ricardo_tips_resolver_sugestoes_live"
    assert fs._nome_sessao("incremento_diario") == "ricardo_tips_incremento_diario"


def _abrir():
    for _ in range(sl._CIRCUIT_FAILURE_THRESHOLD):
        sl._circuit_registar(sucesso=False)


def test_circuito_aberto_renova_a_sessao(circuito_limpo):
    _abrir()
    assert not sl.sofascore_disponivel()
    assert circuito_limpo == ["circuit aberto"]


def test_bloqueio_que_persiste_aumenta_a_pausa(monkeypatch):
    import time
    pausas = []
    for _ in range(6):
        antes = time.time()
        _abrir()
        pausas.append(round(sl._circuit_aberto_ate - antes, -1))
    assert pausas == [120, 240, 480, 900, 900, 900]
    sl._circuit_registar(sucesso=True)          # voltou: tudo a zero
    antes = time.time()
    _abrir()
    assert round(sl._circuit_aberto_ate - antes, -1) == 120


def test_renovar_no_maximo_de_10_em_10_minutos(monkeypatch):
    import importlib
    real = importlib.reload(fs)  # sem o monkeypatch da fixture
    pedidos = []
    monkeypatch.setattr(real.requests, "post", lambda *a, **k: pedidos.append(k["json"]["cmd"]))
    real._ultima_renovacao = 0.0
    real._sessao_criada = True
    assert real.reiniciar_sessao("teste") is True
    assert real._sessao_criada is False and pedidos == ["sessions.destroy"]
    assert real.reiniciar_sessao("outra vez") is False
    assert pedidos == ["sessions.destroy"]


def test_incremento_para_quando_o_sofascore_bloqueia(monkeypatch):
    from apostas.modelos import incremento_diario as inc
    monkeypatch.setattr(inc, "ligas_alvo", lambda: {17: "Premier"})
    monkeypatch.setattr(sl, "sofascore_disponivel", lambda: False)
    monkeypatch.setattr(inc, "fichas_fortes", lambda s, d: (0, 0))
    chamadas = []
    monkeypatch.setattr(inc, "eventos_terminados", lambda *a: chamadas.append(a) or [])

    class _Sess:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False
    from apostas.utils import db as db_mod
    monkeypatch.setattr(db_mod, "abrir_sessao", lambda: _Sess())
    r = inc.correr(dias=3, agora=datetime(2026, 11, 7))
    assert r.interrompido and chamadas == []
    assert "parei para nao piorar" in r.texto()


def test_resolver_para_quando_o_sofascore_bloqueia(tmp_path, monkeypatch):
    import resolver_sugestoes_live as rsl
    f = tmp_path / "sug.csv"
    with f.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["alert_id", "ts_scan", "event_id", "mercado", "lado", "linha", "odd",
                    "resultado", "lucro", "resolvido_em"])
        w.writerow(["1", "2026-11-06T19:00:00", "200", "golos", "over", "2.5", "1.9", "", "", ""])
    monkeypatch.setattr(rsl, "CSV_PATH", f)
    monkeypatch.setattr(rsl, "_garantir_tabelas", lambda: None)
    monkeypatch.setattr(rsl, "_stats_seguro", lambda *a: None)
    monkeypatch.setattr(sl, "sofascore_disponivel", lambda: False)
    pedidos = []
    monkeypatch.setattr(rsl, "_puxar_resultado_final", lambda eid: pedidos.append(eid))
    rsl.resolver(desde=datetime(2026, 11, 6))
    assert pedidos == []
