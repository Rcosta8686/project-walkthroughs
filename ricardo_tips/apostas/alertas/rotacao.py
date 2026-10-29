"""Deteccao de rotacao / taca.

Protege o modelo de 2 situacoes em que o perfil habitual da equipa
*nao* se aplica:

  Camada 1 — competicao de rotacao alta
  --------------------------------------
  Jogos de Taca sao jogados pelos onze B. O cerebro, treinado com
  jogos de Liga, projecta posse e remates do onze principal — fica
  completamente errado. Baseado no nome da liga, sinalizamos a
  possibilidade e aplicamos factor 0.7 nas projecoes.

  Camada 2 — lineup pre-match vs titulares habituais
  ----------------------------------------------------
  Com `EstatisticaJogadorJogo` sabemos os 11 jogadores com mais
  minutos nos ultimos N jogos (= titulares habituais). Quando o
  Sofascore disponibiliza o lineup pre-match, comparamos: se mais
  de 3 habituais estao de fora, sinalizamos "equipa rotada" com
  factor real.

Exemplo real (Ricardo, 28/10/2026): Porto vs Academico de Viseu
(Taca de Portugal) — Porto joga em 5-3-2 com 11 jogadores B. Perfil
habitual (4-2-3-1 ofensivo) nao se aplica.
"""
from __future__ import annotations

from dataclasses import dataclass


# ─── Camada 1: competicoes com rotacao alta ───────────────────────────

_SUBSTRINGS_TACA = (
    # Portugal
    "taca de portugal", "taca da liga portugal", "allianz cup",
    # Inglaterra
    "fa cup", "efl cup", "carabao cup", "league cup",
    # Espanha
    "copa del rey",
    # Italia
    "coppa italia",
    # Alemanha
    "dfb pokal", "dfb-pokal",
    # Franca
    "coupe de france", "coupe de la ligue",
    # Holanda
    "knvb beker",
    # Outras ligas internacionais menores que tambem rodam
    "us open cup",
)

# Factor de atenuacao aplicado sobre as projecoes quando e taca.
# 0.7 significa "esperamos 70% do perfil habitual" — reduz ritmo,
# reduz remates, reduz cantos. Nao mexe em cards (podem ate aumentar
# com jogadores menos coordenados).
_FACTOR_TACA = 0.7


def is_competicao_rotacao_alta(liga_nome: str | None) -> bool:
    """True se o nome da liga sugere taca com rotacao alta."""
    if not liga_nome:
        return False
    n = liga_nome.lower()
    return any(sub in n for sub in _SUBSTRINGS_TACA)


def factor_taca() -> float:
    """Multiplicador aplicado sobre projecoes em jogos de taca."""
    return _FACTOR_TACA


# ─── Camada 2: lineup real vs titulares habituais ─────────────────────

@dataclass
class RelatorioRotacao:
    """Comparacao lineup actual vs titulares habituais."""
    team_id: int
    nome_equipa: str
    habituais_em_campo: int   # dos 11 habituais, quantos estao a jogar
    habituais_total: int       # 11 ou menos (se faltam dados)
    jogadores_novos: list[str] # nomes dos nao-habituais no lineup actual
    titulares_fora: list[str]  # nomes dos habituais que nao jogam
    factor_rotacao: float      # 1.0 (sem rotacao) a 0.6 (rotacao total)

    def tem_rotacao(self, limiar: int = 3) -> bool:
        """True se faltam >= `limiar` habituais no onze."""
        if self.habituais_total < 6:
            return False  # dados insuficientes
        return (self.habituais_total - self.habituais_em_campo) >= limiar

    def mensagem_curta(self) -> str:
        """1 linha pronta para Telegram, ex: 'Porto sem 3 habituais titulares'."""
        n_fora = self.habituais_total - self.habituais_em_campo
        if n_fora <= 0:
            return ""
        return f"⚠ {self.nome_equipa} sem {n_fora} habituais titulares"


