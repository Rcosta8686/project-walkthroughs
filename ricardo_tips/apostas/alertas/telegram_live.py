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

from apostas.alertas.telegram_cliente import cliente
from apostas.utils.logger import get_logger

log = get_logger(__name__)


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
    """Envia uma sugestao live para Telegram. ``s`` tem as colunas CSV."""
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
        odd = s.get("odd", 0)
        prob = float(s.get("prob_modelo", 0))
        ev = float(s.get("ev", 0))
        xg_c = s.get("xg_casa", "")
        xg_f = s.get("xg_fora", "")
        sot_c = s.get("shots_on_target_c", "")
        sot_f = s.get("shots_on_target_f", "")

        url = _URLS_CASAS.get(casa, "")
        aposta_fmt = _formatar_aposta(mercado, lado, linha)

        texto = (
            f"🎯 *EV +{ev*100:.1f}%* LIVE\n"
            f"\n"
            f"⚽ {home} vs {away}\n"
            f"🏆 {liga}  ·  [{minuto}']  {marcador}\n"
            f"📊 xG {xg_c}-{xg_f}  ·  SoT {sot_c}-{sot_f}\n"
            f"\n"
            f"*Aposta:* {aposta_fmt}\n"
            f"*Odd:* {odd}  ({casa})\n"
            f"*Prob modelo:* {prob*100:.1f}%\n"
            f"\n"
            f"[Abrir casa]({url})" if url else f"Casa: {casa}"
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
    return f"{mercado} {lado} {linha_str}".strip()
