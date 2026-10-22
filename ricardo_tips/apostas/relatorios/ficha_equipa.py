"""Ficha rica por equipa (Fase 2 do Segundo Cerebro).

Dado um sofascore_team_id, le EstatisticaEquipaJogo e produz:
  - dict com todas as seccoes (baseline, por parte, por resultado,
    padroes, fadiga, notas)
  - markdown Obsidian-friendly para `docs/equipas/{nome}.md`

Nao faz chamadas HTTP — tudo vem da tabela persistente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from apostas.modelos import historico_equipas
from apostas.utils.logger import get_logger
from apostas.utils.schema import EstatisticaEquipaJogo
from apostas.utils.tempo import agora_utc

log = get_logger(__name__)


# ─────────── Modelos ─────────────────────────────────────────────────────

@dataclass
class SeccaoBaseline:
    """Medias por 90' nos n jogos."""
    n_jogos: int
    golos_marcados: float | None
    golos_sofridos: float | None
    remates: float | None
    sot: float | None
    cantos: float | None
    cards_amarelos: float | None
    fouls: float | None
    posse_pct: float | None


@dataclass
class SeccaoPorParte:
    """Medias split 1T vs 2T."""
    remates_1t: float | None
    remates_2t: float | None
    sot_1t: float | None
    sot_2t: float | None
    cantos_1t: float | None
    cantos_2t: float | None
    cards_am_1t: float | None
    cards_am_2t: float | None
    fouls_1t: float | None
    fouls_2t: float | None
    diferenca_remates: str | None  # ex: "+40% 2T"
    diferenca_cantos: str | None


@dataclass
class SeccaoPorResultado:
    """Baseline nos subsets W / D / L."""
    n_W: int
    n_D: int
    n_L: int
    remates_W: float | None
    remates_D: float | None
    remates_L: float | None
    sot_W: float | None
    sot_D: float | None
    sot_L: float | None
    virar_jogos_pct: float | None  # perdia 1T e ganhou/empatou final


@dataclass
class SeccaoFadiga:
    """Carga semanal."""
    jogos_ultimos_7d: int
    jogos_ultimos_14d: int
    dias_desde_ultimo_jogo: int | None
    # Fase 9: minutos cumulados dos titulares mais usados nos ultimos N
    # jogos. Preenchido quando ha dados de EstatisticaJogadorJogo.
    minutos_top_titulares_7d: float | None = None  # media dos top 5
    minutos_top_titulares_14d: float | None = None


@dataclass
class SeccaoCasaFora:
    """Fase 7: split de desempenho em casa vs fora.

    Permite ao confronto saber que uma equipa que marca 2.1 golos/jogo no
    agregado marca 2.7 em casa e 1.5 fora — grande diferenca que ficha
    agregada esconde.
    """
    n_casa: int
    n_fora: int
    # Agregados chave em cada contexto. None se n<3.
    golos_marcados_casa: float | None
    golos_marcados_fora: float | None
    golos_sofridos_casa: float | None
    golos_sofridos_fora: float | None
    remates_casa: float | None
    remates_fora: float | None
    cantos_casa: float | None
    cantos_fora: float | None
    cards_am_casa: float | None
    cards_am_fora: float | None
    # Record W-D-L em cada contexto
    vitorias_casa: int
    empates_casa: int
    derrotas_casa: int
    vitorias_fora: int
    empates_fora: int
    derrotas_fora: int


@dataclass
class FichaEquipa:
    team_id: int
    nome: str
    liga: str | None
    jogos_lidos: int
    data_actualizacao: datetime
    baseline: SeccaoBaseline
    por_parte: SeccaoPorParte
    por_resultado: SeccaoPorResultado
    fadiga: SeccaoFadiga
    # Fase 7: split casa/fora. Pode ser None se n<3 em cada lado.
    casa_fora: SeccaoCasaFora | None = None
    notas: list[str] = field(default_factory=list)


# ─────────── Agregadores ─────────────────────────────────────────────────

def _mn(valores: Iterable[float | None]) -> float | None:
    vs = [v for v in valores if v is not None]
    return sum(vs) / len(vs) if vs else None


def _pct_diff(v1: float | None, v2: float | None) -> str | None:
    """Devolve '+40% 2T' se v2 > v1 em >=15%, '-15% 2T' se v2 < v1, else None."""
    if v1 is None or v2 is None or v1 <= 0:
        return None
    delta = (v2 / v1 - 1) * 100
    if abs(delta) < 15:
        return None
    sinal = "+" if delta > 0 else ""
    return f"{sinal}{delta:.0f}% 2T"


