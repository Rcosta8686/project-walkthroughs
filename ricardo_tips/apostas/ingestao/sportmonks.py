"""Cliente HTTP para a Sportmonks Football API v3.

Suporta dois modos, decididos pela variável de ambiente ``MODO``:

- ``desenvolvimento``: devolve respostas sintéticas (ver `_mocks_sm.py`).
  Zero requests reais → ideal para desenvolver e correr testes.
- ``producao``: faz requests HTTP reais à Sportmonks, usando o token
  guardado em ``SPORTMONKS_API_TOKEN``.

Documentação v3: https://docs.sportmonks.com/football

Exemplo::

    from apostas.ingestao.sportmonks import cliente

    cli = cliente()
    resp = cli.get("/leagues/8")      # Premier League
"""

from __future__ import annotations

from typing import Any

import requests

from apostas.ingestao import _mocks_sm
from apostas.utils.config import get_env
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api.sportmonks.com/v3/football"


class ClienteSportmonks:
    """Wrapper mínimo em torno da Sportmonks Football API v3."""

    def __init__(self, modo: str | None = None):
        self.modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
        if self.modo not in {"desenvolvimento", "producao"}:
            raise ValueError(
                f"MODO inválido: '{self.modo}'. Usa 'desenvolvimento' ou 'producao'."
            )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET genérico. Em modo desenvolvimento devolve dados sintéticos."""
        params = dict(params or {})

        if self.modo == "desenvolvimento":
            log.debug("[mock] GET %s params=%s", path, params)
            return _mocks_sm.responder(path, params)

        token = get_env("SPORTMONKS_API_TOKEN", required=True)
        params["api_token"] = token or ""
        url = f"{_BASE_URL}{path}"
        log.info("GET %s params=%s", url, {k: v for k, v in params.items() if k != "api_token"})
        resp = requests.get(url, params=params, timeout=30)
        if not resp.ok:
            # Mascara o token em qualquer mensagem de erro antes de propagar
            msg = f"HTTP {resp.status_code} em {path} (status: {resp.reason})"
            raise requests.HTTPError(msg)
        return resp.json()


def cliente(modo: str | None = None) -> ClienteSportmonks:
    """Fábrica. Útil para testes trocarem o modo sem mexer em variáveis globais."""
    return ClienteSportmonks(modo=modo)
