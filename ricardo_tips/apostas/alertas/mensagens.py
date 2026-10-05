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
        "`/registar ID STAKE ODD` — regista uma aposta efetivamente feita\n"
        "`/jogo ID` — análise detalhada de um jogo\n"
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