def _gerar_notas(ficha_base: dict, jogos: list[EstatisticaEquipaJogo]) -> list[str]:
    """Padroes nao-numericos: strings legiveis que ajudam o user."""
    notas: list[str] = []
    pp = ficha_base["por_parte"]
    pr = ficha_base["por_resultado"]
    base = ficha_base["baseline"]

    # 1. 2T vs 1T remates (padrao forte)
    if pp.diferenca_remates and "+" in pp.diferenca_remates:
        notas.append(f"⚡ Carrega na 2a parte: {pp.diferenca_remates} remates")
    elif pp.diferenca_remates and "-" in pp.diferenca_remates:
        notas.append(f"🐢 Abranda na 2a parte: {pp.diferenca_remates} remates")

    # 2. Cantos 2T
    if pp.diferenca_cantos and "+" in pp.diferenca_cantos:
        notas.append(f"🚩 Mais cantos 2a parte: {pp.diferenca_cantos}")

    # 3. CARTOES por parte (padrao oculto importante — ex: Roma 1T=0.4, 2T=1.4)
    if pp.cards_am_1t is not None and pp.cards_am_2t is not None and pp.cards_am_1t > 0:
        delta_cards = (pp.cards_am_2t / pp.cards_am_1t - 1) * 100
        if delta_cards >= 50:
            notas.append(
                f"🟨 Amarelos explodem 2a parte: "
                f"{pp.cards_am_1t:.1f} 1T → {pp.cards_am_2t:.1f} 2T "
                f"(+{delta_cards:.0f}%) — bom para 'proximo cartao >45min'"
            )
        elif delta_cards <= -30:
            notas.append(
                f"🟨 Mais amarelos 1a parte ({pp.cards_am_1t:.1f} vs {pp.cards_am_2t:.1f}) "
                f"— incomum, verificar arbitro"
            )

    # 4. FALTAS por parte
    if pp.fouls_1t is not None and pp.fouls_2t is not None and pp.fouls_1t > 0:
        delta_fouls = (pp.fouls_2t / pp.fouls_1t - 1) * 100
        if abs(delta_fouls) >= 20:
            sinal = "+" if delta_fouls > 0 else ""
            notas.append(
                f"⚠ Faltas {sinal}{delta_fouls:.0f}% 2a parte "
                f"({pp.fouls_1t:.1f} → {pp.fouls_2t:.1f})"
            )

    # 5. Virar jogos
    if pr.virar_jogos_pct is not None and pr.virar_jogos_pct >= 0.35:
        notas.append(f"🔄 Vira jogos frequentemente: {pr.virar_jogos_pct*100:.0f}%")

    # 6. A perder = +remates (desespero)
    if (pr.remates_L and base.remates
            and pr.remates_L > base.remates * 1.15):
        delta = (pr.remates_L / base.remates - 1) * 100
        notas.append(f"🔥 A perder, dispara: +{delta:.0f}% remates")

    # 7. A perder = -remates (equipa dominante — ex: Roma, City)
    #    Quando perde e porque o adversario domina. Nao vale apostar overs
    #    quando ela esta a perder.
    if (pr.remates_L and base.remates and pr.n_L >= 2
            and pr.remates_L < base.remates * 0.90):
        delta = (1 - pr.remates_L / base.remates) * 100
        notas.append(
            f"👑 A perder, remata MENOS: -{delta:.0f}% remates "
            f"— equipa dominante, perde quando NAO joga ela"
        )

    # 8. A ganhar = -remates (acalma)
    if (pr.remates_W and base.remates
            and pr.remates_W < base.remates * 0.90):
        delta = (1 - pr.remates_W / base.remates) * 100
        notas.append(f"🛡 A ganhar, acalma: -{delta:.0f}% remates")

    # 9. A ganhar = MAIS remates (equipa que amassa quando domina)
    if (pr.remates_W and base.remates
            and pr.remates_W > base.remates * 1.10):
        delta = (pr.remates_W / base.remates - 1) * 100
        notas.append(f"⚔ A ganhar, amassa: +{delta:.0f}% remates")

    # 10. Disciplina baseline
    if base.cards_amarelos is not None and base.cards_amarelos >= 3.0:
        notas.append(f"🟡 Equipa amarelada: {base.cards_amarelos:.1f} cartoes/jogo")
    elif base.cards_amarelos is not None and base.cards_amarelos <= 1.5:
        notas.append(f"🟢 Equipa disciplinada: {base.cards_amarelos:.1f} cartoes/jogo")

    # 11. Posse muito alta ou muito baixa (perfil)
    if base.posse_pct is not None:
        if base.posse_pct >= 60:
            notas.append(f"🎯 Dominadora: {base.posse_pct:.0f}% posse media")
        elif base.posse_pct <= 42:
            notas.append(f"🏃 Equipa de contra-ataque: {base.posse_pct:.0f}% posse media")

    # 12. Fadiga aviso
    f = ficha_base["fadiga"]
    if f.jogos_ultimos_7d >= 2:
        notas.append(f"⏱ Carga alta: {f.jogos_ultimos_7d} jogos nos ultimos 7 dias")

    # 13. Fase 9: carga titulares (minutos cumulados)
    if f.minutos_top_titulares_7d is not None and f.minutos_top_titulares_7d >= 180:
        notas.append(
            f"🥵 Titulares TOP5 com {f.minutos_top_titulares_7d:.0f} min nos ultimos "
            f"7 dias (desgaste)"
        )

    # 14. Fase 7: diferenca casa/fora
    cf = ficha_base.get("casa_fora")
    if cf is not None and cf.golos_marcados_casa is not None and cf.golos_marcados_fora is not None:
        delta_g = cf.golos_marcados_casa - cf.golos_marcados_fora
        if delta_g >= 0.5:
            notas.append(
                f"🏠 Muito melhor em casa: {cf.golos_marcados_casa:.1f} vs "
                f"{cf.golos_marcados_fora:.1f} golos marcados"
            )
        elif delta_g <= -0.3:
            notas.append(
                f"✈ Melhor fora: {cf.golos_marcados_fora:.1f} vs "
                f"{cf.golos_marcados_casa:.1f} golos marcados"
            )
    if cf is not None:
        # Record muito desigual
        if cf.n_casa >= 4 and cf.vitorias_casa >= cf.n_casa * 0.7:
            notas.append(
                f"🏠 Fortaleza em casa: {cf.vitorias_casa}W-"
                f"{cf.empates_casa}D-{cf.derrotas_casa}L"
            )
        if cf.n_fora >= 4 and cf.derrotas_fora >= cf.n_fora * 0.6:
            notas.append(
                f"✈ Fraca fora: {cf.vitorias_fora}W-"
                f"{cf.empates_fora}D-{cf.derrotas_fora}L"
            )

    return notas


