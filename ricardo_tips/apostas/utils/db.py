"""Base de dados SQLite — engine, sessões e criação do schema.

Padrão de uso::

    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import Liga

    with abrir_sessao() as s:
        s.add(Liga(nome="Premier League", pais="England", sportmonks_id=8))
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apostas.utils import config as _config
from apostas.utils.logger import get_logger
from apostas.utils.schema import Base

log = get_logger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def reset_engine() -> None:
    """Esquece a engine em cache. Útil em testes com BD temporária."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        caminho = _config.db_path()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{caminho.as_posix()}"
        _engine = create_engine(url, echo=False, future=True)
        log.debug("Engine SQLAlchemy criada: %s", url)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def abrir_sessao() -> Iterator[Session]:
    """Context manager: commit no fim, rollback se houver erro."""
    SessionLocal = get_sessionmaker()
    sessao = SessionLocal()
    try:
        yield sessao
        sessao.commit()
    except Exception:
        sessao.rollback()
        raise
    finally:
        sessao.close()


def criar_schema() -> None:
    """Cria todas as tabelas (idempotente — não apaga dados existentes)."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _acrescentar_colunas(engine)
    log.info("Schema criado/verificado em %s", _config.db_path())


def _acrescentar_colunas(engine: Engine) -> None:
    """create_all nao mexe em tabelas que ja existem: colunas novas (nullable)
    acrescentam-se aqui com ALTER TABLE, sem tocar nos dados."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    tabelas = set(insp.get_table_names())
    with engine.begin() as conn:
        for tabela in Base.metadata.sorted_tables:
            if tabela.name not in tabelas:
                continue
            existentes = {c["name"] for c in insp.get_columns(tabela.name)}
            for col in tabela.columns:
                if col.name in existentes or not col.nullable or col.primary_key:
                    continue
                tipo = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{col.name}" {tipo}'))
                log.info("BD: coluna nova %s.%s", tabela.name, col.name)
