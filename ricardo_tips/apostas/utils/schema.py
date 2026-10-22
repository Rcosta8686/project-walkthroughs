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

from apostas.utils.tempo import agora_utc


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
    # Expected Goals (xG) do Understat — sinal muito melhor que shot counts
    xg: Mapped[float | None] = mapped_column(Float)
    # Expected Goals Against — xG do adversário neste jogo
    xga: Mapped[float | None] = mapped_column(Float)

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
        DateTime, nullable=False, default=agora_utc
    )


class EstadoLiveSnapshot(Base):
    """Snapshot do estado de um jogo durante a transmissao in-play.

    Gravado pelo scanner_live_v2 a cada ciclo. Permite:
      - Backtest: dado o estado no min X, o que aconteceu ate ao fim?
      - Calibrar coeficientes do live_predictor com dados proprios
      - Identificar padroes (ex: 0-0 ao 80' com SoT 4+ tende a Over 0.5
        no final)

    event_id vem da Sofascore (unico global). jogo_id e ligacao ao
    nosso Jogo (nullable porque podemos ter snapshots de jogos que
    nunca entraram na BD).
    """

    __tablename__ = "estado_live_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    jogo_id: Mapped[int | None] = mapped_column(ForeignKey("jogos.id"), index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )
    liga: Mapped[str | None] = mapped_column(String(80))
    home: Mapped[str] = mapped_column(String(100), nullable=False)
    away: Mapped[str] = mapped_column(String(100), nullable=False)
    minuto: Mapped[int] = mapped_column(Integer, nullable=False)
    estado_jogo: Mapped[str | None] = mapped_column(String(30))

    marcador_casa: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    marcador_fora: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    xg_casa: Mapped[float | None] = mapped_column(Float)
    xg_fora: Mapped[float | None] = mapped_column(Float)
    shots_total_casa: Mapped[int | None] = mapped_column(Integer)
    shots_total_fora: Mapped[int | None] = mapped_column(Integer)
    shots_on_target_casa: Mapped[int | None] = mapped_column(Integer)
    shots_on_target_fora: Mapped[int | None] = mapped_column(Integer)
    shots_blocked_casa: Mapped[int | None] = mapped_column(Integer)
    shots_blocked_fora: Mapped[int | None] = mapped_column(Integer)
    big_chances_casa: Mapped[int | None] = mapped_column(Integer)
    big_chances_fora: Mapped[int | None] = mapped_column(Integer)
    posse_casa: Mapped[float | None] = mapped_column(Float)   # 0.0-1.0
    corners_casa: Mapped[int | None] = mapped_column(Integer)
    corners_fora: Mapped[int | None] = mapped_column(Integer)
    yellow_casa: Mapped[int | None] = mapped_column(Integer)
    yellow_fora: Mapped[int | None] = mapped_column(Integer)
    red_casa: Mapped[int | None] = mapped_column(Integer)
    red_fora: Mapped[int | None] = mapped_column(Integer)
    fouls_casa: Mapped[int | None] = mapped_column(Integer)
    fouls_fora: Mapped[int | None] = mapped_column(Integer)

    arbitro: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        # Permite dedupe (nao gravar snapshot do mesmo minuto duas vezes)
        UniqueConstraint("event_id", "minuto", name="uq_snapshot_evento_minuto"),
        Index("ix_snapshot_evento_ts", "event_id", "ts"),
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
        DateTime, nullable=False, default=agora_utc
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
        DateTime, nullable=False, default=agora_utc
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
        DateTime, nullable=False, default=agora_utc
    )
    # ganho | perdido | reembolso | pendente
    resultado: Mapped[str | None] = mapped_column(String(10))
    lucro: Mapped[float | None] = mapped_column(Float)


# ═════════════════════════ MELHORIAS M1-M14 ═══════════════════════════════
# Tabelas novas (nao mexem nas existentes). Adicionadas 2026-10-15.


class OddsSnapshotPrelive(Base):
    """Snapshot de odds pré-match num momento especifico (M2 — CLV).

    Usa sofascore_event_id directamente para evitar matching com jogos.id.
    O momento permite calcular CLV = odd_apostada / odd_fecho_justa - 1.

    Momentos canonicos: "abertura" | "+1h" | "pre_onzes" | "pos_onzes" | "fecho"
    """

    __tablename__ = "odds_snapshot_prelive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sofascore_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    linha: Mapped[float | None] = mapped_column(Float)
    lado: Mapped[str] = mapped_column(String(20), nullable=False)
    odd: Mapped[float] = mapped_column(Float, nullable=False)
    casa: Mapped[str] = mapped_column(String(40), nullable=False)
    momento: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )

    __table_args__ = (
        Index(
            "ix_odds_snap_evento_mercado",
            "sofascore_event_id", "mercado", "linha", "lado", "momento",
        ),
    )


