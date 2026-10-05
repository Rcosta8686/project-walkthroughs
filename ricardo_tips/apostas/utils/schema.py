"""Modelos SQLAlchemy — schema da base de dados.

Qualquer alteração aqui requer uma migração manual (apagar `dados/ricardo_tips.db`
e correr `python scripts/inicializar_bd.py`). Enquanto o projeto não tiver
dados reais, isto é aceitável.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Liga(Base):
    __tablename__ = "ligas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    pais: Mapped[str] = mapped_column(String(100), nullable=False)
    sportmonks_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)

    equipas: Mapped[list["Equipa"]] = relationship(back_populates="liga")
    jogos: Mapped[list["Jogo"]] = relationship(back_populates="liga")


class Equipa(Base):
    __tablename__ = "equipas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    liga_id: Mapped[int] = mapped_column(ForeignKey("ligas.id"), nullable=False)
    # Nullable: a ingestão football-data.co.uk cria equipas sem este id;
    # é preenchido quando a ingestão Sportmonks as reconcilia por alias.
    sportmonks_id: Mapped[int | None] = mapped_column(Integer, unique=True)

    liga: Mapped[Liga] = relationship(back_populates="equipas")
    aliases: Mapped[list["EquipaAlias"]] = relationship(back_populates="equipa")

    __table_args__ = (
        UniqueConstraint("nome", "liga_id", name="uq_equipa_nome_liga"),
    )


class EquipaAlias(Base):
    """Nomes alternativos para a mesma equipa, usados por diferentes fontes.

    Ex.: "Manchester United" (Sportmonks) ↔ "Man United" (football-data.co.uk).
    """

    __tablename__ = "equipa_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipa_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(150), nullable=False)
    fonte: Mapped[str] = mapped_column(String(30), nullable=False)  # sportmonks | football_data

    equipa: Mapped[Equipa] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("alias", "fonte", name="uq_alias_fonte"),
    )


class Jogo(Base):
    __tablename__ = "jogos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ID vindo da fonte externa que ingeriu o jogo (Sportmonks ou chave
    # sintética do football-data.co.uk — ver `_chave_jogo_fd`).
    id_externo: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True
    )
    liga_id: Mapped[int] = mapped_column(ForeignKey("ligas.id"), nullable=False)
    # Ano de início da época: 2024 = época 2024/25
    epoca: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    jornada: Mapped[int | None] = mapped_column(Integer)
    data_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    casa_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False)
    fora_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False)
    golos_casa: Mapped[int | None] = mapped_column(Integer)
    golos_fora: Mapped[int | None] = mapped_column(Integer)
    # agendado | em_curso | terminado | adiado | cancelado
    estado: Mapped[str] = mapped_column(String(30), nullable=False, default="agendado")

    liga: Mapped[Liga] = relationship(back_populates="jogos")

    __table_args__ = (
        Index("ix_jogos_data_estado", "data_utc", "estado"),
    )


class EstatisticasJogo(Base):
    __tablename__ = "estatisticas_jogo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id"), nullable=False, index=True)
    equipa_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False)
    remates: Mapped[int | None] = mapped_column(Integer)
    remates_a_baliza: Mapped[int | None] = mapped_column(Integer)
    cantos: Mapped[int | None] = mapped_column(Integer)
    posse_bola: Mapped[float | None] = mapped_column(Float)  # 0-100
    cartoes_amarelos: Mapped[int | None] = mapped_column(Integer)
    cartoes_vermelhos: Mapped[int | None] = mapped_column(Integer)
    faltas: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("jogo_id", "equipa_id", name="uq_stats_jogo_equipa"),
    )


class OddsFecho(Base):
    """Odds de fecho (closing odds) de football-data.co.uk."""

    __tablename__ = "odds_fecho"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id"), nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)  # golos|cantos|cartoes|1x2
    linha: Mapped[float | None] = mapped_column(Float)
    lado: Mapped[str] = mapped_column(String(10), nullable=False)  # over|under|casa|empate|fora
    odd: Mapped[float] = mapped_column(Float, nullable=False)
    casa_de_apostas: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "jogo_id", "mercado", "linha", "lado", "casa_de_apostas",
            name="uq_odds_fecho",
        ),
    )


class OddsCorrentes(Base):
    """Snapshot pré-jogo das odds correntes (introduzidas no bot ou via scrape)."""

    __tablename__ = "odds_correntes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id"), nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    linha: Mapped[float | None] = mapped_column(Float)
    lado: Mapped[str] = mapped_column(String(10), nullable=False)
    odd: Mapped[float] = mapped_column(Float, nullable=False)
    casa_de_apostas: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class Alinhamento(Base):
    __tablename__ = "alinhamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id"), nullable=False, index=True)
    equipa_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False)
    formacao: Mapped[str | None] = mapped_column(String(20))  # ex.: 4-3-3
    jogadores_json: Mapped[str | None] = mapped_column(Text)   # JSON serializado

    __table_args__ = (
        UniqueConstraint("jogo_id", "equipa_id", name="uq_alinhamento"),
    )


class Lesao(Base):
    __tablename__ = "lesoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipa_id: Mapped[int] = mapped_column(ForeignKey("equipas.id"), nullable=False, index=True)
    jogador: Mapped[str] = mapped_column(String(150), nullable=False)
    tipo: Mapped[str | None] = mapped_column(String(100))  # joelho | suspensão | dúvida
    data_estimada_regresso: Mapped[datetime | None] = mapped_column(DateTime)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class Sugestao(Base):
    """Aposta sugerida pelo modelo (EV >= limiar configurado)."""

    __tablename__ = "sugestoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id"), nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    linha: Mapped[float | None] = mapped_column(Float)
    lado: Mapped[str] = mapped_column(String(10), nullable=False)
    prob_modelo: Mapped[float] = mapped_column(Float, nullable=False)
    odd_referencia: Mapped[float] = mapped_column(Float, nullable=False)
    ev: Mapped[float] = mapped_column(Float, nullable=False)
    enviado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    estado: Mapped[str] = mapped_column(String(20), default="pendente")  # pendente|aceite|ignorado


class ApostaReal(Base):
    """Aposta efetivamente feita pelo utilizador (registada via comando /registar)."""

    __tablename__ = "apostas_reais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sugestao_id: Mapped[int] = mapped_column(
        ForeignKey("sugestoes.id"), nullable=False, index=True
    )
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    odd_executada: Mapped[float] = mapped_column(Float, nullable=False)
    casa_de_apostas: Mapped[str] = mapped_column(String(30), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # ganho | perdido | reembolso | pendente
    resultado: Mapped[str | None] = mapped_column(String(10))
    lucro: Mapped[float | None] = mapped_column(Float)