def titulares_habituais(
    team_id: int, n_jogos: int = 10,
) -> list[tuple[int, str, int]]:
    """Devolve top 11 jogadores por minutos totais nos ultimos N jogos.

    Cada entrada: (sofascore_player_id, nome_jogador, minutos_totais).
    Lista pode ter <11 entradas se a equipa tem poucos jogos.
    """
    try:
        from sqlalchemy import desc, func, select
        from apostas.utils.db import abrir_sessao
        from apostas.utils.schema import EstatisticaJogadorJogo
    except Exception:  # noqa: BLE001
        return []

    try:
        with abrir_sessao() as sess:
            # Primeiro apanha os n_jogos mais recentes da equipa
            jogos_recentes = sess.execute(
                select(EstatisticaJogadorJogo.sofascore_event_id)
                .where(EstatisticaJogadorJogo.sofascore_team_id == int(team_id))
                .group_by(EstatisticaJogadorJogo.sofascore_event_id)
                .order_by(desc(func.max(EstatisticaJogadorJogo.data_jogo)))
                .limit(n_jogos)
            ).scalars().all()
            if not jogos_recentes:
                return []
            # Agora soma minutos por jogador NESSES jogos
            rows = sess.execute(
                select(
                    EstatisticaJogadorJogo.sofascore_player_id,
                    EstatisticaJogadorJogo.nome_jogador,
                    func.sum(EstatisticaJogadorJogo.minutos).label("mins"),
                )
                .where(
                    EstatisticaJogadorJogo.sofascore_team_id == int(team_id),
                    EstatisticaJogadorJogo.sofascore_event_id.in_(jogos_recentes),
                )
                .group_by(
                    EstatisticaJogadorJogo.sofascore_player_id,
                    EstatisticaJogadorJogo.nome_jogador,
                )
                .order_by(desc("mins"))
                .limit(11)
            ).all()
    except Exception:  # noqa: BLE001
        return []
    return [(int(r[0]), str(r[1]), int(r[2])) for r in rows if r[2]]


def analisar_lineup_actual(
    team_id: int, nome_equipa: str,
    lineup_actual_player_ids: list[int],
    lineup_actual_nomes: dict[int, str] | None = None,
    n_jogos: int = 10,
) -> RelatorioRotacao | None:
    """Compara lineup actual com titulares habituais.

    `lineup_actual_player_ids`: ids dos 11 titulares deste jogo.
    `lineup_actual_nomes`: opcional {player_id: nome} para listar na msg.
    """
    habituais = titulares_habituais(team_id, n_jogos=n_jogos)
    if not habituais:
        return None

    habituais_ids = {pid for pid, _, _ in habituais}
    habituais_nomes = {pid: nome for pid, nome, _ in habituais}
    actuais_ids = set(lineup_actual_player_ids)

    em_campo = habituais_ids & actuais_ids
    fora_do_campo = habituais_ids - actuais_ids
    novos = actuais_ids - habituais_ids

    nomes_fora = [habituais_nomes[pid] for pid in fora_do_campo]
    nomes_novos = []
    if lineup_actual_nomes:
        nomes_novos = [lineup_actual_nomes.get(pid, f"#{pid}") for pid in novos]

    habituais_total = len(habituais_ids)
    habituais_em_campo = len(em_campo)
    # Factor: 0.6 (rotacao total — 0 habituais) a 1.0 (sem rotacao — 11 habituais)
    if habituais_total == 0:
        factor = 1.0
    else:
        rat_presentes = habituais_em_campo / habituais_total
        factor = 0.6 + 0.4 * rat_presentes

    return RelatorioRotacao(
        team_id=int(team_id), nome_equipa=nome_equipa,
        habituais_em_campo=habituais_em_campo,
        habituais_total=habituais_total,
        jogadores_novos=nomes_novos,
        titulares_fora=nomes_fora,
        factor_rotacao=round(factor, 2),
    )
