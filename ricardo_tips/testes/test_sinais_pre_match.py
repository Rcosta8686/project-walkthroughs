"""Testes do script sinais_pre_match."""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sinais_pre_match as spm  # noqa: E402


def _ss(jogo_id=1, mercado="golos", lado="over", linha=2.5, casa_soft="betano",
        odd_soft=2.10, ev=0.08, prob_fair=0.55):
    """Factory para SugestaoSoft dummy."""
    sugestao = SimpleNamespace(
        mercado=mercado, lado=lado, linha=linha, ev=ev,
        odd_referencia=odd_soft, prob_modelo=prob_fair,
    )
    jogo = SimpleNamespace(id=jogo_id, data_utc=datetime(2026, 11, 1, 20, 0))
    liga = SimpleNamespace(nome="Liga Portugal")
    casa = SimpleNamespace(nome="Benfica")
    fora = SimpleNamespace(nome="Porto")
    return SimpleNamespace(
        sugestao=sugestao, jogo=jogo, liga=liga, casa=casa, fora=fora,
        casa_soft=casa_soft, prob_fair=prob_fair,
        odd_pinnacle=round(1 / prob_fair, 2), odd_soft=odd_soft,
    )


# ─── _chave_sugestao ─────────────────────────────────────────────────────

def test_chave_determinista():
    a = spm._chave_sugestao(_ss())
    b = spm._chave_sugestao(_ss())
    assert a == b
    assert len(a) == 16


def test_chave_difere_por_mercado():
    a = spm._chave_sugestao(_ss(mercado="golos"))
    b = spm._chave_sugestao(_ss(mercado="1x2"))
    assert a != b


def test_chave_difere_por_lado():
    a = spm._chave_sugestao(_ss(lado="over"))
    b = spm._chave_sugestao(_ss(lado="under"))
    assert a != b


def test_chave_difere_por_casa_soft():
    a = spm._chave_sugestao(_ss(casa_soft="betano"))
    b = spm._chave_sugestao(_ss(casa_soft="bet365"))
    assert a != b


def test_chave_difere_por_jogo():
    a = spm._chave_sugestao(_ss(jogo_id=1))
    b = spm._chave_sugestao(_ss(jogo_id=2))
    assert a != b


# ─── _carregar/_gravar_enviado ───────────────────────────────────────────

def test_carregar_sem_ficheiro_devolve_set_vazio(tmp_path):
    assert spm._carregar_enviados(tmp_path / "nope.csv") == set()


def test_gravar_e_carregar_roundtrip(tmp_path):
    path = tmp_path / "tracking.csv"
    s1 = _ss(jogo_id=10)
    s2 = _ss(jogo_id=20, mercado="1x2", lado="casa")

    spm._gravar_enviado(path, "chave_a", s1)
    spm._gravar_enviado(path, "chave_b", s2)

    enviados = spm._carregar_enviados(path)
    assert enviados == {"chave_a", "chave_b"}


def test_gravar_cria_pasta(tmp_path):
    path = tmp_path / "nova" / "pasta" / "x.csv"
    spm._gravar_enviado(path, "k1", _ss())
    assert path.exists()


def test_gravar_mesma_chave_duas_vezes_nao_duplica_leitura(tmp_path):
    """Mesmo que gravemos a mesma chave 2x, carregar devolve set unico."""
    path = tmp_path / "x.csv"
    spm._gravar_enviado(path, "mesma_chave", _ss())
    spm._gravar_enviado(path, "mesma_chave", _ss())
    assert spm._carregar_enviados(path) == {"mesma_chave"}


def test_gravar_grava_campos_essenciais(tmp_path):
    path = tmp_path / "x.csv"
    s = _ss(jogo_id=42, mercado="golos", lado="under", linha=3.5,
            casa_soft="bet365", odd_soft=1.95, ev=0.12)
    spm._gravar_enviado(path, "chave_x", s)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    r = rows[0]
    assert r["chave"] == "chave_x"
    assert r["jogo_id"] == "42"
    assert r["mercado"] == "golos"
    assert r["lado"] == "under"
    assert r["linha"] == "3.5"
    assert r["casa_soft"] == "bet365"
    assert r["odd_soft"] == "1.95"
    assert r["ev"] == "0.12"


# ─── _formatar_telegram ──────────────────────────────────────────────────

def test_formatar_golos():
    msg = spm._formatar_telegram(_ss(mercado="golos", lado="over", linha=2.5))
    assert "OVER 2.5" in msg
    assert "Benfica vs Porto" in msg
    assert "Liga Portugal" in msg
    assert "PRE-MATCH" in msg
    assert "EV *+" in msg


def test_formatar_1x2_casa():
    msg = spm._formatar_telegram(_ss(mercado="1x2", lado="casa"))
    assert "1 (casa)" in msg


def test_formatar_1x2_empate():
    msg = spm._formatar_telegram(_ss(mercado="1x2", lado="empate"))
    assert "X (empate)" in msg


def test_formatar_1x2_fora():
    msg = spm._formatar_telegram(_ss(mercado="1x2", lado="fora"))
    assert "2 (fora)" in msg


def test_formatar_btts():
    msg = spm._formatar_telegram(_ss(mercado="btts", lado="sim"))
    assert "BTTS SIM" in msg


def test_formatar_mostra_casa_soft():
    msg = spm._formatar_telegram(_ss(casa_soft="unibet"))
    assert "unibet" in msg


def test_formatar_estrelas_por_ev():
    msg_baixo = spm._formatar_telegram(_ss(ev=0.03))  # 3% → 1 estrela
    msg_alto = spm._formatar_telegram(_ss(ev=0.18))   # 18% → 5 estrelas
    assert msg_baixo.count("⭐") == 1
    assert msg_alto.count("⭐") == 5


def test_formatar_marcado_pre_match_nao_live():
    msg = spm._formatar_telegram(_ss())
    assert "PRE-MATCH" in msg
    # confirma que nao se parece com um alerta live (sem minuto)
    assert "'" not in msg or "min" not in msg.lower()


# ─── _CSV_ENVIADOS constante ─────────────────────────────────────────────

def test_csv_envios_e_pre_match():
    assert "pre_match" in spm._CSV_ENVIADOS
