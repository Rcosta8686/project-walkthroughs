"""Bootstrap das ligas a partir do config.yaml.

Antes dependíamos da Sportmonks para criar ligas e equipas, mas o plano
tornou-se demasiado limitado. Agora:

  - As ligas são criadas directamente a partir de ``config.yaml`` (não precisa
    de API externa).
  - As equipas são criadas pelo football-data e pelo Understat conforme
    aparecem nos CSVs/HTML — já faziam isso.
  - Aliases Sportmonks ficam na tabela só por compatibilidade; o campo
    ``sportmonks_id`` já era nullable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from apostas.utils.config import load_config
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Liga

log = get_logger(__name__)


@dataclass
class ResultadoIngestao:
    ligas_criadas: int = 0
    ligas_existentes: int = 0
    equipas_criadas: int = 0
    equipas_existentes: int = 0


def sincronizar(cli: object | None = None) -> ResultadoIngestao:  # noqa: ARG001
    """Garante que as ligas do config.yaml existem na BD.

    O parâmetro ``cli`` é aceite por compatibilidade com os testes antigos
    (que lhe passavam um cliente Sportmonks); hoje é ignorado.
    """
    cfg = load_config()
    resultado = ResultadoIngestao()

    with abrir_sessao() as s:
        for liga_cfg in cfg["ligas"]:
            nome = liga_cfg["nome"]
            sm_id = int(liga_cfg.get("sportmonks_id") or 0) or None
            existente = s.scalar(select(Liga).where(Liga.nome == nome))
            if existente is not None:
                resultado.ligas_existentes += 1
                continue
            s.add(Liga(nome=nome, pais=liga_cfg["pais"], sportmonks_id=sm_id))
            resultado.ligas_criadas += 1
            log.info("Criada liga %s", nome)

    log.info(
        "Ligas: %d novas, %d já existentes.",
        resultado.ligas_criadas, resultado.ligas_existentes,
    )
    return resultado
