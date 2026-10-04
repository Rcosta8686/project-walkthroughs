"""Leitor da configuração central (`config.yaml` + `.env`).

Toda a aplicação pede a sua configuração a este módulo. Nada deve ler
`config.yaml` ou `os.environ` diretamente — assim há um só sítio onde
mudar como as coisas são carregadas.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def project_root() -> Path:
    """Pasta raiz do projeto `ricardo_tips/` (onde vive o `config.yaml`)."""
    # Este ficheiro está em ricardo_tips/apostas/utils/config.py
    return Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Carrega `config.yaml` e devolve o dicionário. Chamadas repetidas são gratuitas."""
    config_path = project_root() / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml não encontrado em {config_path}. "
            "Confirma que estás a correr a partir da pasta ricardo_tips/."
        )
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def _load_env_once() -> None:
    env_path = project_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_env(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Lê uma variável do `.env` (carregando-o na primeira chamada).

    Se ``required`` for True e a variável não existir, levanta erro claro.
    """
    _load_env_once()
    value = os.environ.get(key, default)
    if required and not value:
        raise RuntimeError(
            f"Variável de ambiente obrigatória '{key}' não definida. "
            "Verifica o teu ficheiro .env (ver .env.example)."
        )
    return value


def db_path() -> Path:
    """Caminho absoluto para o ficheiro SQLite definido no `config.yaml`."""
    cfg = load_config()
    rel = cfg["dados"]["caminho_bd"]
    return project_root() / rel
