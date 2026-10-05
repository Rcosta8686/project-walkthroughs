"""Bot Telegram interativo.

Comandos:
    /hoje      — sugestões de jogos de hoje com EV positivo
    /amanha    — sugestões de jogos de amanhã
    /stats     — ROI do mês corrente
    /registar  — regista uma aposta efetivamente feita: /registar ID STAKE ODD
    /jogo ID   — análise detalhada de um jogo (sugestão específica)
    /pausar    — suspende alertas automáticos
    /retomar   — reativa alertas automáticos
    /start, /ajuda — mostra os comandos

Em modo producao arranca em polling. Em dev pode-se correr os handlers
diretamente nos testes sem precisar da Bot API.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from apostas.alertas import analise, estado, mensagens
from apostas.utils.config import get_env
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import ApostaReal, Equipa, Jogo, Liga, Sugestao

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

log = get_logger(__name__)


# ─── Lógica dos comandos (puros, testáveis sem Telegram) ────────────

def comando_hoje(agora: datetime | None = None) -> str:
    agora = agora or datetime.utcnow()
    inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1)
    return mensagens.resumo_dia(
        [(s.sugestao, s.jogo, s.liga, s.casa, s.fora)
         for s in analise.sugestoes_na_janela(inicio, fim)]
    )


def comando_amanha(agora: datetime | None = None) -> str:
    agora = agora or datetime.utcnow()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = inicio_hoje + timedelta(days=1)
    fim = inicio + timedelta(days=1)
    return mensagens.resumo_dia(
        [(s.sugestao, s.jogo, s.liga, s.casa, s.fora)
         for s in analise.sugestoes_na_janela(inicio, fim)]
    )


def comando_stats(agora: datetime | None = None) -> str:
    agora = agora or datetime.utcnow()
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    with abrir_sessao() as s:
        apostas = s.scalars(
            select(ApostaReal).where(ApostaReal.criado_em >= inicio_mes)
        ).all()
        stake_total = sum(a.stake for a in apostas)
        lucro = sum(a.lucro or 0.0 for a in apostas)
        n_ganhas = sum(1 for a in apostas if a.resultado == "ganho")
    return mensagens.estatisticas_mes(
        inicio_mes, len(apostas), n_ganhas, stake_total, lucro,
    )


def comando_registar(args: list[str]) -> str:
    if len(args) < 3:
        return mensagens.erro(
            "Uso: /registar ID STAKE ODD (ex.: /registar 42 1 1.85)"
        )
    try:
        sid = int(args[0])
        stake = float(args[1])
        odd = float(args[2])
    except ValueError:
        return mensagens.erro("Parâmetros inválidos. Usa números para ID, STAKE e ODD.")

    if stake <= 0 or odd <= 1:
        return mensagens.erro("Stake deve ser > 0 e odd > 1.")

    with abrir_sessao() as s:
        sugestao = s.get(Sugestao, sid)
        if sugestao is None:
            return mensagens.erro(f"Sugestão #{sid} não encontrada.")

        ja_registada = s.scalar(
            select(ApostaReal).where(ApostaReal.sugestao_id == sid)
        )
        if ja_registada is not None:
            return mensagens.erro(f"Já existe uma aposta registada para a sugestão #{sid}.")

        s.add(ApostaReal(
            sugestao_id=sid,
            stake=stake,
            odd_executada=odd,
            casa_de_apostas="22bet",
            resultado="pendente",
        ))
        sugestao.estado = "aceite"

    retorno = stake * (odd - 1)
    return mensagens.registo_confirmado(sid, stake, odd, retorno)


def comando_jogo(args: list[str]) -> str:
    if not args:
        return mensagens.erro("Uso: /jogo ID (o ID é o número da sugestão).")
    try:
        sid = int(args[0])
    except ValueError:
        return mensagens.erro("ID tem de ser um número.")

    with abrir_sessao() as s:
        sugestao = s.get(Sugestao, sid)
        if sugestao is None:
            return mensagens.erro(f"Sugestão #{sid} não encontrada.")
        jogo = s.get(Jogo, sugestao.jogo_id)
        liga = s.get(Liga, jogo.liga_id)
        casa = s.get(Equipa, jogo.casa_id)
        fora = s.get(Equipa, jogo.fora_id)
        return mensagens.sugestao_nova(sugestao, jogo, liga, casa, fora)


def comando_pausar() -> str:
    estado.pausar()
    return mensagens.pausado()


def comando_retomar() -> str:
    estado.retomar()
    return mensagens.retomado()


def comando_ajuda() -> str:
    return mensagens.ajuda()


# ─── Integração com python-telegram-bot ─────────────────────────────

def _check_autorizado(update) -> bool:
    """Garante que só o chat_id autorizado consegue falar com o bot."""
    try:
        chat_id = int(get_env("TELEGRAM_CHAT_ID", required=True))
    except (TypeError, ValueError):
        return False
    return update.effective_chat.id == chat_id


async def _responder(update, texto: str) -> None:
    await update.message.reply_text(texto, parse_mode="Markdown")


async def h_hoje(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_hoje())


async def h_amanha(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_amanha())


async def h_stats(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_stats())


async def h_registar(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_registar(ctx.args))


async def h_jogo(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_jogo(ctx.args))


async def h_pausar(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_pausar())


async def h_retomar(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_retomar())


async def h_ajuda(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_ajuda())


def construir_application():
    """Monta a Application do python-telegram-bot pronta a correr."""
    from telegram.ext import Application, CommandHandler

    token = get_env("TELEGRAM_BOT_TOKEN", required=True)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("hoje", h_hoje))
    app.add_handler(CommandHandler("amanha", h_amanha))
    app.add_handler(CommandHandler("stats", h_stats))
    app.add_handler(CommandHandler("registar", h_registar))
    app.add_handler(CommandHandler("jogo", h_jogo))
    app.add_handler(CommandHandler("pausar", h_pausar))
    app.add_handler(CommandHandler("retomar", h_retomar))
    app.add_handler(CommandHandler(["start", "ajuda", "help"], h_ajuda))
    return app
