"""Ingestão de ligas e equipas.

Puxa as 5 ligas configuradas em `config.yaml` e todas as equipas da
época atual, gravando-as nas tabelas `ligas` e `equipas`. Idempotente.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from apostas.ingestao.api_football import ClienteApiFootball, cliente
from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, Liga

log = get_logger(__name__)


@dataclass
class ResultadoIngestao:
    ligas_criadas: int = 0
    ligas_existentes: int = 0
    equipas_criadas: int = 0
    equipas_existentes: int = 0


def sincronizar(cli: ClienteApiFootball | None = None) -> ResultadoIngestao:
    """Puxa ligas + equipas e persiste. Devolve um resumo."""
    cli = cli or cliente()
    cfg = load_config()
    resultado = ResultadoIngestao()

    with abrir_sessao() as s:
        for liga_cfg in cfg["ligas"]:
            api_id = int(liga_cfg["api_football_id"])
            liga = _upsert_liga(s, api_id, liga_cfg["nome"], liga_cfg["pais"], resultado, cli)
            _sincronizar_equipas(s, liga, cli, resultado)

    log.info(
        "Ingestão concluída: %d ligas (%d novas) · %d equipas (%d novas)",
        resultado.ligas_criadas + resultado.ligas_existentes,
        resultado.ligas_criadas,
        resultado.equipas_criadas + resultado.equipas_existentes,
        resultado.equipas_criadas,
    )
    return resultado


def _upsert_liga(
    s,
    api_id: int,
    nome: str,
    pais: str,
    resultado: ResultadoIngestao,
    cli: ClienteApiFootball,
) -> Liga:
    existente = s.scalar(select(Liga).where(Liga.api_football_id == api_id))
    if existente is not None:
        resultado.ligas_existentes += 1
        return existente

    # Confirma na API que a liga existe (opcional mas valida config)
    resp = cli.get("/leagues", params={"id": api_id})
    if resp["results"] == 0:
        raise RuntimeError(
            f"API-Football não encontrou liga com id={api_id} ({nome}). "
            "Verifica o teu config.yaml."
        )

    liga = Liga(api_football_id=api_id, nome=nome, pais=pais)
    s.add(liga)
    s.flush()  # para ter o liga.id disponível
    resultado.ligas_criadas += 1
    log.info("Criada liga %s (api_id=%d)", nome, api_id)
    return liga


def _sincronizar_equipas(
    s,
    liga: Liga,
    cli: ClienteApiFootball,
    resultado: ResultadoIngestao,
) -> None:
    cfg = load_config()
    epoca_atual = max(
        cfg.get("modelo", {}).get("epoca_atual", 2024),
        2024,
    )
    resp = cli.get("/teams", params={"league": liga.api_football_id, "season": epoca_atual})

    for item in resp["response"]:
        api_id = int(item["team"]["id"])
        nome = item["team"]["name"]

        existente = s.scalar(select(Equipa).where(Equipa.api_football_id == api_id))
        if existente is not None:
            resultado.equipas_existentes += 1
            continue

        equipa = Equipa(api_football_id=api_id, nome=nome, liga_id=liga.id)
        s.add(equipa)
        resultado.equipas_criadas += 1