def gerar_ficha(team_id: int, n_jogos: int = 20) -> FichaEquipa | None:
    """Produz FichaEquipa a partir das ultimas n linhas da tabela.

    Devolve None se nao houver jogos minimos (<3).
    """
    jogos = historico_equipas.stats_recentes_equipa(team_id, n_jogos)
    if len(jogos) < 3:
        return None

    nome = jogos[0].nome_equipa
    liga = jogos[0].liga

    # Baseline
    baseline = SeccaoBaseline(
        n_jogos=len(jogos),
        golos_marcados=_mn([j.golos_marcados for j in jogos]),
        golos_sofridos=_mn([j.golos_sofridos for j in jogos]),
        remates=_mn([j.remates for j in jogos]),
        sot=_mn([j.sot for j in jogos]),
        cantos=_mn([j.cantos for j in jogos]),
        cards_amarelos=_mn([j.cards_amarelos for j in jogos]),
        fouls=_mn([j.fouls for j in jogos]),
        posse_pct=_mn([j.posse_pct for j in jogos]),
    )

    # Por parte
    r1 = _mn([j.remates_1t for j in jogos])
    r2 = _mn([j.remates_2t for j in jogos])
    c1 = _mn([j.cantos_1t for j in jogos])
    c2 = _mn([j.cantos_2t for j in jogos])
    por_parte = SeccaoPorParte(
        remates_1t=r1, remates_2t=r2,
        sot_1t=_mn([j.sot_1t for j in jogos]),
        sot_2t=_mn([j.sot_2t for j in jogos]),
        cantos_1t=c1, cantos_2t=c2,
        cards_am_1t=_mn([j.cards_amarelos_1t for j in jogos]),
        cards_am_2t=_mn([j.cards_amarelos_2t for j in jogos]),
        fouls_1t=_mn([j.fouls_1t for j in jogos]),
        fouls_2t=_mn([j.fouls_2t for j in jogos]),
        diferenca_remates=_pct_diff(r1, r2),
        diferenca_cantos=_pct_diff(c1, c2),
    )

    # Por resultado
    ganhou = [j for j in jogos if j.resultado == "W"]
    empatou = [j for j in jogos if j.resultado == "D"]
    perdeu = [j for j in jogos if j.resultado == "L"]

    virou = sum(
        1 for j in jogos
        if j.resultado in ("W", "D") and j.resultado_1t == "L"
    )
    denom = sum(1 for j in jogos if j.resultado_1t == "L")
    virar_pct = (virou / denom) if denom > 0 else None

    por_resultado = SeccaoPorResultado(
        n_W=len(ganhou), n_D=len(empatou), n_L=len(perdeu),
        remates_W=_mn([j.remates for j in ganhou]),
        remates_D=_mn([j.remates for j in empatou]),
        remates_L=_mn([j.remates for j in perdeu]),
        sot_W=_mn([j.sot for j in ganhou]),
        sot_D=_mn([j.sot for j in empatou]),
        sot_L=_mn([j.sot for j in perdeu]),
        virar_jogos_pct=virar_pct,
    )

    # Fadiga
    hoje = agora_utc()
    jogos_7d = sum(1 for j in jogos if (hoje - j.data_jogo).days <= 7)
    jogos_14d = sum(1 for j in jogos if (hoje - j.data_jogo).days <= 14)
    dias_desde = None
    if jogos:
        delta = hoje - jogos[0].data_jogo
        dias_desde = delta.days

    # Fase 9: carga titulares (minutos cumulados nos ultimos jogos).
    # Nao bloqueante — se falhar fica None e segue.
    mins_7d, mins_14d = _minutos_titulares_recentes(team_id, hoje)

    fadiga = SeccaoFadiga(
        jogos_ultimos_7d=jogos_7d,
        jogos_ultimos_14d=jogos_14d,
        dias_desde_ultimo_jogo=dias_desde,
        minutos_top_titulares_7d=mins_7d,
        minutos_top_titulares_14d=mins_14d,
    )

    # Fase 7: casa/fora split
    casa_jogos = [j for j in jogos if j.era_casa]
    fora_jogos = [j for j in jogos if not j.era_casa]
    casa_fora = SeccaoCasaFora(
        n_casa=len(casa_jogos),
        n_fora=len(fora_jogos),
        golos_marcados_casa=_mn([j.golos_marcados for j in casa_jogos]) if len(casa_jogos) >= 3 else None,
        golos_marcados_fora=_mn([j.golos_marcados for j in fora_jogos]) if len(fora_jogos) >= 3 else None,
        golos_sofridos_casa=_mn([j.golos_sofridos for j in casa_jogos]) if len(casa_jogos) >= 3 else None,
        golos_sofridos_fora=_mn([j.golos_sofridos for j in fora_jogos]) if len(fora_jogos) >= 3 else None,
        remates_casa=_mn([j.remates for j in casa_jogos]) if len(casa_jogos) >= 3 else None,
        remates_fora=_mn([j.remates for j in fora_jogos]) if len(fora_jogos) >= 3 else None,
        cantos_casa=_mn([j.cantos for j in casa_jogos]) if len(casa_jogos) >= 3 else None,
        cantos_fora=_mn([j.cantos for j in fora_jogos]) if len(fora_jogos) >= 3 else None,
        cards_am_casa=_mn([j.cards_amarelos for j in casa_jogos]) if len(casa_jogos) >= 3 else None,
        cards_am_fora=_mn([j.cards_amarelos for j in fora_jogos]) if len(fora_jogos) >= 3 else None,
        vitorias_casa=sum(1 for j in casa_jogos if j.resultado == "W"),
        empates_casa=sum(1 for j in casa_jogos if j.resultado == "D"),
        derrotas_casa=sum(1 for j in casa_jogos if j.resultado == "L"),
        vitorias_fora=sum(1 for j in fora_jogos if j.resultado == "W"),
        empates_fora=sum(1 for j in fora_jogos if j.resultado == "D"),
        derrotas_fora=sum(1 for j in fora_jogos if j.resultado == "L"),
    )

    base_dict = {
        "baseline": baseline, "por_parte": por_parte,
        "por_resultado": por_resultado, "fadiga": fadiga,
        "casa_fora": casa_fora,
    }
    notas = _gerar_notas(base_dict, jogos)

    return FichaEquipa(
        team_id=team_id,
        nome=nome,
        liga=liga,
        jogos_lidos=len(jogos),
        data_actualizacao=hoje,
        baseline=baseline,
        por_parte=por_parte,
        por_resultado=por_resultado,
        fadiga=fadiga,
        casa_fora=casa_fora,
        notas=notas,
    )


