"""Bot Telegram interativo.

Comandos:
    /hoje      — sugestões de jogos de hoje com EV positivo
    /amanha    — sugestões de jogos de amanhã
    /stats     — ROI do mês corrente
    /registar  — regista aposta pre-match: /registar ID STAKE ODD
    /apostei   — regista aposta LIVE a partir de um alerta: /apostei ID ODD STAKE
    /jogo ID   — análise detalhada de um jogo (sugestão específica)
    /equipa    — ficha táctica de uma equipa: /equipa Benfica
    /pausar    — suspende alertas automáticos
    /retomar   — reativa alertas automáticos
    /start, /ajuda — mostra os comandos

Em modo producao arranca em polling. Em dev pode-se correr os handlers
diretamente nos testes sem precisar da Bot API.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import csv

from sqlalchemy import func, select

from apostas.alertas import analise, estado, mensagens
from apostas.utils.config import get_env, project_root
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import (
    ApostaReal, ApostaRealLive, Equipa, Jogo, Liga, Sugestao,
)
from apostas.utils.tempo import agora_utc

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

log = get_logger(__name__)


# ─── Lógica dos comandos (puros, testáveis sem Telegram) ────────────

def comando_hoje(agora: datetime | None = None) -> str:
    agora = agora or agora_utc()
    inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1)
    return mensagens.resumo_dia(
        [(s.sugestao, s.jogo, s.liga, s.casa, s.fora)
         for s in analise.sugestoes_na_janela(inicio, fim)]
    )


def comando_amanha(agora: datetime | None = None) -> str:
    agora = agora or agora_utc()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = inicio_hoje + timedelta(days=1)
    fim = inicio + timedelta(days=1)
    return mensagens.resumo_dia(
        [(s.sugestao, s.jogo, s.liga, s.casa, s.fora)
         for s in analise.sugestoes_na_janela(inicio, fim)]
    )


def comando_stats(agora: datetime | None = None) -> str:
    agora = agora or agora_utc()
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


def _csv_sugestoes_live_path():
    """Localizacao do CSV de sugestoes live (parametrizavel via env)."""
    import os
    override = os.environ.get("RT_LIVE_CSV")
    if override:
        from pathlib import Path
        return Path(override)
    return project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _procurar_linha_live(alert_id: int) -> dict | None:
    """Devolve a linha do CSV live cujo alert_id bate — ou None."""
    path = _csv_sugestoes_live_path()
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fp:
            for linha in csv.DictReader(fp):
                try:
                    if int(linha.get("alert_id") or 0) == alert_id:
                        return linha
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    return None


def comando_apostei(args: list[str]) -> str:
    """`/apostei <alert_id> <odd> <stake>` — regista aposta live.

    O ``alert_id`` vem no texto do alerta Telegram (`#id` no rodape). A
    linha e cruzada com ``sugestoes_live_v2.csv`` para obter mercado,
    liga, probabilidade do modelo etc. Grava em ``ApostaRealLive``.
    """
    if len(args) < 3:
        return mensagens.erro(
            "Uso: /apostei ID ODD STAKE  (ex: /apostei 142 1.95 2.0)"
        )
    try:
        alert_id = int(args[0])
        odd = float(args[1])
        stake = float(args[2])
    except ValueError:
        return mensagens.erro("Parâmetros inválidos. Usa números para ID, ODD e STAKE.")

    if alert_id <= 0:
        return mensagens.erro("ID inválido.")
    if odd <= 1.0:
        return mensagens.erro("Odd tem de ser > 1.")
    if stake <= 0:
        return mensagens.erro("Stake tem de ser > 0.")

    linha = _procurar_linha_live(alert_id)
    if linha is None:
        return mensagens.erro(
            f"Alerta #{alert_id} não encontrado no CSV live. "
            f"Verifica o ID no texto do alerta."
        )

    with abrir_sessao() as s:
        ja = s.scalar(select(ApostaRealLive).where(ApostaRealLive.alert_id == alert_id))
        if ja is not None:
            return mensagens.erro(
                f"Já existe uma aposta registada para o alerta #{alert_id}."
            )

        try:
            event_id = int(linha.get("event_id") or 0)
        except (TypeError, ValueError):
            event_id = 0
        try:
            minuto_val = int(linha.get("minuto") or 0)
        except (TypeError, ValueError):
            minuto_val = None
        try:
            odd_rec = float(linha.get("odd")) if linha.get("odd") else None
        except (TypeError, ValueError):
            odd_rec = None
        try:
            prob = float(linha.get("prob_modelo")) if linha.get("prob_modelo") else None
        except (TypeError, ValueError):
            prob = None

        aposta = ApostaRealLive(
            alert_id=alert_id,
            event_id=event_id,
            liga=linha.get("liga", "") or "",
            home=linha.get("home", "") or "",
            away=linha.get("away", "") or "",
            minuto=minuto_val,
            mercado=linha.get("mercado", "") or "",
            lado=linha.get("lado", "") or "",
            linha=linha.get("linha", "") or None,
            casa_recomendada=linha.get("casa") or None,
            odd_recomendada=odd_rec,
            odd_executada=odd,
            stake=stake,
            prob_modelo=prob,
            resultado="pendente",
        )
        s.add(aposta)

    from apostas.alertas.telegram_live import _formatar_aposta
    aposta_fmt = _formatar_aposta(
        linha.get("mercado", "") or "",
        linha.get("lado", "") or "",
        linha.get("linha", "") or "",
    )
    retorno = stake * (odd - 1)
    return mensagens.apostei_confirmado(
        alert_id=alert_id,
        home=linha.get("home", "?"),
        away=linha.get("away", "?"),
        aposta=aposta_fmt,
        stake=stake,
        odd_executada=odd,
        odd_recomendada=odd_rec,
        retorno_esperado=retorno,
        clv_pct=None,  # Preenchido pelo resolver depois do fecho
    )


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


def comando_equipa(args: list[str]) -> str:
    """`/equipa <nome>` — devolve ficha resumida de uma equipa.

    Faz lookup fuzzy no historico persistente. Se o nome e ambiguo,
    lista as opcoes mais recentes para o user escolher.
    """
    if not args:
        return mensagens.erro(
            "Uso: /equipa <nome>  (ex: /equipa Benfica)"
        )
    query = " ".join(args).strip()
    try:
        from apostas.modelos.historico_equipas import encontrar_equipa_por_nome
        from apostas.relatorios.ficha_equipa import gerar_ficha
    except Exception as exc:  # noqa: BLE001
        return mensagens.erro(f"Não foi possível carregar módulos: {exc}")

    candidatos = encontrar_equipa_por_nome(query, max_resultados=5)
    if not candidatos:
        return mensagens.erro(
            f"Nenhuma equipa com '{query}' no histórico. Correste "
            f"`popular_cerebros.py`?"
        )
    if len(candidatos) > 1:
        linhas = [f"🔍 Várias equipas correspondem a '{query}':"]
        for tid, nome in candidatos:
            linhas.append(f"  • {nome} (`id={tid}`)")
        linhas.append("")
        linhas.append("Repete com o nome mais completo (ex: 'Sporting CP').")
        return "\n".join(linhas)

    team_id, nome = candidatos[0]
    ficha = gerar_ficha(team_id, n_jogos=20)
    if ficha is None:
        return mensagens.erro(
            f"{nome} não tem jogos suficientes no histórico (<3)."
        )
    return mensagens.ficha_equipa_resumida(ficha)


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


async def h_apostei(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_apostei(ctx.args))


async def h_jogo(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_jogo(ctx.args))


async def h_equipa(update: "Update", ctx: "ContextTypes.DEFAULT_TYPE") -> None:
    if not _check_autorizado(update):
        return
    await _responder(update, comando_equipa(ctx.args))


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
    app.add_handler(CommandHandler("apostei", h_apostei))
    app.add_handler(CommandHandler("jogo", h_jogo))
    app.add_handler(CommandHandler("equipa", h_equipa))
    app.add_handler(CommandHandler("pausar", h_pausar))
    app.add_handler(CommandHandler("retomar", h_retomar))
    app.add_handler(CommandHandler(["start", "ajuda", "help"], h_ajuda))
    return app
