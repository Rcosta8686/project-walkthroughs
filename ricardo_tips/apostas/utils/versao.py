"""Versao do codigo em execucao (commit git), para saber se um processo
longo (scanner) ja corre o codigo novo depois de um git pull."""
from __future__ import annotations

import subprocess

from apostas.utils.config import project_root


def versao_codigo() -> str:
    """'5b08950 (2026-11-04 22:10)' ou 'desconhecida' se o git nao responder."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h (%cd)", "--date=format:%Y-%m-%d %H:%M"],
            cwd=project_root(), capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "desconhecida"
    return out.stdout.strip() or "desconhecida"