class EquipaPerfil(Base):
    """Perfil comportamental acumulado por equipa (user ask 2026-10-15).

    Actualizado a cada 24h a partir dos ultimos 10-20 jogos.
    Serve para enriquecer analise pre-match: ex. "Juventus tem caudal
    ofensivo de 18 remates/90 quando perde" — ajusta λ_remates quando
    jogo esperado for em desvantagem.

    sofascore_team_id como chave: Sofascore é a fonte canónica (user).
    """

    __tablename__ = "equipa_perfil"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sofascore_team_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    liga: Mapped[str | None] = mapped_column(String(80))

    # Comportamento quando perde (caudal ofensivo em desvantagem)
    remates_atras_90: Mapped[float | None] = mapped_column(Float)
    sot_atras_90: Mapped[float | None] = mapped_column(Float)
    cantos_atras_90: Mapped[float | None] = mapped_column(Float)
    golos_em_atraso_media: Mapped[float | None] = mapped_column(Float)
    virar_jogos_pct: Mapped[float | None] = mapped_column(Float)  # 0.0-1.0

    # Padrao por parte
    remates_1a_parte_media: Mapped[float | None] = mapped_column(Float)
    remates_2a_parte_media: Mapped[float | None] = mapped_column(Float)
    cantos_1a_parte_media: Mapped[float | None] = mapped_column(Float)
    cantos_2a_parte_media: Mapped[float | None] = mapped_column(Float)

    # Baseline
    remates_media_90: Mapped[float | None] = mapped_column(Float)
    sot_media_90: Mapped[float | None] = mapped_column(Float)
    cantos_media_90: Mapped[float | None] = mapped_column(Float)
    cards_media_90: Mapped[float | None] = mapped_column(Float)
    fouls_media_90: Mapped[float | None] = mapped_column(Float)
    golos_marcados_90: Mapped[float | None] = mapped_column(Float)
    golos_sofridos_90: Mapped[float | None] = mapped_column(Float)

    # Fadiga e carga
    carga_jogos_7d: Mapped[int | None] = mapped_column(Integer)
    carga_jogos_14d: Mapped[int | None] = mapped_column(Integer)
    viagens_longas_14d: Mapped[int | None] = mapped_column(Integer)

    # Meta
    n_jogos_analisados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_atualizacao: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc
    )

    # Notas livres (padroes nao-numericos)
    notas: Mapped[str | None] = mapped_column(Text)


