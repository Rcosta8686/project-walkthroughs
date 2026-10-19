"""Formatação de mensagens para o Telegram.

As mensagens usam markdown-light (apenas negrito e código inline) para
máxima compatibilidade com clientes antigos.
"""

from __future__ import annotations

from datetime import datetime

from apostas.utils.schema import Equipa, Jogo, Liga, Sugestao


def sugestao_nova(
    sugestao: Sugestao, jogo: Jogo, liga: Liga, casa: Equipa, fora: Equipa
) -> str:
    """Alerta inicial quando uma nova sugestão é identificada."""
    linhas = [
        "⚡ *Sugestão nova* ",
        "",
        f"🏆 {liga.nome}",
        f"⚽ {casa.nome} vs {fora.nome}",
        f"🕒 {_fmt_hora(jogo.data_utc)}",
        "",
        f"Mercado: *{sugestao.mercado.capitalize()} {_linha(sugestao.linha)} {sugestao.lado}*",
        f"Prob modelo: *{sugestao.prob_modelo * 100:.1f}%*",
        f"Odd referência ({_casa_ref()}): *{sugestao.odd_referencia:.2f}*",
        f"EV estimado: *{sugestao.ev * 100:+.2f}%*",
        "",
        "Para apostar na 22bet, confirma a odd corrente e regista:",
        f"`/registar {sugestao.id} <stake> <odd_executada>`",
    ]
    return "\n".join(linhas)


def resumo_dia(sugestoes_jogos: list[tuple[Sugestao, Jogo, Liga, Equipa, Equipa]]) -> str:
    if not sugestoes_jogos:
        return "📭 Sem sugestões para hoje."
    linhas = ["📋 *Sugestões de hoje*", ""]
    for s, j, lg, c, f in sugestoes_jogos:
        linhas.append(
            f"• {c.nome} vs {f.nome} ({_fmt_hora(j.data_utc, hora=True)}) · "
            f"{s.mercado} {_linha(s.linha)} {s.lado} · EV {s.ev * 100:+.2f}% · "
            f"`#{s.id}`"
        )
    return "\n".join(linhas)


def estatisticas_mes(
    inicio: datetime,
    n_apostas: int,
    n_ganhas: int,
    stake_total: float,
    lucro: float,
) -> str:
    if n_apostas == 0:
        return f"📊 Sem apostas registadas em {inicio.strftime('%B %Y')}."
    hit = n_ganhas / n_apostas * 100
    roi = lucro / stake_total * 100 if stake_total else 0.0
    linhas = [
        f"📊 *Estatísticas · {inicio.strftime('%B %Y')}*",
        "",
        f"Apostas: *{n_apostas}* ({n_ganhas} ganhas, {hit:.1f}%)",
        f"Stake total: *{stake_total:.2f}* un.",
        f"Lucro: *{lucro:+.2f}* un.",
        f"ROI: *{roi:+.2f}%*",
    ]
    return "\n".join(linhas)


def registo_confirmado(sugestao_id: int, stake: float, odd: float, retorno_esperado: float) -> str:
    return (
        f"✅ Aposta registada (sugestão #{sugestao_id})\n"
        f"Stake: {stake:.2f} · Odd: {odd:.2f}\n"
        f"Retorno esperado se ganha: *{retorno_esperado:.2f}* unidades"
    )


def apostei_confirmado(
    alert_id: int, home: str, away: str, aposta: str,
    stake: float, odd_executada: float, odd_recomendada: float | None,
    retorno_esperado: float, clv_pct: float | None,
) -> str:
    linhas = [
        f"✅ Aposta live registada (#{alert_id})",
        f"⚽ {home} vs {away}",
        f"🎯 {aposta}",
        f"💰 Stake: {stake:.2f}  ·  Odd: {odd_executada:.2f}",
    ]
    if odd_recomendada is not None and odd_recomendada > 0:
        delta = (odd_executada / odd_recomendada - 1) * 100
        sinal = "+" if delta >= 0 else ""
        linhas.append(
            f"📏 vs recomendada {odd_recomendada:.2f}  →  {sinal}{delta:.1f}%"
        )
    if clv_pct is not None:
        sinal = "+" if clv_pct >= 0 else ""
        linhas.append(f"📈 CLV estimado: {sinal}{clv_pct:.1f}%")
    linhas.append(
        f"💵 Retorno se ganha: *{retorno_esperado:.2f}* unidades"
    )
    return "\n".join(linhas)


