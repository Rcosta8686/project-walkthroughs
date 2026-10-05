"""Ingestão de ligas e equipas via Sportmonks.

Puxa as ligas configuradas em `config.yaml` e todas as equipas da época
atual, gravando-as nas tabelas `ligas` e `equipas`. Idempotente.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from apostas.ingestao import aliases as _aliases
from apostas.ingestao.sportmonks import ClienteSportmonks, cliente
from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, EquipaAlias, Liga

log = get_logger(__name__)


@dataclass
class ResultadoIngestao:
    ligas_criadas: int = 0
    ligas_existentes: int = 0
    equipas_criadas: int = 0
    equipas_existentes: int = 0


def sincronizar(cli: ClienteSportmonks | None = None) -> ResultadoIngestao:
    """Puxa ligas + equipas da Sportmonks e persiste. Devolve um resumo."""
    cli = cli or cliente()
    cfg = load_config()
    resultado = ResultadoIngestao()

    with abrir_sessao() as s:
        for liga_cfg in cfg["ligas"]:
            sm_id = int(liga_cfg["sportmonks_id"])
            liga, resp_liga = _upsert_liga(
                s, sm_id, liga_cfg["nome"], liga_cfg["pais"], resultado, cli
            )
            season_id = _epoca_atual_id(resp_liga)
            if season_id is None:
                log.warning("Liga %s sem época atual na resposta Sportmonks", liga_cfg["nome"])
                continue
            _sincronizar_equipas(s, liga, season_id, cli, resultado)

    log.info(
        "Ingestão Sportmonks concluída: %d ligas (%d novas) · %d equipas (%d novas)",
        resultado.ligas_criadas + resultado.ligas_existentes,
        resultado.ligas_criadas,
        resultado.equipas_criadas + resultado.equipas_existentes,
        resultado.equipas_criadas,
    )
    return resultado


def _upsert_liga(
    s,
    sm_id: int,
    nome: str,
    pais: str,
    resultado: ResultadoIngestao,
    cli: ClienteSportmonks,
) -> tuple[Liga, dict]:
    resp = cli.get(f"/leagues/{sm_id}")
    if not resp.get("data"):
        raise RuntimeError(
            f"Sportmonks não devolveu dados para liga id={sm_id} ({nome}). "
            "Verifica o teu config.yaml e o teu plano."
        )

    existente = s.scalar(select(Liga).where(Liga.sportmonks_id == sm_id))
    if existente is not None:
        resultado.ligas_existentes += 1
        return existente, resp

    liga = Liga(sportmonks_id=sm_id, nome=nome, pais=pais)
    s.add(liga)
    s.flush()
    resultado.ligas_criadas += 1
    log.info("Criada liga %s (sportmonks_id=%d)", nome, sm_id)
    return liga, resp


def _epoca_atual_id(resp_liga: dict) -> int | None:
    data = resp_liga.get("data") or {}
    season = data.get("currentseason")
    if not season:
        return None
    return int(season["id"])


def _sincronizar_equipas(
    s,
    liga: Liga,
    season_id: int,
    cli: ClienteSportmonks,
    resultado: ResultadoIngestao,
) -> None:
    resp = cli.get(f"/teams/seasons/{season_id}")
    for item in resp.get("data", []):
        sm_id = int(item["id"])
        nome = item["name"]

        existente = s.scalar(select(Equipa).where(Equipa.sportmonks_id == sm_id))
        if existente is not None:
            resultado.equipas_existentes += 1
            continue

        equipa = Equipa(sportmonks_id=sm_id, nome=nome, liga_id=liga.id)
        s.add(equipa)
        s.flush()
        resultado.equipas_criadas += 1

        # Regista todos os aliases conhecidos para esta equipa
        _registar_aliases(s, equipa)


def _registar_aliases(s, equipa: Equipa) -> None:
    """Insere na tabela equipa_aliases os aliases conhecidos para esta equipa."""
    # Alias para a própria Sportmonks (nome canónico)
    _add_alias_se_novo(s, equipa.id, equipa.nome, _aliases.FONTE_SPORTMONKS)
    # Aliases para o football-data.co.uk
    for alias in _aliases.aliases_de(equipa.nome, _aliases.FONTE_FOOTBALL_DATA):
        _add_alias_se_novo(s, equipa.id, alias, _aliases.FONTE_FOOTBALL_DATA)


def _add_alias_se_novo(s, equipa_id: int, alias: str, fonte: str) -> None:
    existente = s.scalar(
        select(EquipaAlias).where(EquipaAlias.alias == alias, EquipaAlias.fonte == fonte)
    )
    if existente is None:
        s.add(EquipaAlias(equipa_id=equipa_id, alias=alias, fonte=fonte))