class EstatisticaEquipaJogo(Base):
    """Historico PERMANENTE de stats por equipa por jogo, com split por parte.

    Esta tabela e append-only (nunca re-escrita, nunca apagada). Serve de
    base para:
      - calcular perfil comportamental (perfil_equipa v2) sem re-scrape
      - saber o que cada equipa faz quando ganha/perde/empata
      - saber o que cada equipa faz 1T vs 2T
      - medir fadiga (dias de descanso) e carga de jogos
      - alimentar projecoes live com baseline por equipa

    Chave composta (sofascore_team_id, sofascore_event_id) para evitar
    duplicados. Pode haver 2 linhas por jogo (casa e fora).
    """

    __tablename__ = "estatistica_equipa_jogo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identificacao
    sofascore_team_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sofascore_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    nome_equipa: Mapped[str] = mapped_column(String(100), nullable=False)
    liga: Mapped[str | None] = mapped_column(String(80), index=True)
    data_jogo: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    era_casa: Mapped[bool] = mapped_column(Integer, nullable=False)  # 0/1 sqlite

    # Resultado final e intervalo
    golos_marcados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    golos_sofridos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    golos_marcados_1t: Mapped[int | None] = mapped_column(Integer)
    golos_sofridos_1t: Mapped[int | None] = mapped_column(Integer)
    resultado: Mapped[str] = mapped_column(String(1), nullable=False)  # W/D/L
    resultado_1t: Mapped[str | None] = mapped_column(String(1))  # W/D/L ao intervalo

    # Stats TOTAL (jogo inteiro)
    remates: Mapped[float | None] = mapped_column(Float)
    sot: Mapped[float | None] = mapped_column(Float)
    cantos: Mapped[float | None] = mapped_column(Float)
    cards_amarelos: Mapped[float | None] = mapped_column(Float)
    cards_vermelhos: Mapped[float | None] = mapped_column(Float)
    fouls: Mapped[float | None] = mapped_column(Float)
    posse_pct: Mapped[float | None] = mapped_column(Float)
    passes: Mapped[float | None] = mapped_column(Float)
    passes_pct: Mapped[float | None] = mapped_column(Float)

    # Stats por parte (split 1T/2T)
    remates_1t: Mapped[float | None] = mapped_column(Float)
    remates_2t: Mapped[float | None] = mapped_column(Float)
    sot_1t: Mapped[float | None] = mapped_column(Float)
    sot_2t: Mapped[float | None] = mapped_column(Float)
    cantos_1t: Mapped[float | None] = mapped_column(Float)
    cantos_2t: Mapped[float | None] = mapped_column(Float)
    cards_amarelos_1t: Mapped[float | None] = mapped_column(Float)
    cards_amarelos_2t: Mapped[float | None] = mapped_column(Float)
    fouls_1t: Mapped[float | None] = mapped_column(Float)
    fouls_2t: Mapped[float | None] = mapped_column(Float)

    # Contexto (fadiga, carga)
    dias_desde_jogo_anterior: Mapped[int | None] = mapped_column(Integer)
    jogo_europeu_antes: Mapped[bool | None] = mapped_column(Integer)  # 0/1
    minutos_jogados: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    # Fase 8: estadio (venue.name do raw event Sofascore). Preenchido
    # em `gravar_jogo_terminado` e via `scripts/backfill_venue.py` para
    # linhas antigas.
    venue: Mapped[str | None] = mapped_column(String(100), index=True)

    # Nivel 3: stats detalhadas + arbitro no historico persistente.
    # arbitro cruzar com `referencias.fator_arbitro` para explicar perfil
    # de cards de arbitro em futuros alertas de cartoes do mesmo juiz.
    arbitro: Mapped[str | None] = mapped_column(String(100), index=True)
    # Stats avancadas (jogo inteiro). Nullable quando Sofascore nao
    # devolveu (ligas pequenas / jogos antigos).
    passes_longos_total: Mapped[int | None] = mapped_column(Integer)
    passes_longos_pct: Mapped[float | None] = mapped_column(Float)
    duels_won_pct: Mapped[float | None] = mapped_column(Float)
    interceptions: Mapped[int | None] = mapped_column(Integer)
    tackles_total: Mapped[int | None] = mapped_column(Integer)
    offsides: Mapped[int | None] = mapped_column(Integer)
    pass_accuracy_final_third: Mapped[float | None] = mapped_column(Float)

    # Meta
    data_ingestao: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )
    fonte: Mapped[str] = mapped_column(String(30), nullable=False, default="sofascore")

    __table_args__ = (
        UniqueConstraint(
            "sofascore_team_id", "sofascore_event_id",
            name="uq_estatistica_equipa_jogo",
        ),
        Index("ix_estatistica_equipa_data", "sofascore_team_id", "data_jogo"),
    )


