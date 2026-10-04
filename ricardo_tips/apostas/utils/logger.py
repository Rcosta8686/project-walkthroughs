"""Configuração central de logging.

Todos os módulos pedem o seu logger a `get_logger(__name__)` — o nível
e o ficheiro de destino vêm do `config.yaml`.
"""

from __future__ import annotations

import logging

from apostas.utils.config import load_config, project_root

_configured = False


def configure_logging() -> None:
    """Configura o logger raiz uma única vez por processo."""
    global _configured
    if _configured:
        return

    cfg = load_config()
    nivel = getattr(logging, cfg["log"]["nivel"].upper(), logging.INFO)
    log_path = project_root() / cfg["log"]["caminho"]
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(nivel)
    # Evita duplicação se este módulo for reimportado
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
