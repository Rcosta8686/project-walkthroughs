"""Fase 10 — Lesoes e suspensos.

O Sofascore expoe `/api/v1/event/{id}/pregame-form` e um endpoint
`/event/{id}/lineups` ja usado para onzes. Para ausencias usamos:

  /api/v1/team/{team_id}/pregame-form?eventId=...  (dados pre-match)
  /api/v1/event/{id}/managers                      (treinador)

Nos payloads retornados aparece sempre uma lista `missingPlayers` com
cada entrada {player, type ('missing'|'doubtful'), reason ('injury',
'suspension', 'national duty', 'other')}.

Para o live predictor usamos:
  obter_ausencias(event_id) → (missing_casa, missing_fora)
  cada entry: {"nome": str, "type": "missing"|"doubtful",
               "razao": "injury"|"suspension"|...}

Com os onzes do EstadoLive e os `missing` podemos:
  - Alertar quando onze actual tem baixas vs equipa recente.
  - Ajustar lam_golos/-5% se missing inclui remateador prolifico (fase
    posterior — por agora so marca a flag).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from apostas.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Ausencia:
    """1 jogador ausente ou duvidoso."""
    nome: str
    tipo: str      # "missing" | "doubtful"
    razao: str     # "injury" | "suspension" | "national duty" | "other"


def obter_ausencias(event_id: int) -> tuple[list[Ausencia], list[Ausencia]]:
    """Devolve (ausencias_casa, ausencias_fora) do Sofascore.

    Lazy import para evitar carregar o cliente Sofascore em contextos
    de teste onde ele nao esta disponivel. Em caso de erro devolve
    duas listas vazias e loga em debug.
    """
    try:
        from apostas.ingestao.sofascore_live import _get_json
    except Exception as exc:  # noqa: BLE001
        log.debug("sofascore_live import falhou: %s", exc)
        return [], []
    try:
        payload = _get_json(f"/event/{event_id}/pregame-form")
    except Exception as exc:  # noqa: BLE001
        log.debug("pregame-form %s falhou: %s", event_id, exc)
        return [], []

    if not payload or not isinstance(payload, dict):
        return [], []

    home = _extrair_lista(payload.get("homeTeam") or {})
    fora = _extrair_lista(payload.get("awayTeam") or {})
    return home, fora


def _extrair_lista(team_obj: dict) -> list[Ausencia]:
    """Extrai lista Ausencia de um team obj do pregame-form."""
    out: list[Ausencia] = []
    for mp in team_obj.get("missingPlayers") or []:
        nome = ((mp.get("player") or {}).get("name") or "").strip()
        if not nome:
            continue
        tipo = (mp.get("type") or "missing").lower()
        razao = (mp.get("reason") or mp.get("reasonCode") or "other").lower()
        out.append(Ausencia(nome=nome, tipo=tipo, razao=razao))
    return out


def resumo_ausencias(
    missing_casa: list[Ausencia],
    missing_fora: list[Ausencia],
) -> dict:
    """Agregados uteis para o cerebro/telegram.

    Devolve dict com contagens por tipo + razao e lista dos nomes mais
    importantes (ordenados: primeiro suspensoes, depois lesoes).
    """
    def _agg(lst):
        n = len(lst)
        susp = sum(1 for a in lst if "susp" in a.razao)
        lesao = sum(1 for a in lst if "injur" in a.razao or "lesao" in a.razao)
        nac = sum(1 for a in lst if "nation" in a.razao or "selec" in a.razao)
        duvid = sum(1 for a in lst if a.tipo == "doubtful")
        return {
            "n": n, "n_suspensoes": susp, "n_lesoes": lesao,
            "n_nacional": nac, "n_duvidosos": duvid,
        }

    return {
        "casa": _agg(missing_casa),
        "fora": _agg(missing_fora),
    }