class EstatisticaJogadorJogo(Base):
    """Historico PERMANENTE de stats por JOGADOR por jogo (Fase 6, user ask).

    Append-only, nunca re-escrita. Serve para:
      - calcular perfil jogador (agressividade, forma, papel tactico)
      - confronto tactico (atacante driblador vs defesa agressivo
        → amarelo provavel para o defesa)
      - alerta jogador ja amarelado → risco 2o cartao

    Chave composta (sofascore_player_id, sofascore_event_id) impede duplicados.
    """

    __tablename__ = "estatistica_jogador_jogo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identificacao
    sofascore_player_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sofascore_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sofascore_team_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    nome_jogador: Mapped[str] = mapped_column(String(100), nullable=False)
    posicao: Mapped[str | None] = mapped_column(String(10))  # GK, LB, CB, RB, CDM, CM, CAM, LW, RW, ST
    liga: Mapped[str | None] = mapped_column(String(80))
    data_jogo: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    era_casa: Mapped[bool] = mapped_column(Integer, nullable=False)

    # Participacao
    foi_titular: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    minutos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating: Mapped[float | None] = mapped_column(Float)  # Sofascore rating 0-10

    # Producao
    golos: Mapped[int | None] = mapped_column(Integer)
    assistencias: Mapped[int | None] = mapped_column(Integer)
    remates: Mapped[int | None] = mapped_column(Integer)
    sot: Mapped[int | None] = mapped_column(Integer)
    grandes_oportunidades: Mapped[int | None] = mapped_column(Integer)
    grandes_oportunidades_falhadas: Mapped[int | None] = mapped_column(Integer)

    # Passes
    passes: Mapped[int | None] = mapped_column(Integer)
    passes_pct: Mapped[float | None] = mapped_column(Float)
    passes_chave: Mapped[int | None] = mapped_column(Integer)

    # Defesa / duels
    tackles: Mapped[int | None] = mapped_column(Integer)
    interceptacoes: Mapped[int | None] = mapped_column(Integer)
    desarmes: Mapped[int | None] = mapped_column(Integer)
    duels_won: Mapped[int | None] = mapped_column(Integer)
    duels_lost: Mapped[int | None] = mapped_column(Integer)
    dribles_tentados: Mapped[int | None] = mapped_column(Integer)
    dribles_sucesso: Mapped[int | None] = mapped_column(Integer)

    # Disciplina
    fouls_committed: Mapped[int | None] = mapped_column(Integer)
    fouls_drawn: Mapped[int | None] = mapped_column(Integer)
    cards_amarelos: Mapped[int | None] = mapped_column(Integer)
    cards_vermelhos: Mapped[int | None] = mapped_column(Integer)
    minuto_amarelo: Mapped[int | None] = mapped_column(Integer)  # quando foi apontado
    minuto_vermelho: Mapped[int | None] = mapped_column(Integer)

    data_ingestao: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "sofascore_player_id", "sofascore_event_id",
            name="uq_estatistica_jogador_jogo",
        ),
        Index("ix_jogador_data", "sofascore_player_id", "data_jogo"),
    )


class ProjecaoLive(Base):
    """Projecao enviada ao Telegram num minuto X de um jogo.

    Grava para medir calibracao mais tarde (Brier) e refinar modelo.
    Append-only.
    """

    __tablename__ = "projecao_live"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    sofascore_event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    minuto: Mapped[int] = mapped_column(Integer, nullable=False)  # 40, 55, 70...
    score_casa: Mapped[int] = mapped_column(Integer, nullable=False)
    score_fora: Mapped[int] = mapped_column(Integer, nullable=False)
    nome_casa: Mapped[str] = mapped_column(String(100), nullable=False)
    nome_fora: Mapped[str] = mapped_column(String(100), nullable=False)
    liga: Mapped[str | None] = mapped_column(String(80))

    # Mercado projectado: "sot"|"remates"|"cantos"
    mercado: Mapped[str] = mapped_column(String(20), nullable=False)

    # Valor atual + projecao final
    valor_atual_total: Mapped[float] = mapped_column(Float, nullable=False)
    projecao_final_total: Mapped[float] = mapped_column(Float, nullable=False)
    valor_atual_casa: Mapped[float | None] = mapped_column(Float)
    valor_atual_fora: Mapped[float | None] = mapped_column(Float)
    projecao_final_casa: Mapped[float | None] = mapped_column(Float)
    projecao_final_fora: Mapped[float | None] = mapped_column(Float)

    # Odds justas por linha (JSON serializado)
    odds_justas_json: Mapped[str | None] = mapped_column(Text)

    data_envio: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "sofascore_event_id", "minuto", "mercado",
            name="uq_projecao_live",
        ),
    )


class FalhaAlerta(Base):
    """Post-mortem de 1 alerta que resultou em L (M20 — espeleologia).

    Guarda informacao suficiente para agregar padroes ("os overs falham
    quando o jogo e 2-2 ao min 55 porque o modelo esquece-se do break")
    sem precisar de re-visitar o Sofascore. Uma linha por alerta L.
    """

    __tablename__ = "falhas_alerta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, unique=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    liga: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    lado: Mapped[str] = mapped_column(String(20), nullable=False)
    linha: Mapped[str | None] = mapped_column(String(10))
    minuto: Mapped[int | None] = mapped_column(Integer)
    prob_modelo: Mapped[float | None] = mapped_column(Float)
    ev: Mapped[float | None] = mapped_column(Float)
    marcador_no_alerta: Mapped[str | None] = mapped_column(String(10))
    marcador_final: Mapped[str | None] = mapped_column(String(10))
    # Categoria do padrao da falha. Vazio = por classificar.
    # Ex: 'linha_pouco_fiavel' | 'golo_tardio_perdido' | 'cards_sem_arbitro_estrito'
    # | 'xg_contra' | 'onze_errado' | 'other'
    categoria: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    nota: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc
    )


