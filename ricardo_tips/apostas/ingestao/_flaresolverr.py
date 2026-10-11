"""Cliente HTTP que fala com flaresolverr para contornar Cloudflare.

Flaresolverr é um servidor em Docker que corre um Chromium headless.
Recebe pedidos HTTP via JSON na porta 8191, resolve o challenge
Cloudflare, e devolve a resposta já descoberta.

Fluxo:
  Tu (Python) → POST localhost:8191/v1 → Flaresolverr → Cloudflare → Sofascore
                                       ← Resposta JSON com HTML/JSON ←

Setup no PC do utilizador (ver automacao/FLARESOLVERR_SETUP.md):
  docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest

Env vars opcionais:
  FLARESOLVERR_URL  Default http://localhost:8191/v1
  FLARESOLVERR_TIMEOUT_MS  Default 60000 (60s por pedido)

Se flaresolverr NÃO estiver a correr, o cliente levanta
`FlaresolverrIndisponivel`. O chamador decide se faz fallback
(ex: para curl_cffi) ou se avisa o utilizador para arrancar o docker.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from apostas.utils.logger import get_logger

log = get_logger(__name__)

_DEFAULT_URL = "http://localhost:8191/v1"
_DEFAULT_TIMEOUT_MS = 60_000
_SESSION_NAME = "ricardo_tips_sofascore"

# Rate limit prudente: 1 pedido / 1.5s para nao sobrecarregar
_DELAY_ENTRE_PEDIDOS_S = 1.5
_ultimo_pedido: float = 0.0

# Sessao flaresolverr criada uma vez e reutilizada (reduz overhead
# de re-solve do challenge Cloudflare a cada pedido).
_sessao_criada: bool = False


class FlaresolverrIndisponivel(RuntimeError):
    """Flaresolverr nao esta a correr ou nao e acessivel."""


@dataclass
class RespostaFS:
    status: int
    url: str
    body_text: str
    headers: dict

    def json(self) -> Any:
        """Devolve o JSON parsed. Lida com flaresolverr embrulhar em HTML.

        Flaresolverr pode retornar o JSON cru ou dentro de <pre>...</pre>
        (depende da content-type). Esta funcao tenta ambos.
        """
        body = (self.body_text or "").strip()
        # Caso 1: flaresolverr embrulhou JSON em HTML
        match = re.search(r"<pre[^>]*>(.*?)</pre>", body, re.DOTALL)
        if match:
            body = match.group(1).strip()
        return json.loads(body)


def _url_flaresolverr() -> str:
    return os.getenv("FLARESOLVERR_URL", _DEFAULT_URL)


def _timeout_ms() -> int:
    try:
        return int(os.getenv("FLARESOLVERR_TIMEOUT_MS", str(_DEFAULT_TIMEOUT_MS)))
    except ValueError:
        return _DEFAULT_TIMEOUT_MS


def _rate_limit() -> None:
    global _ultimo_pedido
    agora = time.monotonic()
    espera = _DELAY_ENTRE_PEDIDOS_S - (agora - _ultimo_pedido)
    if espera > 0:
        time.sleep(espera)
    _ultimo_pedido = time.monotonic()


def _garantir_sessao() -> None:
    """Cria uma sessao flaresolverr (reutiliza cookies entre pedidos)."""
    global _sessao_criada
    if _sessao_criada:
        return
    try:
        r = requests.post(
            _url_flaresolverr(),
            json={"cmd": "sessions.create", "session": _SESSION_NAME},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            # Pode ja existir — e OK
            log.debug("Flaresolverr sessions.create: %s", data.get("message"))
        _sessao_criada = True
        log.info("Flaresolverr: sessao '%s' pronta.", _SESSION_NAME)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise FlaresolverrIndisponivel(
            f"Nao conseguiu ligar a flaresolverr em {_url_flaresolverr()}: {exc}. "
            "Verifica que o container docker esta a correr: "
            "`docker ps | grep flaresolverr`."
        ) from exc


def get(url: str, headers: dict | None = None) -> RespostaFS:
    """GET via flaresolverr. Resolve Cloudflare transparentemente."""
    _garantir_sessao()
    _rate_limit()
    payload = {
        "cmd": "request.get",
        "url": url,
        "session": _SESSION_NAME,
        "maxTimeout": _timeout_ms(),
    }
    if headers:
        payload["headers"] = headers
    log.debug("Flaresolverr GET %s", url)
    try:
        r = requests.post(_url_flaresolverr(), json=payload, timeout=90)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise FlaresolverrIndisponivel(
            f"Erro de comunicacao com flaresolverr: {exc}"
        ) from exc
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise requests.HTTPError(
            f"Flaresolverr status={data.get('status')} message={data.get('message')}"
        )
    sol = data.get("solution", {})
    return RespostaFS(
        status=sol.get("status", 0),
        url=sol.get("url", url),
        body_text=sol.get("response", "") or "",
        headers=sol.get("headers", {}) or {},
    )


def encerrar_sessao() -> None:
    """Destroi a sessao flaresolverr (chama no shutdown do script)."""
    global _sessao_criada
    if not _sessao_criada:
        return
    try:
        requests.post(
            _url_flaresolverr(),
            json={"cmd": "sessions.destroy", "session": _SESSION_NAME},
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass
    _sessao_criada = False
