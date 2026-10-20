"""Notificacoes Telegram para o scanner live v2.

Reutiliza o ClienteTelegram existente. Em MODO=desenvolvimento as mensagens
vao para dados/telegram_outbox.log; em producao vao para o chat Telegram.

Setup rapido:
  1. Fala com @BotFather no Telegram, cria um bot, copia o TOKEN.
  2. Fala uma vez com o bot (/start) no Telegram.
  3. Abre https://api.telegram.org/bot<TOKEN>/getUpdates para veres o teu chat_id.
  4. No .env:
       TELEGRAM_BOT_TOKEN=1234567890:ABCDEF...
       TELEGRAM_CHAT_ID=123456789
       MODO=producao
"""
from __future__ import annotations

from pathlib import Path

from apostas.alertas.telegram_cliente import cliente
from apostas.utils.logger import get_logger

log = get_logger(__name__)


# ─── Cache em memoria dos perfis historicos (M16) ──────────────────────
# Evita recarregar o CSV a cada alerta. Invalidado a cada reinicio do
# scanner — o resolver cresce ao longo do dia mas e aceitavel.
_cache_perfis: dict | None = None
_cache_csv_path: Path | None = None


def _perfis_historicos():
    """Carrega (ou devolve cache) do historico de alertas via CSV."""
    global _cache_perfis, _cache_csv_path
    try:
        from apostas.alertas.historico_alertas import carregar_perfis
        from apostas.utils.config import project_root
    except Exception:  # noqa: BLE001
        return {}
    csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    if _cache_perfis is not None and _cache_csv_path == csv_path:
        return _cache_perfis
    try:
        _cache_perfis = carregar_perfis(csv_path)
        _cache_csv_path = csv_path
    except Exception as exc:  # noqa: BLE001
        log.debug("Carga perfis falhou: %s", exc)
        _cache_perfis = {}
    return _cache_perfis


def _fase_do_jogo(minuto: int) -> str:
    """Emoji + texto curto conforme a fase do jogo."""
    if minuto == 0:
        return "⏱ A começar"
    if minuto < 15:
        return "🔥 Inicio"
    if minuto < 30:
        return "📈 1a parte cedo"
    if minuto < 45:
        return "⏳ Final 1a parte"
    if minuto == 45:
        return "☕ Intervalo"
    if minuto < 60:
        return "🔄 2a parte cedo"
    if minuto < 75:
        return "⚡ Final 2a parte"
    if minuto < 85:
        return "🏁 Reta final"
    return "⏱ Descontos"