class Hipotese(Base):
    """Registo cientifico de hipoteses (M21 — hypothesis ledger).

    Antes de alterar o modelo, grava-se a hipotese ("over 2.5 na 2a parte
    quando CASA perde por 1 → mais golos") e observa-se N alertas. No
    fim, confirma/refuta/inconclusivo e explica porque.
    """

    __tablename__ = "hipoteses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )
    # ativa | confirmada | refutada | inconclusiva
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="ativa")
    evidencia_a_favor: Mapped[str | None] = mapped_column(Text)
    evidencia_contra: Mapped[str | None] = mapped_column(Text)
    decisao_final: Mapped[str | None] = mapped_column(Text)
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime)


class ApostaRealLive(Base):
    """Aposta real registada por /apostei a partir de um alerta live.

    Diferente de ``ApostaReal`` (que exige sugestao pre-match da BD): esta
    liga-se a uma linha do CSV ``sugestoes_live_v2.csv`` via ``alert_id``.
    Guarda a odd/stake efetivamente executada pelo user + resultado quando
    o resolver live resolve o jogo.
    """

    __tablename__ = "apostas_reais_live"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, unique=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    liga: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    home: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    away: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    minuto: Mapped[int | None] = mapped_column(Integer)
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    lado: Mapped[str] = mapped_column(String(20), nullable=False)
    linha: Mapped[str | None] = mapped_column(String(10))
    casa_recomendada: Mapped[str | None] = mapped_column(String(40))
    odd_recomendada: Mapped[float | None] = mapped_column(Float)
    odd_executada: Mapped[float] = mapped_column(Float, nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    prob_modelo: Mapped[float | None] = mapped_column(Float)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc
    )
    # pendente | W | L | push
    resultado: Mapped[str | None] = mapped_column(String(10))
    lucro: Mapped[float | None] = mapped_column(Float)
    # CLV calculado quando o resolver fecha a aposta (opcional)
    clv: Mapped[float | None] = mapped_column(Float)


class SinalMegaCerebro(Base):
    """Sinal emitido pelo mega-cerebro durante um jogo (Fase D).

    Cada emissao e gravada aqui. Quando o jogo acaba, o resolver vai ver
    se o sinal (ex: over cantos) se confirmou olhando ao total final.
    O relatorio semanal cruza isto para dizer qual heuristica acerta.
    """

    __tablename__ = "sinais_mega_cerebro"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    liga: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    home: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    away: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    minuto_emitido: Mapped[int] = mapped_column(Integer, nullable=False)
    score_casa_emitido: Mapped[int] = mapped_column(Integer, nullable=False)
    score_fora_emitido: Mapped[int] = mapped_column(Integer, nullable=False)
    # Dados live no momento (para debug/analise)
    sot_c: Mapped[int | None] = mapped_column(Integer)
    sot_f: Mapped[int | None] = mapped_column(Integer)
    cantos_c: Mapped[int | None] = mapped_column(Integer)
    cantos_f: Mapped[int | None] = mapped_column(Integer)
    cards_c: Mapped[int | None] = mapped_column(Integer)
    cards_f: Mapped[int | None] = mapped_column(Integer)
    # Sintese
    mercado: Mapped[str] = mapped_column(String(30), nullable=False)
    direccao: Mapped[str] = mapped_column(String(10), nullable=False)
    razao: Mapped[str] = mapped_column(Text, nullable=False)
    confianca: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Classificacao da heuristica (razao → tag curta, para agregar relatorio)
    heuristica: Mapped[str] = mapped_column(String(30), nullable=False, default="?")
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )
    # Resolucao: pendente | acerto | falha | nao_resolvido
    resolvido: Mapped[str] = mapped_column(
        String(15), nullable=False, default="pendente"
    )
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime)
    # Valor final observado (ex: total cantos no jogo = 11)
    valor_final: Mapped[float | None] = mapped_column(Float)


class BancaRegisto(Base):
    """Registo diario de banca para tracking + stop-loss (M13).

    Snapshot do saldo + apostas abertas no fim do dia. Usado para
    calcular perda_dia e perda_semana vs limites.
    """

    __tablename__ = "banca_registo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True, unique=True
    )
    saldo_chf: Mapped[float] = mapped_column(Float, nullable=False)
    apostas_abertas_stake: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    apostas_abertas_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lucro_dia: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stop_loss_atingido: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=0  # 0=False, 1=True (sqlite)
    )
    notas: Mapped[str | None] = mapped_column(Text)
