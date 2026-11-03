"""Instancia unica: o scanner novo mata scanners orfaos antigos.

Caso real (03/11): 4 processos python = 2 scanners em paralelo na mesma
sessao flaresolverr (HTTP 500, alertas duplicados).
"""
from __future__ import annotations

import os
import sys

import pytest

from apostas.utils.instancia_unica import (
    _listar_posix, _parse_saida_powershell, garantir_instancia_unica,
)

SCANNER = r'"C:\venv\Scripts\python.exe" "C:\ricardo_tips\scripts\scanner_live_v2.py" --telegram'


def _correr(procs, pid=10, ppid=11):
    mortos: list[int] = []
    out = garantir_instancia_unica(
        "scanner_live_v2.py", listar=lambda: procs, matar=mortos.append,
        pid=pid, ppid=ppid,
    )
    return out, mortos


def test_mata_scanners_antigos():
    """Caso real: par antigo (launcher+filho) + par novo (nos)."""
    procs = [(29212, SCANNER), (96860, SCANNER), (10, SCANNER), (11, SCANNER)]
    out, mortos = _correr(procs)
    assert sorted(mortos) == [29212, 96860]
    assert out == mortos


def test_nao_se_mata_a_si_nem_ao_pai():
    _, mortos = _correr([(10, SCANNER), (11, SCANNER)])
    assert mortos == []


def test_nao_mata_outros_scripts_python():
    procs = [(500, "python scripts\\popular_cerebros.py --n 15"),
             (501, "python -m pytest"), (10, SCANNER)]
    _, mortos = _correr(procs)
    assert mortos == []


def test_falha_a_listar_nao_crasha():
    def boom():
        raise OSError("powershell indisponivel")
    assert garantir_instancia_unica("scanner_live_v2.py", listar=boom,
                                    matar=lambda p: None) == []


def test_falha_a_matar_um_continua_os_outros():
    def matar(p):
        if p == 1:
            raise PermissionError("acesso negado")
    out = garantir_instancia_unica(
        "scanner_live_v2.py", listar=lambda: [(1, SCANNER), (2, SCANNER)],
        matar=matar, pid=10, ppid=11,
    )
    assert out == [2]


def test_parse_saida_powershell():
    texto = (f"29212\t{SCANNER}\r\n"
             "96860\t\r\n"           # sem command line (processo de outro user)
             "lixo sem tab\r\n"
             "\r\n")
    assert _parse_saida_powershell(texto) == [(29212, SCANNER), (96860, "")]


@pytest.mark.skipif(sys.platform == "win32", reason="/proc so existe em Linux")
def test_listar_posix_encontra_o_proprio_processo():
    procs = dict(_listar_posix())
    assert os.getpid() in procs
    assert "pytest" in procs[os.getpid()]