def _minutos_titulares_recentes(
    team_id: int, hoje: datetime,
) -> tuple[float | None, float | None]:
    """Fase 9: carga semanal rica.

    Para cada jogador que foi titular nos ultimos N jogos da equipa,
    soma minutos jogados nos ultimos 7 e 14 dias. Devolve media dos top
    5 jogadores por minutos (os mais usados). None se nao houver dados.

    Lazy import — EstatisticaJogadorJogo pode nao ter linhas em todas
    as sessoes de teste.
    """
    try:
        from sqlalchemy import func, select
        from apostas.utils.db import abrir_sessao
        from apostas.utils.schema import (
            EstatisticaJogadorJogo as EJ, EstatisticaEquipaJogo as EE,
        )
    except Exception:  # noqa: BLE001
        return None, None

    def _top5_media_na_janela(dias: int) -> float | None:
        limiar = hoje - timedelta(days=dias)
        try:
            with abrir_sessao() as s:
                # Jogos recentes desta equipa
                event_ids = s.scalars(
                    select(EE.sofascore_event_id).where(
                        EE.sofascore_team_id == team_id,
                        EE.data_jogo >= limiar,
                    )
                ).all()
                if not event_ids:
                    return None
                # Minutos por jogador em cada um desses jogos
                rows = s.execute(
                    select(EJ.sofascore_player_id, func.sum(EJ.minutos))
                    .where(
                        EJ.sofascore_team_id == team_id,
                        EJ.sofascore_event_id.in_(event_ids),
                    )
                    .group_by(EJ.sofascore_player_id)
                ).all()
        except Exception:  # noqa: BLE001
            return None
        totais = sorted((m for (_pid, m) in rows if m is not None), reverse=True)
        if not totais:
            return None
        top5 = totais[:5]
        return sum(top5) / len(top5)

    return _top5_media_na_janela(7), _top5_media_na_janela(14)


