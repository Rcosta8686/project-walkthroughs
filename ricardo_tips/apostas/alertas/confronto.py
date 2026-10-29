"""Modulo Confronto — Fase 3 do Segundo Cerebro.

Pega em 2 FichaEquipa + estado do jogo e produz:
  - Projeccoes esperadas (combina baselines das 2 equipas)
  - Padroes cruzados ("A tem +40% remates 2T E B esta a perder -> boost")
  - Alertas (fadiga, derby, discrepancia de perfil)
  - Resumo narrativo em markdown para Telegram

Entrada: team_id_casa, team_id_fora, opcional estado live (score/minuto).
Saida: ResultadoConfronto com todos os campos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from apostas.relatorios.ficha_equipa import (
    FichaEquipa, gerar_ficha,
)
from apostas.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ResultadoConfronto:
    """Resultado completo de um confronto."""
    nome_casa: str
    nome_fora: str
    ficha_casa: FichaEquipa
    ficha_fora: FichaEquipa

    # Projeccoes agregadas (soma das baselines + contexto)
    remates_esperados: float
    sot_esperados: float
    cantos_esperados: float
    golos_esperados: float
    cards_am_esperados: float

    # Padroes cruzados (strings legiveis)
    padroes_cruzados: list[str] = field(default_factory=list)

    # Alertas de contexto (fadiga, carga)
    alertas: list[str] = field(default_factory=list)

    # Score / minuto (se chamado com estado live)
    minuto: Optional[int] = None
    score_casa: Optional[int] = None
    score_fora: Optional[int] = None


# ─────────── Agregacao projeccoes esperadas ──────────────────────────────

def _soma(a: float | None, b: float | None, default: float = 0.0) -> float:
    """Soma com fallback a default se None."""
    aa = a if a is not None else default
    bb = b if b is not None else default
    return aa + bb


def _esperados(casa: FichaEquipa, fora: FichaEquipa) -> dict:
    """Projeccoes esperadas por somar os baselines das 2 equipas."""
    bc = casa.baseline
    bf = fora.baseline
    return {
        "remates": _soma(bc.remates, bf.remates),
        "sot": _soma(bc.sot, bf.sot),
        "cantos": _soma(bc.cantos, bf.cantos),
        "golos": _soma(bc.golos_marcados, bf.golos_marcados),
        "cards_am": _soma(bc.cards_amarelos, bf.cards_amarelos),
    }


# ─────────── Padroes cruzados ─────────────────────────────────────────────

def _padroes_cruzados(
    casa: FichaEquipa, fora: FichaEquipa,
    minuto: int | None, score_casa: int | None, score_fora: int | None,
) -> list[str]:
    """Identifica padroes do confronto que sao mais fortes que as fichas isoladas."""
    out: list[str] = []
    pc, pf = casa.por_parte, fora.por_parte
    rc, rf = casa.por_resultado, fora.por_resultado
    bc, bf = casa.baseline, fora.baseline

    # 1. Ambas carregam 2T
    if (pc.diferenca_remates and "+" in pc.diferenca_remates
            and pf.diferenca_remates and "+" in pf.diferenca_remates):
        out.append(
            f"⚡ **Ambas carregam na 2ª parte** ({casa.nome} {pc.diferenca_remates}, "
            f"{fora.nome} {pf.diferenca_remates}) — esperar explosao SOT 2T"
        )

    # 2. Virar jogos cruzados — se casa vira muito E fora deixa virar
    if (rc.virar_jogos_pct is not None and rc.virar_jogos_pct >= 0.35
            and rf.n_L >= 2):
        out.append(
            f"🔄 {casa.nome} vira jogos {rc.virar_jogos_pct*100:.0f}% das vezes "
            f"— cuidado com 1X2 ao intervalo"
        )
    if (rf.virar_jogos_pct is not None and rf.virar_jogos_pct >= 0.35
            and rc.n_L >= 2):
        out.append(
            f"🔄 {fora.nome} vira jogos {rf.virar_jogos_pct*100:.0f}% das vezes"
        )

    # 3. Discrepancia disciplina (uma amarelada vs outra disciplinada)
    if (bc.cards_amarelos is not None and bf.cards_amarelos is not None):
        delta = bc.cards_amarelos - bf.cards_amarelos
        if abs(delta) >= 1.5:
            muito = casa.nome if delta > 0 else fora.nome
            pouco = fora.nome if delta > 0 else casa.nome
            out.append(
                f"🟡 Discrepancia disciplinar: {muito} ({max(bc.cards_amarelos, bf.cards_amarelos):.1f}/jogo) "
                f"vs {pouco} ({min(bc.cards_amarelos, bf.cards_amarelos):.1f}/jogo)"
            )

    # 4. Live only: estado do jogo + padroes de reaccao
    if minuto is not None and score_casa is not None and score_fora is not None:
        diff = score_casa - score_fora
        # Casa a perder E tem padrao "a perder = +remates"
        if diff <= -1 and rc.remates_L and bc.remates and rc.remates_L > bc.remates * 1.15:
            delta = (rc.remates_L / bc.remates - 1) * 100
            out.append(
                f"🔥 {casa.nome} a perder {score_casa}-{score_fora} + perfil "
                f"'a perder dispara +{delta:.0f}%' → esperar pressao"
            )
        if diff >= 1 and rf.remates_L and bf.remates and rf.remates_L > bf.remates * 1.15:
            delta = (rf.remates_L / bf.remates - 1) * 100
            out.append(
                f"🔥 {fora.nome} a perder {score_casa}-{score_fora} + perfil "
                f"'a perder dispara +{delta:.0f}%' → esperar pressao"
            )

    # 5. Discrepancia posse
    if (bc.posse_pct is not None and bf.posse_pct is not None):
        delta_posse = bc.posse_pct - bf.posse_pct
        if abs(delta_posse) >= 10:
            dominante = casa.nome if delta_posse > 0 else fora.nome
            out.append(
                f"🎯 {dominante} tem posse clara ({max(bc.posse_pct, bf.posse_pct):.0f}% "
                f"vs {min(bc.posse_pct, bf.posse_pct):.0f}%)"
            )

    # 6. Fase 7: casa/fora split cruzado
    cfc = casa.casa_fora
    cff = fora.casa_fora
    if cfc is not None and cff is not None:
        gmc = cfc.golos_marcados_casa
        gsf = cff.golos_sofridos_fora
        if gmc is not None and gsf is not None:
            esperado_casa = (gmc + gsf) / 2
            # Comparar com baseline agregada da casa
            if bc.golos_marcados is not None and esperado_casa > bc.golos_marcados * 1.20:
                out.append(
                    f"🏠 Em casa + adversario sofre fora: {casa.nome} esperado "
                    f"{esperado_casa:.1f} golos (vs {bc.golos_marcados:.1f} agregado)"
                )
        gmf = cff.golos_marcados_fora
        gsc = cfc.golos_sofridos_casa
        if gmf is not None and gsc is not None:
            esperado_fora = (gmf + gsc) / 2
            if bf.golos_marcados is not None and esperado_fora > bf.golos_marcados * 1.20:
                out.append(
                    f"✈ Visitante perigoso fora: {fora.nome} esperado "
                    f"{esperado_fora:.1f} golos (vs {bf.golos_marcados:.1f} agregado)"
                )

    return out


# ─────────── Alertas (fadiga / carga) ─────────────────────────────────────

def _alertas(casa: FichaEquipa, fora: FichaEquipa) -> list[str]:
    out: list[str] = []
    fc, ff = casa.fadiga, fora.fadiga

    # Fadiga grave: 2+ jogos nos ultimos 7 dias
    if fc.jogos_ultimos_7d >= 2:
        out.append(f"⏱ {casa.nome}: {fc.jogos_ultimos_7d} jogos nos ultimos 7d (fadiga)")
    if ff.jogos_ultimos_7d >= 2:
        out.append(f"⏱ {fora.nome}: {ff.jogos_ultimos_7d} jogos nos ultimos 7d (fadiga)")

    # Pouco descanso (<= 3 dias)
    if fc.dias_desde_ultimo_jogo is not None and fc.dias_desde_ultimo_jogo <= 3:
        out.append(
            f"🛌 {casa.nome} jogou ha {fc.dias_desde_ultimo_jogo} dias "
            f"(impacto na 2a parte)"
        )
    if ff.dias_desde_ultimo_jogo is not None and ff.dias_desde_ultimo_jogo <= 3:
        out.append(
            f"🛌 {fora.nome} jogou ha {ff.dias_desde_ultimo_jogo} dias "
            f"(impacto na 2a parte)"
        )

    return out


# ─────────── API principal ────────────────────────────────────────────────

def analisar_confronto(
    team_id_casa: int,
    team_id_fora: int,
    *,
    minuto: int | None = None,
    score_casa: int | None = None,
    score_fora: int | None = None,
    n_jogos_ficha: int = 20,
    liga_nome: str | None = None,
) -> ResultadoConfronto | None:
    """Produz analise completa de um confronto.

    Devolve None se qualquer uma das 2 equipas nao tem jogos minimos (<3).

    Quando `liga_nome` indica competicao com rotacao alta (Taca de
    Portugal, FA Cup, etc.), as projecoes agregadas sao reduzidas em
    30% e um alerta "⚠ Taça — rotacao provavel" e adicionado.
    """
    ficha_casa = gerar_ficha(team_id_casa, n_jogos=n_jogos_ficha)
    ficha_fora = gerar_ficha(team_id_fora, n_jogos=n_jogos_ficha)
    if ficha_casa is None or ficha_fora is None:
        log.info("Confronto %s vs %s: ficha incompleta.", team_id_casa, team_id_fora)
        return None

    esperados = _esperados(ficha_casa, ficha_fora)
    padroes = _padroes_cruzados(ficha_casa, ficha_fora, minuto, score_casa, score_fora)
    alertas = _alertas(ficha_casa, ficha_fora)

    # Rotacao de taca: reduz projecoes (menos cards — jogadores B podem
    # fazer mais faltas) e sinaliza no alerta.
    try:
        from apostas.alertas.rotacao import (
            factor_taca, is_competicao_rotacao_alta,
        )
        if is_competicao_rotacao_alta(liga_nome):
            f = factor_taca()
            esperados["remates"] = esperados["remates"] * f
            esperados["sot"] = esperados["sot"] * f
            esperados["cantos"] = esperados["cantos"] * f
            esperados["golos"] = esperados["golos"] * f
            # cards NAO reduzimos — jogadores menos coordenados podem
            # ate acumular mais amarelos.
            alertas.insert(
                0,
                "⚠ Taca — rotacao provavel: projecoes reduzidas 30%, "
                "perfil habitual pode nao aplicar.",
            )
    except Exception:  # noqa: BLE001
        pass

    return ResultadoConfronto(
        nome_casa=ficha_casa.nome,
        nome_fora=ficha_fora.nome,
        ficha_casa=ficha_casa,
        ficha_fora=ficha_fora,
        remates_esperados=esperados["remates"],
        sot_esperados=esperados["sot"],
        cantos_esperados=esperados["cantos"],
        golos_esperados=esperados["golos"],
        cards_am_esperados=esperados["cards_am"],
        padroes_cruzados=padroes,
        alertas=alertas,
        minuto=minuto,
        score_casa=score_casa,
        score_fora=score_fora,
    )


# ─────────── Formatacoes ──────────────────────────────────────────────────

def formatar_confronto_markdown(res: ResultadoConfronto) -> str:
    """Markdown rico para `docs/confrontos/` ou leitura no terminal."""
    lines: list[str] = []
    titulo = f"# {res.nome_casa} vs {res.nome_fora}"
    if res.score_casa is not None and res.minuto is not None:
        titulo += f"  ({res.score_casa}-{res.score_fora} · {res.minuto}')"
    lines.append(titulo)
    lines.append("")

    # Padroes
    if res.padroes_cruzados:
        lines.append("## 🧠 Padroes cruzados")
        lines.append("")
        for p in res.padroes_cruzados:
            lines.append(f"- {p}")
        lines.append("")

    if res.alertas:
        lines.append("## ⚠ Alertas")
        lines.append("")
        for a in res.alertas:
            lines.append(f"- {a}")
        lines.append("")

    # Projeccoes
    lines.append("## 📊 Projeccoes esperadas (soma dos baselines)")
    lines.append("")
    lines.append("| Mercado | Total esperado |")
    lines.append("|---|---|")
    lines.append(f"| Remates | {res.remates_esperados:.1f} |")
    lines.append(f"| Remates a baliza | {res.sot_esperados:.1f} |")
    lines.append(f"| Cantos | {res.cantos_esperados:.1f} |")
    lines.append(f"| Golos | {res.golos_esperados:.1f} |")
    lines.append(f"| Cartoes amarelos | {res.cards_am_esperados:.1f} |")
    lines.append("")

    # Resumo das fichas (so os cabecalhos)
    lines.append("## 🏠 Perfil Casa")
    lines.append("")
    for n in res.ficha_casa.notas:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("## ✈ Perfil Fora")
    lines.append("")
    for n in res.ficha_fora.notas:
        lines.append(f"- {n}")
    lines.append("")

    lines.append("---")
    lines.append(f"_Confronto gerado a partir de {res.ficha_casa.jogos_lidos} + "
                 f"{res.ficha_fora.jogos_lidos} jogos de historico._")
    return "\n".join(lines)


def formatar_confronto_telegram(res: ResultadoConfronto) -> str:
    """Mensagem Telegram compacta."""
    linhas = [f"🧠 *CONFRONTO* {res.nome_casa} vs {res.nome_fora}"]
    if res.score_casa is not None:
        linhas.append(f"({res.score_casa}-{res.score_fora} · {res.minuto}')")
    linhas.append("")
    linhas.append(
        f"Esperado: {res.remates_esperados:.0f} remates · "
        f"{res.sot_esperados:.0f} SOT · "
        f"{res.cantos_esperados:.0f} cantos · "
        f"{res.golos_esperados:.1f} golos"
    )
    if res.padroes_cruzados:
        linhas.append("")
        for p in res.padroes_cruzados[:3]:
            linhas.append(p)
    if res.alertas:
        linhas.append("")
        for a in res.alertas[:2]:
            linhas.append(a)
    return "\n".join(linhas)