def _linhas_treinador(s: dict) -> str:
    """Analise 'ponto de vista do treinador' das duas equipas.

    Devolve 0, 1 ou 2 linhas prefixadas com '🎙 Treinador ...'. Fica em
    silencio se nao houver IDs no sinal ou se as fichas ainda tiverem
    menos de 5 jogos (dados ruidosos demais).
    """
    home_id = s.get("home_id")
    away_id = s.get("away_id")
    if not home_id or not away_id:
        return ""
    try:
        minuto = int(s.get("minuto") or 0)
    except (TypeError, ValueError):
        return ""
    marcador = str(s.get("marcador") or "")
    try:
        parte_c, parte_f = marcador.split("-", 1)
        score_c = int(parte_c.strip())
        score_f = int(parte_f.strip())
    except (ValueError, AttributeError):
        return ""
    try:
        from apostas.alertas.analise_treinador import gerar_para_ambas
        voz_c, voz_f = gerar_para_ambas(
            int(home_id), int(away_id),
            minuto=minuto, score_casa=score_c, score_fora=score_f,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Analise treinador falhou: %s", exc)
        return ""
    linhas = []
    if voz_c is not None:
        linhas.append(f"🎙 {voz_c.texto}")
    if voz_f is not None:
        linhas.append(f"🎙 {voz_f.texto}")
    if not linhas:
        return ""
    return "\n" + "\n".join(linhas)


def _emoji_mercado(mercado: str) -> str:
    return {
        "golos": "⚽",
        "cantos": "🚩",
        "cartoes": "🟨",
        "btts": "🎯",
        "remates": "💥",
        "remates_a_baliza": "🎯",
        "1x2": "🏆",
        "golo_equipa": "📈",
    }.get(mercado, "🎯")


_URLS_CASAS = {
    "pinnacle":    "https://www.pinnacle.com/",
    "betano_uk":   "https://www.betano.pt/",
    "paddypower":  "https://www.paddypower.com/",
    "skybet":      "https://www.skybet.com/",
    "williamhill": "https://sports.williamhill.com/",
    "coral":       "https://sports.coral.co.uk/",
    "betvictor":   "https://www.betvictor.com/",
    "boylesports": "https://www.boylesports.com/",
    "betway":      "https://betway.com/",
    "virginbet":   "https://virginbet.com/",
    "betfred_uk":  "https://www.betfred.com/",
    "unibet_uk":   "https://www.unibet.co.uk/",
    "sport888":    "https://www.888sport.com/",
    "onexbet":     "https://1xbet.com/",
    "marathonbet": "https://www.marathonbet.com/",
}


def enviar_sugestao_live(s: dict) -> None:
    """Envia uma sugestao live para Telegram. ``s`` tem as colunas CSV.

    Dois formatos:
    - Sugestao com odd real: mostra EV, link para casa.
    - Sinal puro do analista (casa='ANALISTA'): mostra prob modelo +
      odd minima + odd desejada para +5% EV.
    """
    try:
        liga = s.get("liga", "?")
        home = s.get("home", "?")
        away = s.get("away", "?")
        minuto = s.get("minuto", "?")
        marcador = s.get("marcador", "?")
        mercado = s.get("mercado", "?")
        lado = s.get("lado", "?")
        linha = s.get("linha", "")
        casa = s.get("casa", "?")
        prob = float(s.get("prob_modelo", 0))
        xg_c = s.get("xg_casa", "")
        xg_f = s.get("xg_fora", "")
        sot_c = s.get("shots_on_target_c", "")
        sot_f = s.get("shots_on_target_f", "")
        aposta_fmt = _formatar_aposta(mercado, lado, linha)
        try:
            estrelas = int(s.get("estrelas") or 3)
        except (TypeError, ValueError):
            estrelas = 3
        estrelas = max(1, min(5, estrelas))
        estrelas_str = "⭐" * estrelas
        # §19.3 flag "verificar": sinal com p_modelo muito longe do mercado
        # (gap >10pp) ou EV puro >15%. Nao se recusa, avisa-se para o user
        # confirmar se nao e lesao/onze errado antes de meter stake grande.
        verificar = bool(s.get("verificar"))
        verificar_str = "⚠ VERIFICAR — " if verificar else ""

        # M17: dual signal — EV + historico forte confirmam juntos. Scanner
        # carimba `dual_sinal` no dict quando passa; usamos aqui apenas para
        # mostrar o badge no texto (nao bloqueia nada).
        dual_str = "🔗 DUAL — " if s.get("dual_sinal") else ""

        # M16: historico por tipo de alerta — linha extra na mensagem
        historico_line = ""
        try:
            from apostas.alertas.historico_alertas import (
                formatar_linha_telegram, perfil_para,
            )
            perfis = _perfis_historicos()
            if perfis:
                p = perfil_para(perfis, liga, mercado, lado)
                if p is not None and p.n_alertas >= 3:
                    historico_line = f"\n{formatar_linha_telegram(p)}"
        except Exception as exc:  # noqa: BLE001
            log.debug("Historico perfil falhou: %s", exc)

        # Treinadores: 1 linha por equipa com o que o mister quer agora
        treinadores_line = _linhas_treinador(s)

        # M19: melhor casa suica (vem de filtros_mercado via scanner)
        melhor_casa_line = ""
        outras_casas_str = s.get("outras_casas") or ""
        if outras_casas_str:
            melhor_casa_line = f"\n💰 {outras_casas_str}"

        # Linha final: /apostei <id> <odd> <stake> para o user registar
        # a aposta real. Alert_id vem do scanner (coluna CSV alert_id).
        alert_id = s.get("alert_id")
        apostei_line = ""
        if alert_id:
            apostei_line = (
                f"\n\n`/apostei {alert_id} <odd> <stake>`"
            )

        if casa == "ANALISTA":
            # Sinal puro — mensagem narrativa com racional
            odd_desejada = float(s.get("odd", 0))
            odd_be = round(1.0 / prob, 2) if prob > 0 else 0
            fase = _fase_do_jogo(int(minuto) if str(minuto).isdigit() else 0)
            # Racional vem como lista no sinal (nao esta no CSV atual, puxa de s se existir)
            racional_str = s.get("racional_str", "")
            racional_section = f"\n{racional_str}\n" if racional_str else ""
            texto = (
                f"{estrelas_str}  {dual_str}{verificar_str}{_emoji_mercado(mercado)} *{aposta_fmt}*  ·  {prob*100:.0f}%\n"
                f"\n"
                f"⚽ {home} vs {away}\n"
                f"🏆 {liga}  ·  [{minuto}']  {marcador}  {fase}\n"
                f"📊 xG {xg_c}-{xg_f}  ·  SoT {sot_c}-{sot_f}"
                f"{historico_line}"
                f"{treinadores_line}"
                f"{racional_section}"
                f"\n"
                f"🎯 Odd alvo *{odd_desejada}* (min {odd_be})\n"
                f"_Procura esta odd. Se bater o alvo, mete._"
                f"{apostei_line}"
            )
        else:
            try:
                ev = float(s.get("ev", 0))
            except (ValueError, TypeError):
                ev = 0.0
            odd = s.get("odd", 0)
            url = _URLS_CASAS.get(casa, "")
            link_line = f"[Abrir casa]({url})" if url else f"Casa: {casa}"
            texto = (
                f"{estrelas_str}  {dual_str}{verificar_str}🎯 *EV +{ev*100:.1f}%* LIVE\n"
                f"\n"
                f"⚽ {home} vs {away}\n"
                f"🏆 {liga}  ·  [{minuto}']  {marcador}\n"
                f"📊 xG {xg_c}-{xg_f}  ·  SoT {sot_c}-{sot_f}"
                f"{historico_line}"
                f"{treinadores_line}"
                f"\n"
                f"\n"
                f"*Aposta:* {aposta_fmt}\n"
                f"*Odd:* {odd}  ({casa})"
                f"{melhor_casa_line}\n"
                f"*Prob modelo:* {prob*100:.1f}%\n"
                f"\n"
                f"{link_line}"
                f"{apostei_line}"
            )
        cliente().enviar(texto)
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha a enviar notificacao Telegram: %s", exc)


def _formatar_aposta(mercado: str, lado: str, linha: str | float) -> str:
    linha_str = str(linha) if linha else ""
    if mercado == "golos":
        return f"{lado.upper()} {linha_str} golos"
    if mercado == "1x2":
        mapa = {"casa": "1 (casa vence)", "empate": "X (empate)", "fora": "2 (fora vence)"}
        return mapa.get(lado, lado)
    if mercado == "btts":
        return "BTTS SIM (ambas marcam)" if lado == "sim" else "BTTS NAO"
    if mercado == "dupla":
        mapa = {"1x": "Dupla 1X", "x2": "Dupla X2", "12": "Dupla 12"}
        return mapa.get(lado, f"Dupla {lado}")
    if mercado == "handicap":
        sinal = "+" if linha_str and float(linha_str) >= 0 else ""
        return f"Handicap {lado.upper()} {sinal}{linha_str}"
    if mercado == "cantos":
        return f"{lado.upper()} {linha_str} cantos"
    if mercado == "cartoes":
        return f"{lado.upper()} {linha_str} cartoes amarelos"
    if mercado == "remates":
        return f"{lado.upper()} {linha_str} remates"
    if mercado == "remates_a_baliza":
        return f"{lado.upper()} {linha_str} remates a baliza"
    if mercado == "golo_equipa":
        # lado = "marca_casa" ou "marca_fora"
        quem = "CASA" if lado.endswith("_casa") else "FORA"
        return f"{quem} marca (regressao xG)"
    if mercado == "1x2":
        return {"casa": "VITORIA CASA",
                "empate": "EMPATE",
                "fora": "VITORIA FORA"}.get(lado, lado)
    return f"{mercado} {lado} {linha_str}".strip()