# ─────────── Renderer Markdown (Obsidian-friendly) ────────────────────────

def _fmt(v: float | None, suf: str = "") -> str:
    return f"{v:.1f}{suf}" if v is not None else "—"


def ficha_markdown(ficha: FichaEquipa) -> str:
    """Produz markdown pronto para `docs/equipas/{nome}.md`."""
    lines: list[str] = []
    lines.append(f"# {ficha.nome}")
    lines.append("")
    lines.append(f"_Liga: **{ficha.liga or '—'}**  ·  Jogos lidos: "
                 f"{ficha.jogos_lidos}  ·  Actualizado: "
                 f"{ficha.data_actualizacao:%Y-%m-%d %H:%M}_")
    lines.append("")

    # Notas (destaque — ponto-chave do perfil)
    if ficha.notas:
        lines.append("## Padroes-chave")
        lines.append("")
        for n in ficha.notas:
            lines.append(f"- {n}")
        lines.append("")

    # Baseline
    b = ficha.baseline
    lines.append("## Baseline (medias por jogo)")
    lines.append("")
    lines.append("| Metrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Golos marcados | {_fmt(b.golos_marcados)} |")
    lines.append(f"| Golos sofridos | {_fmt(b.golos_sofridos)} |")
    lines.append(f"| Remates | {_fmt(b.remates)} |")
    lines.append(f"| Remates a baliza | {_fmt(b.sot)} |")
    lines.append(f"| Cantos | {_fmt(b.cantos)} |")
    lines.append(f"| Cartoes amarelos | {_fmt(b.cards_amarelos)} |")
    lines.append(f"| Faltas | {_fmt(b.fouls)} |")
    lines.append(f"| Posse de bola | {_fmt(b.posse_pct, '%')} |")
    lines.append("")

    # Por parte
    pp = ficha.por_parte
    lines.append("## Por parte (1T vs 2T)")
    lines.append("")
    lines.append("| Metrica | 1a parte | 2a parte | Diferenca |")
    lines.append("|---|---|---|---|")
    lines.append(f"| Remates   | {_fmt(pp.remates_1t)} | {_fmt(pp.remates_2t)} | "
                 f"{pp.diferenca_remates or '—'} |")
    lines.append(f"| SOT       | {_fmt(pp.sot_1t)} | {_fmt(pp.sot_2t)} | — |")
    lines.append(f"| Cantos    | {_fmt(pp.cantos_1t)} | {_fmt(pp.cantos_2t)} | "
                 f"{pp.diferenca_cantos or '—'} |")
    lines.append(f"| Amarelos  | {_fmt(pp.cards_am_1t)} | {_fmt(pp.cards_am_2t)} | — |")
    lines.append("")

    # Por resultado
    pr = ficha.por_resultado
    lines.append("## Por resultado (W / D / L)")
    lines.append("")
    lines.append(f"- Vitorias: **{pr.n_W}**  ·  Empates: **{pr.n_D}**  ·  Derrotas: **{pr.n_L}**")
    lines.append("")
    lines.append("| Metrica | A ganhar (W) | Empate (D) | A perder (L) |")
    lines.append("|---|---|---|---|")
    lines.append(f"| Remates   | {_fmt(pr.remates_W)} | {_fmt(pr.remates_D)} | {_fmt(pr.remates_L)} |")
    lines.append(f"| SOT       | {_fmt(pr.sot_W)} | {_fmt(pr.sot_D)} | {_fmt(pr.sot_L)} |")
    lines.append("")
    if pr.virar_jogos_pct is not None:
        lines.append(f"- Virar jogos (perdia 1T → ganhou/empatou): "
                     f"**{pr.virar_jogos_pct*100:.0f}%**")
        lines.append("")

    # Casa / fora (Fase 7)
    cf = ficha.casa_fora
    if cf is not None and (cf.n_casa > 0 or cf.n_fora > 0):
        lines.append("## Casa vs Fora")
        lines.append("")
        lines.append(
            f"- Casa: {cf.n_casa} jogos  "
            f"({cf.vitorias_casa}W-{cf.empates_casa}D-{cf.derrotas_casa}L)"
        )
        lines.append(
            f"- Fora: {cf.n_fora} jogos  "
            f"({cf.vitorias_fora}W-{cf.empates_fora}D-{cf.derrotas_fora}L)"
        )
        if (cf.golos_marcados_casa is not None
                and cf.golos_marcados_fora is not None):
            lines.append("")
            lines.append("| Metrica | Casa | Fora |")
            lines.append("|---|---|---|")
            lines.append(
                f"| Golos marcados | {_fmt(cf.golos_marcados_casa)} | "
                f"{_fmt(cf.golos_marcados_fora)} |"
            )
            lines.append(
                f"| Golos sofridos | {_fmt(cf.golos_sofridos_casa)} | "
                f"{_fmt(cf.golos_sofridos_fora)} |"
            )
            lines.append(
                f"| Remates | {_fmt(cf.remates_casa)} | "
                f"{_fmt(cf.remates_fora)} |"
            )
            lines.append(
                f"| Cantos | {_fmt(cf.cantos_casa)} | "
                f"{_fmt(cf.cantos_fora)} |"
            )
            lines.append(
                f"| Amarelos | {_fmt(cf.cards_am_casa)} | "
                f"{_fmt(cf.cards_am_fora)} |"
            )
        lines.append("")

    # Fadiga
    f = ficha.fadiga
    lines.append("## Fadiga e carga")
    lines.append("")
    lines.append(f"- Jogos ultimos 7 dias: **{f.jogos_ultimos_7d}**")
    lines.append(f"- Jogos ultimos 14 dias: **{f.jogos_ultimos_14d}**")
    if f.dias_desde_ultimo_jogo is not None:
        if f.dias_desde_ultimo_jogo == 0:
            lines.append(f"- Ultimo jogo: **hoje**")
        elif f.dias_desde_ultimo_jogo == 1:
            lines.append(f"- Ultimo jogo: **ontem**")
        else:
            lines.append(f"- Dias desde ultimo jogo: **{f.dias_desde_ultimo_jogo}**")
    if f.minutos_top_titulares_7d is not None:
        lines.append(
            f"- Top5 titulares: **{f.minutos_top_titulares_7d:.0f} min** 7d"
            + (f" · {f.minutos_top_titulares_14d:.0f} min 14d"
               if f.minutos_top_titulares_14d is not None else "")
        )
    lines.append("")

    lines.append("---")
    lines.append(f"_Ficha gerada automaticamente a partir de "
                 f"`EstatisticaEquipaJogo`._")

    return "\n".join(lines)
