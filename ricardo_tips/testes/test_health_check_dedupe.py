"""Testes do dedupe do health_check script."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from health_check import _deve_enviar, _hash_problemas  # noqa: E402
from apostas.utils.tempo import agora_utc


class _Res:
    def __init__(self, tudo_ok=True, problemas=None):
        self.tudo_ok = tudo_ok
        self.problemas = problemas or []


# ─── hash ──────────────────────────────────────────────────────────

def test_hash_estavel_ignora_numeros():
    """CSV sem update ha 30 min vs 45 min → mesmo hash."""
    h1 = _hash_problemas(["⚠ CSV sem update ha 30 min"])
    h2 = _hash_problemas(["⚠ CSV sem update ha 45 min"])
    assert h1 == h2


def test_hash_diferente_problemas_diferentes():
    h1 = _hash_problemas(["⚠ CSV sem update ha 30 min"])
    h2 = _hash_problemas(["❌ Flaresolverr: timeout"])
    assert h1 != h2


def test_hash_estavel_mesmo_que_ordem_mude():
    h1 = _hash_problemas(["A", "B"])
    h2 = _hash_problemas(["B", "A"])
    assert h1 == h2


# ─── deve_enviar ──────────────────────────────────────────────────

def test_envia_quando_novo_problema():
    r = _Res(False, ["X"])
    estado = {}
    envia, _ = _deve_enviar(r, estado, forcar=False)
    assert envia is True


def test_envia_quando_tudo_ok_mas_estado_anterior_com_problemas():
    r = _Res(True, [])
    estado = {"tudo_ok": False, "hash": "abc"}
    envia, motivo = _deve_enviar(r, estado, forcar=False)
    assert envia is True
    assert "resolvido" in motivo


def test_silencio_quando_tudo_ok_sempre():
    r = _Res(True, [])
    estado = {"tudo_ok": True}
    envia, _ = _deve_enviar(r, estado, forcar=False)
    assert envia is False


def test_silencio_quando_mesmo_problema_recente():
    r = _Res(False, ["X"])
    estado = {
        "tudo_ok": False,
        "hash": _hash_problemas(r.problemas),
        "ultimo_envio": agora_utc().isoformat(),
    }
    envia, motivo = _deve_enviar(r, estado, forcar=False)
    assert envia is False
    assert "mesmo problema" in motivo


def test_envia_quando_mesmo_problema_mas_6h_passaram():
    r = _Res(False, ["X"])
    passado = agora_utc() - timedelta(hours=7)
    estado = {
        "tudo_ok": False,
        "hash": _hash_problemas(r.problemas),
        "ultimo_envio": passado.isoformat(),
    }
    envia, motivo = _deve_enviar(r, estado, forcar=False)
    assert envia is True
    assert "relembra" in motivo


def test_envia_quando_hash_mudou():
    r = _Res(False, ["novo problema"])
    estado = {
        "tudo_ok": False,
        "hash": "velho_hash",
        "ultimo_envio": agora_utc().isoformat(),
    }
    envia, motivo = _deve_enviar(r, estado, forcar=False)
    assert envia is True


def test_forcar_envia_sempre():
    r = _Res(False, ["X"])
    estado = {
        "tudo_ok": False,
        "hash": _hash_problemas(r.problemas),
        "ultimo_envio": agora_utc().isoformat(),
    }
    envia, motivo = _deve_enviar(r, estado, forcar=True)
    assert envia is True
    assert "forcar" in motivo
