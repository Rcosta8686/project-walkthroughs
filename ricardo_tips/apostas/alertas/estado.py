"""Estado persistente simples do bot (pausado/retomado, última análise).

Guardado num JSON em ``dados/estado_bot.json`` — pequeno, legível à mão
se precisares de inspecionar.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from apostas.utils import config as _config
from apostas.utils.logger import get_logger

log = get_logger(__name__)

_DEFAULT: dict[str, Any] = {
    "pausado": False,
    "ultima_analise_utc": None,  # ISO string
}


def _caminho() -> Path:
    return _config.project_root() / "dados" / "estado_bot.json"


def ler() -> dict[str, Any]:
    p = _caminho()
    if not p.exists():
        return dict(_DEFAULT)
    try:
        return {**_DEFAULT, **json.loads(p.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Ficheiro estado inválido (%s). A reiniciar.", exc)
        return dict(_DEFAULT)


def gravar(estado: dict[str, Any]) -> None:
    p = _caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")


def pausar() -> None:
    e = ler()
    e["pausado"] = True
    gravar(e)
    log.info("Alertas pausados.")


def retomar() -> None:
    e = ler()
    e["pausado"] = False
    gravar(e)
    log.info("Alertas retomados.")


def esta_pausado() -> bool:
    return bool(ler().get("pausado", False))


def marcar_analise_feita(ref: datetime | None = None) -> None:
    e = ler()
    e["ultima_analise_utc"] = (ref or datetime.utcnow()).isoformat(timespec="seconds")
    gravar(e)
