"""Confronto táctico jogador vs jogador (Fase 6 — ideia Ricardo 2026-10-18).

Dado 2 equipas em jogo, identifica:
  - Atacantes dribladores + defesas agressivos no mesmo lado do campo
    → amarelo provavel
  - Jogadores ja amarelados em jogos frequentes
    → risco segundo amarelo
  - Match-ups chave para o Telegram

Entrada: 11 inicial casa + 11 inicial fora (lista de player_ids).
Saida: lista de 'situacoes a monitorar'.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apostas.modelos.historico_jogadores import perfil_jogador
from apostas.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class MatchUp:
    """Confronto individual jogador vs jogador."""
    atacante_nome: str
    atacante_papel: str
    defesa_nome: str
    defesa_papel: str
    razao: str
    severidade: str  # "alta" | "media" | "baixa"


@dataclass
class ResultadoTactico:
    matchups: list[MatchUp] = field(default_factory=list)
    avisos_cartoes: list[str] = field(default_factory=list)
    perfis_destaque: list[str] = field(default_factory=list)


# Posicoes que se enfrentam no mesmo lado do campo
# atacante do flanco esquerdo vs defesa direito, etc.
_PARES_FLANCOS = [
    ({"LW"}, {"RB", "RWB"}),  # ponta esquerda vs lateral direito
    ({"RW"}, {"LB", "LWB"}),  # ponta direita vs lateral esquerdo
    ({"CAM", "ST"}, {"CB", "CDM"}),  # avancado central vs defesa/pivot
]


def _lado_opos(pos: str | None) -> set[str]:
    """Dado posicao de atacante, devolve posicoes defensivas que enfrenta."""
    if pos is None:
        return set()
    for atacantes, defesas in _PARES_FLANCOS:
        if pos in atacantes:
            return defesas
    return set()


def analisar_tactico(
    titulares_casa_ids: list[int],
    titulares_fora_ids: list[int],
    *,
    event_id: int | None = None,
) -> ResultadoTactico:
    """Produz analise tactica a partir dos 2 onzes iniciais.

    Para cada par (atacante_lado_X, defesa_lado_X_oposto), verifica
    perfis e emite matchup se ha divergencia significativa.
    """
    perfis_casa = {
        pid: perfil_jogador(pid, n=10)
        for pid in titulares_casa_ids
    }
    perfis_fora = {
        pid: perfil_jogador(pid, n=10)
        for pid in titulares_fora_ids
    }

    out = ResultadoTactico()

    # Pre-filtra apenas jogadores com perfil disponivel
    casa_disp = {pid: p for pid, p in perfis_casa.items() if p is not None}
    fora_disp = {pid: p for pid, p in perfis_fora.items() if p is not None}

    # Match-ups: para cada atacante da casa, procurar defesa oposto do fora
    _emitir_matchups(casa_disp, fora_disp, out)
    _emitir_matchups(fora_disp, casa_disp, out)

    # Perfis de destaque (jogadores com tendencia_amarelo alta)
    for pid, p in {**casa_disp, **fora_disp}.items():
        if p["tendencia_amarelo"] >= 0.35:  # ~1 amarelo cada 3 jogos
            out.perfis_destaque.append(
                f"🟨 {p['nome']} ({p['papel']}): tendencia amarelo "
                f"{p['tendencia_amarelo']:.2f}/90 · "
                f"agressividade {p['agressividade']:.1f} faltas/90"
            )
        if p["forma"] and p["forma"] >= 7.5:
            out.perfis_destaque.append(
                f"🔥 {p['nome']} ({p['papel']}): forma alta "
                f"({p['forma']:.1f}/10 ultimos 5 jogos)"
            )

    return out


def _emitir_matchups(
    perfis_atacantes: dict,
    perfis_defesas: dict,
    out: ResultadoTactico,
) -> None:
    """Emite matchups driblador vs defesa agressivo no mesmo lado."""
    for pid_at, p_at in perfis_atacantes.items():
        posicoes_opostas = _lado_opos(p_at.get("posicao"))
        if not posicoes_opostas:
            continue
        # Procura defesas opostos com perfil agressivo
        for pid_def, p_def in perfis_defesas.items():
            if p_def.get("posicao") not in posicoes_opostas:
                continue
            # Criterios para matchup "amarelo provavel":
            #  - atacante com drible_pct bom (>=0.55) OU produtividade alta
            #  - defesa agressivo (agressividade >= 1.8/90 OU tendencia_amarelo >= 0.3)
            drible_bom = (p_at.get("drible_pct") or 0) >= 0.55
            produtivo = p_at["produtividade_90"] >= 0.5
            defesa_agressivo = p_def["agressividade"] >= 1.8
            defesa_amarelado = p_def["tendencia_amarelo"] >= 0.30

            if (drible_bom or produtivo) and (defesa_agressivo or defesa_amarelado):
                razoes = []
                if drible_bom:
                    razoes.append(f"driblador ({p_at['drible_pct']*100:.0f}% sucesso)")
                if produtivo:
                    razoes.append(f"produtivo ({p_at['produtividade_90']:.2f} g+a/90)")
                if defesa_agressivo:
                    razoes.append(f"defesa agressivo ({p_def['agressividade']:.1f} faltas/90)")
                if defesa_amarelado:
                    razoes.append(f"ja viu {p_def['tendencia_amarelo']:.2f} amarelos/90")
                razao = " · ".join(razoes)
                sev = "alta" if (drible_bom and defesa_agressivo) else "media"
                out.matchups.append(MatchUp(
                    atacante_nome=p_at["nome"],
                    atacante_papel=p_at["papel"],
                    defesa_nome=p_def["nome"],
                    defesa_papel=p_def["papel"],
                    razao=razao,
                    severidade=sev,
                ))


def formatar_telegram(res: ResultadoTactico, max_matchups: int = 3) -> str:
    """Mensagem compacta para Telegram."""
    if not res.matchups and not res.avisos_cartoes and not res.perfis_destaque:
        return ""
    linhas: list[str] = ["🎯 *CONFRONTO TACTICO*"]
    if res.matchups:
        linhas.append("")
        linhas.append("*Match-ups chave:*")
        for mu in res.matchups[:max_matchups]:
            emoji = "🔥" if mu.severidade == "alta" else "⚡"
            linhas.append(
                f"{emoji} {mu.atacante_nome} vs {mu.defesa_nome}"
            )
            linhas.append(f"   _{mu.razao}_")
    if res.perfis_destaque:
        linhas.append("")
        for p in res.perfis_destaque[:5]:
            linhas.append(p)
    if res.avisos_cartoes:
        linhas.append("")
        for a in res.avisos_cartoes:
            linhas.append(a)
    return "\n".join(linhas)
