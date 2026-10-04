"""Cliente HTTP para a API-Football (RapidAPI).

Suporta dois modos, decididos pela variável de ambiente ``MODO``:

- ``desenvolvimento``: devolve respostas sintéticas (ver `_mocks.py`).
  Zero requests reais → ideal para desenvolver e correr testes.
- ``producao``: faz requests HTTP reais à API-Football, usando a chave
  guardada em ``API_FOOTBALL_KEY``.

Exemplo::

    from apostas.ingestao.api_football import cliente

    cli = cliente()
    resp = cli.get("/leagues", params={"id": 39})
"""

from __future__ import annotations

from typing import Any

import requests

from apostas.ingestao import _mocks
from apostas.utils.config import get_env
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"


class ClienteApiFootball:
    """Wrapper mínimo em torno da API-Football."""

    def __init__(self, modo: str | None = None):
        self.modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
        if self.modo not in {"desenvolvimento", "producao"}:
            raise ValueError(
                f"MODO inválido: '{self.modo}'. Usa 'desenvolvimento' ou 'producao'."
            )

    @property
    def _headers(self) -> dict[str, str]:
        key = get_env("API_FOOTBALL_KEY", required=True)
        host = get_env("API_FOOTBALL_HOST", "api-football-v1.p.rapidapi.com") or ""
        return {
            "X-RapidAPI-Key": key or "",
            "X-RapidAPI-Host": host,
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET genérico. Em modo desenvolvimento devolve dados sintéticos."""
        params = params or {}

        if self.modo == "desenvolvimento":
            log.debug("[mock] GET %s params=%s", path, params)
            return _mocks.responder(path, params)

        url = f"{_BASE_URL}{path}"
        log.info("GET %s params=%s", url, params)
        resp = requests.get(url, headers=self._headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()


def cliente(modo: str | None = None) -> ClienteApiFootball:
    """Fábrica. Útil para testes trocarem o modo sem mexer em variáveis globais."""
    return ClienteApiFootball(modo=modo)
