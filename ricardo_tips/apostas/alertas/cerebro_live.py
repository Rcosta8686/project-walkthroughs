"""Cerebro live — Fase 5. Liga mini-cerebros (confronto, perfil equipa)
ao predictor live.

Para cada jogo in-play, o scanner chama `calcular_ajustes(home_id,
away_id, minuto, score_casa, score_fora)` ANTES de correr o predictor.
Resultado: multiplicadores sobre os lambdas (golos, cantos, cards, shots
por equipa) + notas textuais para injectar na mensagem Telegram.

Fontes:
  - `confronto.analisar_confronto(home_id, away_id, minuto, score)`
    → ResultadoConfronto com padroes cruzados, projecoes e alertas de
      fadiga (ja implementado na Fase 3).

Logica dos ajustes (conservadora — multiplicadores entre 0.9 e 1.2):
  - Ambas carregam 2T: lam_cards_mult = 1.15, lam_shots_mult = 1.05.
  - Discrepancia disciplinar: lam_cards_mult = 1.10.
  - Posse clara de uma equipa: lam_cantos_mult = 1.10 (mais pressao).
  - A perder + perfil "a perder dispara": lam_shots da equipa a perder
    sobe +8% (chasing ja captura 20%, isto e refinamento).
  - Fadiga alta (>=2 jogos 7d): lam_shots da equipa fadigada -5%.

Cache: por (home_id, away_id) com TTL 10 min. O confronto depende de
historico persistente (muda poucas vezes num jogo); nao vale a pena
re-computar a cada ciclo de 3 min.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from apostas.utils.logger import get_logger
from apostas.utils.tempo import agora_utc

log = get_logger(__name__)


# TTL do cache de confronto. Confronto depende de historico persistente
# — nao muda rapido. 10 min e seguro.
CACHE_TTL = timedelta(minutes=10)


@dataclass
class AjustesCerebro:
    """Multiplicadores a aplicar aos lambdas do predictor + notas."""
    lam_golos_mult: float = 1.0
    lam_cantos_mult: float = 1.0
    lam_cards_mult: float = 1.0
    lam_shots_casa_mult: float = 1.0
    lam_shots_fora_mult: float = 1.0
    notas: list[str] = field(default_factory=list)
    # Para debug no terminal/telegram
    razoes: list[str] = field(default_factory=list)


# Cache em memoria: {(home_id, away_id): (timestamp, AjustesCerebro)}
_cache: dict[tuple[int, int], tuple[datetime, AjustesCerebro]] = {}


def limpar_cache() -> None:
    """Util para testes."""
    _cache.clear()


def _cached(chave: tuple[int, int], agora: datetime) -> AjustesCerebro | None:
    entry = _cache.get(chave)
    if entry is None:
        return None
    ts, aj = entry
    if agora - ts > CACHE_TTL:
        _cache.pop(chave, None)
        return None
    return aj


def _store(chave: tuple[int, int], agora: datetime, aj: AjustesCerebro) -> None:
    _cache[chave] = (agora, aj)


def calcular_ajustes(
    home_id: int,
    away_id: int,
    *,
    minuto: int | None = None,
    score_casa: int | None = None,
    score_fora: int | None = None,
    agora: datetime | None = None,
    missing_casa: list | None = None,
    missing_fora: list | None = None,
) -> Optional[AjustesCerebro]:
    """Calcula ajustes do cerebro para 1 jogo.

    `missing_casa` e `missing_fora` sao listas opcionais de dicts com
    {"nome","tipo","razao"} (Fase 10). Afecta `lam_shots_<lado>_mult`
    quando ha pelo menos 3 ausencias claras do mesmo lado.

    Devolve None quando o confronto nao tem dados suficientes (<3 jogos
    numa das equipas). Nesse caso o predictor funciona sem ajustes
    (comportamento antigo).
    """
    agora = agora or agora_utc()
    chave = (int(home_id), int(away_id))

    cacheado = _cached(chave, agora)
    if cacheado is not None:
        return cacheado

    try:
        from apostas.alertas.confronto import analisar_confronto
    except Exception as exc:  # noqa: BLE001
        log.debug("import confronto falhou: %s", exc)
        return None

    try:
        res = analisar_confronto(
            home_id, away_id,
            minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("analisar_confronto falhou %s-%s: %s", home_id, away_id, exc)
        return None

    if res is None:
        return None

    aj = AjustesCerebro()

    # Padroes cruzados → ajustes
    for padrao in res.padroes_cruzados:
        if padrao.startswith("⚡"):
            # Ambas carregam 2T
            aj.lam_cards_mult *= 1.15
            aj.lam_shots_casa_mult *= 1.05
            aj.lam_shots_fora_mult *= 1.05
            aj.razoes.append("ambas carregam 2T → cards+15%, shots+5%")
            aj.notas.append(padrao)
        elif padrao.startswith("🟡"):
            # Discrepancia disciplinar
            aj.lam_cards_mult *= 1.10
            aj.razoes.append("discrepancia disciplinar → cards+10%")
            aj.notas.append(padrao)
        elif padrao.startswith("🎯"):
            # Posse clara → mais cantos
            aj.lam_cantos_mult *= 1.10
            aj.razoes.append("posse clara → cantos+10%")
            aj.notas.append(padrao)
        elif padrao.startswith("🔥"):
            # A perder + perfil "a perder dispara"
            # O texto do padrao diz qual equipa — procuramos "casa" / "fora"
            # mas e mais pratico: aplica em ambas (chasing symmetric)
            if "nome_casa" in padrao or score_casa is not None and score_fora is not None:
                if score_casa < score_fora:
                    aj.lam_shots_casa_mult *= 1.08
                elif score_fora < score_casa:
                    aj.lam_shots_fora_mult *= 1.08
            aj.razoes.append("a perder + perfil chasing → shots da perdedora+8%")
            aj.notas.append(padrao)
        elif padrao.startswith("🔄"):
            # Vira jogos — nao mexe no lambda, mas e flag para 1X2
            aj.notas.append(padrao)
        elif padrao.startswith("🏠"):
            # Fase 7: casa forte + adversario sofre fora → +golos casa
            aj.lam_golos_mult *= 1.08
            aj.lam_shots_casa_mult *= 1.05
            aj.razoes.append("casa × adv-sofre-fora → golos+8%, shots-casa+5%")
            aj.notas.append(padrao)
        elif padrao.startswith("✈"):
            # Fase 7: visitante perigoso fora
            aj.lam_golos_mult *= 1.08
            aj.lam_shots_fora_mult *= 1.05
            aj.razoes.append("visitante-perigoso-fora → golos+8%, shots-fora+5%")
            aj.notas.append(padrao)
        elif padrao.startswith("🥵"):
            # Fase 9: titulares desgastados → -shots 2a parte
            aj.lam_shots_casa_mult *= 0.95
            aj.lam_shots_fora_mult *= 0.95
            aj.razoes.append("titulares desgastados → shots-5%")
            aj.notas.append(padrao)

    # Fadiga: equipa com fadiga alta remata menos
    for alerta in res.alertas:
        if alerta.startswith("⏱") or alerta.startswith("🛌"):
            # Identificar casa/fora pela ocurrencia do nome
            if res.nome_casa in alerta:
                aj.lam_shots_casa_mult *= 0.95
                aj.razoes.append(f"fadiga {res.nome_casa} → shots-5%")
            elif res.nome_fora in alerta:
                aj.lam_shots_fora_mult *= 0.95
                aj.razoes.append(f"fadiga {res.nome_fora} → shots-5%")
            aj.notas.append(alerta)

    # Fase 10: ausencias. 3+ missing confirmados do mesmo lado → -5% shots
    # dessa equipa; 5+ → -8% golos tambem (equipa descompensada).
    def _n_missing_confirmados(lst):
        if not lst:
            return 0
        return sum(1 for a in lst if (a.get("tipo") or "").lower() == "missing")

    nc = _n_missing_confirmados(missing_casa)
    nf = _n_missing_confirmados(missing_fora)
    if nc >= 3:
        aj.lam_shots_casa_mult *= 0.95
        aj.razoes.append(f"casa {nc} ausencias → shots-5%")
        aj.notas.append(f"🚑 Casa: {nc} ausencias confirmadas")
    if nc >= 5:
        aj.lam_golos_mult *= 0.95
        aj.razoes.append(f"casa {nc} ausencias → golos-5%")
    if nf >= 3:
        aj.lam_shots_fora_mult *= 0.95
        aj.razoes.append(f"fora {nf} ausencias → shots-5%")
        aj.notas.append(f"🚑 Fora: {nf} ausencias confirmadas")
    if nf >= 5:
        aj.lam_golos_mult *= 0.95
        aj.razoes.append(f"fora {nf} ausencias → golos-5%")

    # Cap duro: nao deixar os multiplicadores saltarem alem destes limites
    aj.lam_golos_mult = max(0.75, min(1.30, aj.lam_golos_mult))
    aj.lam_cantos_mult = max(0.75, min(1.30, aj.lam_cantos_mult))
    aj.lam_cards_mult = max(0.75, min(1.30, aj.lam_cards_mult))
    aj.lam_shots_casa_mult = max(0.75, min(1.30, aj.lam_shots_casa_mult))
    aj.lam_shots_fora_mult = max(0.75, min(1.30, aj.lam_shots_fora_mult))

    _store(chave, agora, aj)
    return aj