def ficha_equipa_resumida(ficha) -> str:
    """Resumo Telegram-ready de uma ficha de equipa (Fase 4 do 2o Cerebro).

    Mostra: liga, baseline, split casa/fora, record W-D-L, fadiga,
    e as 5 notas tacticas mais importantes.
    """
    linhas = [f"🧠 *Ficha — {ficha.nome}*"]
    if ficha.liga:
        linhas.append(f"_{ficha.liga}_  ·  {ficha.jogos_lidos} jogos no histórico")
    linhas.append("")

    # Baseline
    b = ficha.baseline
    linhas.append("*Baseline (por jogo)*")
    if b.golos_marcados is not None:
        linhas.append(
            f"⚽ {b.golos_marcados:.1f} marcados · {b.golos_sofridos:.1f} sofridos"
        )
    if b.remates is not None:
        linhas.append(f"🎯 {b.remates:.1f} remates · {b.sot:.1f} SOT")
    if b.cantos is not None:
        linhas.append(f"🚩 {b.cantos:.1f} cantos")
    if b.cards_amarelos is not None:
        linhas.append(f"🟨 {b.cards_amarelos:.1f} amarelos")
    if b.posse_pct is not None:
        linhas.append(f"⚖ {b.posse_pct:.0f}% posse")

    # Casa/fora
    cf = ficha.casa_fora
    if cf is not None and cf.n_casa > 0 and cf.n_fora > 0:
        linhas.append("")
        linhas.append("*Casa vs Fora*")
        linhas.append(
            f"🏠 {cf.n_casa}j — {cf.vitorias_casa}W/{cf.empates_casa}D/"
            f"{cf.derrotas_casa}L"
            + (f" · {cf.golos_marcados_casa:.1f} marcados"
               if cf.golos_marcados_casa is not None else "")
        )
        linhas.append(
            f"✈ {cf.n_fora}j — {cf.vitorias_fora}W/{cf.empates_fora}D/"
            f"{cf.derrotas_fora}L"
            + (f" · {cf.golos_marcados_fora:.1f} marcados"
               if cf.golos_marcados_fora is not None else "")
        )

    # Fadiga
    f = ficha.fadiga
    linhas.append("")
    linhas.append(
        f"⏱ {f.jogos_ultimos_7d} jogos nos últimos 7d · "
        f"{f.jogos_ultimos_14d} nos últimos 14d"
    )
    if f.minutos_top_titulares_7d is not None:
        linhas.append(f"🥵 Top5 titulares: {f.minutos_top_titulares_7d:.0f} min 7d")

    # Notas tacticas (top 5)
    if ficha.notas:
        linhas.append("")
        linhas.append("*Padrões táticos*")
        for n in ficha.notas[:5]:
            linhas.append(f"• {n}")

    return "\n".join(linhas)


def pausado() -> str:
    return "⏸️ Alertas pausados. Usa /retomar para voltar a receber."


def retomado() -> str:
    return "▶️ Alertas retomados."


def ajuda() -> str:
    return (
        "*Ricardo Tips — comandos*\n\n"
        "`/hoje` — jogos de hoje com EV positivo\n"
        "`/amanha` — jogos de amanhã com EV positivo\n"
        "`/stats` — ROI do mês\n"
        "`/registar ID STAKE ODD` — regista aposta pré-match pela sugestão\n"
        "`/apostei ID ODD STAKE` — regista aposta LIVE a partir de um alerta\n"
        "`/jogo ID` — análise detalhada de um jogo\n"
        "`/equipa <nome>` — ficha táctica da equipa (padrões, casa/fora, carga)\n"
        "`/pausar` — não enviar alertas automáticos\n"
        "`/retomar` — reativar alertas"
    )


def erro(texto: str) -> str:
    return f"⚠️ {texto}"


# ─── helpers ────────────────────────────────────────────────────────

def _fmt_hora(dt: datetime, hora: bool = False) -> str:
    if hora:
        return dt.strftime("%H:%M")
    return dt.strftime("%d/%m %H:%M")


def _linha(linha: float | None) -> str:
    if linha is None:
        return ""
    if float(linha).is_integer():
        return str(int(linha))
    return f"{linha:.1f}"


def _casa_ref() -> str:
    # Dinamicamente poder-se-ia ler config; mantemos simples por agora.
    return "Avg_Closing"
