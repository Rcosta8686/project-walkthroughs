"""Versao do codigo no arranque do scanner."""
from __future__ import annotations

import subprocess

from apostas.utils import versao


def test_versao_e_o_commit_actual():
    commit = subprocess.run(["git", "log", "-1", "--format=%h"], capture_output=True,
                            text=True, cwd=versao.project_root()).stdout.strip()
    assert versao.versao_codigo().startswith(commit)


def test_sem_git_nao_rebenta(monkeypatch):
    def falha(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(versao.subprocess, "run", falha)
    assert versao.versao_codigo() == "desconhecida"
