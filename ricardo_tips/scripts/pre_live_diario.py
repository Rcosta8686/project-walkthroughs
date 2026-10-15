"""Pre-live diario: analise pre-match dos jogos do dia.

Fluxo:
  1. Puxa jogos agendados para hoje (Sofascore scheduled-events)
  2. Filtra para ligas de interesse (zonas vencedoras + Big-5 principais)
  3. Para cada jogo: puxa H2H, forma, AI insights, classificacao
  4. Gera previsoes pre-match para mercados vencedores (remates/BTTS/SoT)
  5. Envia resumo para Telegram

Uso:
  python scripts/pre_live_diario.py
  python scripts/pre_live_diario.py --data 2026-10-16
  python scripts/pre_live_diario.py --telegram
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.alertas import analista  # noqa: E402
from apostas.alertas.telegram_cliente import cliente  # noqa: E402
from apostas.ingestao import sofascore_live, sofascore_classificacao, sofascore_contexto  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)

# Baselines europeus pre-match
LAM_GOLOS_BASE = 2.6
LAM_CANTOS_BASE = 10.5
LAM_CARDS_BASE = 4.0
LAM_SHOTS_BASE = 24.0           # 12 por equipa
LAM_SOT_BASE = 24.0 * 0.35      # ~8.4 (ratio liga)


def _poisson_cdf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    acc = math.exp(-lam)
    termo = acc
    for i in range(1, k + 1):
        termo *= lam / i
        acc += termo
    return min(1.0, acc)


def _p_over(linha: float, lam: float) -> float:
    """P(total >= ceil(linha + 0.5)) com Poisson."""
    threshold = int(linha + 1) if linha % 1 == 0.5 else int(linha) + 1
    return 1.0 - _poisson_cdf(threshold - 1, lam)


def _ajustar_lam_com_h2h(lam_base: float, h2h_media: float | None, peso: float = 0.3) -> float:
    """Ajusta lambda base com media H2H historica."""
    if h2h_media is None:
        return lam_base
    return (1 - peso) * lam_base + peso * h2h_media


def _ajustar_lam_com_forma(lam_base: float, forma_c: dict, forma_f: dict,
                            tipo: str = "golos") -> float:
    """Ajusta lam baseado em forma recente de cada equipa.

    tipo: 'golos' usa (golos_marcados + sofridos) / 2 de cada equipa
    """
    if not forma_c or not forma_f:
        return lam_base
    if tipo == "golos":
        gm_c = forma_c.get("golos_marcados_media", 1.3)
        gs_c = forma_c.get("golos_sofridos_media", 1.3)
        gm_f = forma_f.get("golos_marcados_media", 1.1)
        gs_f = forma_f.get("golos_sofridos_media", 1.1)
        # Expectativa combinada
        exp_c = (gm_c + gs_f) / 2 + 0.1  # home advantage pequena
        exp_f = (gm_f + gs_c) / 2
        exp_total = exp_c + exp_f
        # Blend com baseline
        return 0.5 * lam_base + 0.5 * exp_total
    return lam_base


def _emoji_mercado(mercado: str) -> str:
    return {"remates": "💥", "btts": "🎯", "remates_a_baliza": "🎯",
            "golos": "⚽", "cantos": "🚩"}.get(mercado, "📊")


def _ai_insights_relevantes(ai: list) -> list[str]:
    """Filtra AI insights para 2-3 mais relevantes."""
    palavras_chave = ("scored", "conceded", "won", "lost", "clean sheet",
                      "both teams", "over 2", "under 2", "home matches",
                      "away matches", "last 5", "last 10", "corner",
                      "cards", "shots")
    relevantes = []
    for s in ai[:12]:
        sl = (s or "").lower()
        if any(k in sl for k in palavras_chave) and len(s) < 180:
            relevantes.append(s)
        if len(relevantes) >= 3:
            break
    return relevantes


def _analisar_jogo(evento: dict) -> dict | None:
    """Analisa um jogo pre-match e devolve dict com sinais."""
    event_id = evento.get("id")
    home = (evento.get("homeTeam") or {}).get("name", "?")
    away = (evento.get("awayTeam") or {}).get("name", "?")
    liga = (evento.get("tournament") or {}).get("name", "?")
    hora_utc = evento.get("startTimestamp")
    try:
        hora = datetime.fromtimestamp(int(hora_utc)).strftime("%H:%M")
    except (TypeError, ValueError):
        hora = "?"

    # H2H + forma + AI insights (cache 24h, so uma chamada por jogo por dia)
    h2h = sofascore_contexto.obter_h2h(event_id)
    ai = sofascore_contexto.obter_ai_insights(event_id)
    home_id = (evento.get("homeTeam") or {}).get("id")
    away_id = (evento.get("awayTeam") or {}).get("id")
    forma_c = sofascore_contexto.obter_forma_recente(home_id) if home_id else {}
    forma_f = sofascore_contexto.obter_forma_recente(away_id) if away_id else {}

    # Lambda estimates
    media_h2h = h2h.get("media_golos_total")
    lam_golos = _ajustar_lam_com_h2h(LAM_GOLOS_BASE, media_h2h)
    lam_golos = _ajustar_lam_com_forma(lam_golos, forma_c, forma_f)

    lam_shots = LAM_SHOTS_BASE
    lam_sot = LAM_SOT_BASE
    lam_cantos = LAM_CANTOS_BASE

    # Sinais nos mercados vencedores
    sinais = []

    # Over remates (linha padrao 22.5 e 20.5)
    # Threshold 0.60: pre-match tem incerteza natural maior que live
    for linha in (22.5, 24.5, 26.5):
        p = _p_over(linha, lam_shots)
        if p >= 0.60:
            sinais.append({
                "mercado": "remates", "linha": linha, "lado": "over",
                "prob": p, "emoji": _emoji_mercado("remates"),
            })
            break

    # SoT over
    for linha in (7.5, 8.5, 9.5):
        p = _p_over(linha, lam_sot)
        if p >= 0.60:
            sinais.append({
                "mercado": "remates_a_baliza", "linha": linha, "lado": "over",
                "prob": p, "emoji": _emoji_mercado("remates_a_baliza"),
            })
            break

    # BTTS (via H2H rate + Poisson)
    if h2h.get("n_jogos", 0) >= 5:
        btts_rate = h2h["btts_rate"]
        p_casa_marca = 1 - math.exp(-lam_golos / 2)
        p_fora_marca = 1 - math.exp(-lam_golos / 2)
        p_btts_poisson = p_casa_marca * p_fora_marca
        p_btts_final = 0.5 * p_btts_poisson + 0.5 * btts_rate
        if p_btts_final >= 0.57:
            sinais.append({
                "mercado": "btts", "linha": None, "lado": "sim",
                "prob": p_btts_final, "emoji": _emoji_mercado("btts"),
            })

    # Devolve sempre o resultado — mesmo sem sinais, queremos ver H2H/forma
    # para contexto manual. Lista de sinais pode ficar vazia.

    # Rank por estrelas
    for s in sinais:
        s["estrelas"] = analista.calcular_estrelas(s["mercado"], liga, s["prob"])

    return {
        "event_id": event_id,
        "home": home,
        "away": away,
        "liga": liga,
        "hora": hora,
        "sinais": sinais,
        "h2h": h2h,
        "forma_c": forma_c,
        "forma_f": forma_f,
        "ai_insights": _ai_insights_relevantes(ai),
    }


def _formatar_jogo(j: dict) -> str:
    """Formato compacto para Telegram."""
    linhas = [f"🏆 *{j['liga']}* · {j['hora']}", f"⚽ {j['home']} vs {j['away']}"]

    # Sinais ordenados por estrelas desc
    sinais = sorted(j["sinais"], key=lambda x: -x["estrelas"])
    for s in sinais:
        estrelas = "⭐" * s["estrelas"]
        linha_str = str(s["linha"]) if s["linha"] is not None else ""
        if s["mercado"] == "btts":
            desc = "BTTS SIM"
        else:
            desc = f"Over {linha_str} {s['mercado']}"
        linhas.append(f"{estrelas} {s['emoji']} {desc} · {s['prob']*100:.0f}%")

    # Contexto H2H
    h2h = j["h2h"]
    if h2h.get("n_jogos", 0) >= 4:
        n = h2h["n_jogos"]
        if h2h["over_2_5_rate"] >= 0.7:
            linhas.append(f"  H2H: {int(h2h['over_2_5_rate']*n)}/{n} over 2.5")
        if h2h["btts_rate"] >= 0.7:
            linhas.append(f"  H2H: BTTS em {int(h2h['btts_rate']*n)}/{n}")

    # Forma
    fc = j["forma_c"]
    ff = j["forma_f"]
    if fc.get("wdl"):
        linhas.append(f"  {j['home']}: {fc['wdl']} · "
                       f"{fc['golos_marcados_media']:.1f}m-{fc['golos_sofridos_media']:.1f}s")
    if ff.get("wdl"):
        linhas.append(f"  {j['away']}: {ff['wdl']} · "
                       f"{ff['golos_marcados_media']:.1f}m-{ff['golos_sofridos_media']:.1f}s")

    # AI insights (max 2 em formato curto)
    for ins in j["ai_insights"][:2]:
        linhas.append(f"  ℹ {ins[:120]}")

    return "\n".join(linhas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None,
                        help="Data YYYY-MM-DD (default: hoje)")
    parser.add_argument("--telegram", action="store_true",
                        help="Envia resumo para Telegram")
    parser.add_argument("--max-jogos", type=int, default=15,
                        help="Max jogos a analisar")
    args = parser.parse_args()

    data_iso = args.data or date.today().isoformat()

    print(f"\n{'=' * 90}")
    print(f"  PRE-LIVE DIARIO  —  {data_iso}")
    print("=" * 90)

    # Fetch TODOS os jogos (sem filtro API-side por ids_ligas que pode falhar
    # devido a divergencia entre uniqueTournament.id e tournament.id).
    # Filtramos depois em Python por nome, mais robusto.
    # incluir_live=True: jogos ja a decorrer sao uteis (ex. ver UEL 18:45 HT
    # enquanto o 21:00 nao comecou).
    eventos = sofascore_live.listar_jogos_do_dia(data_iso, incluir_live=True)
    print(f"\n{len(eventos)} jogos agendados+live totais (todas as competicoes).")

    # Filtra: nao-blacklist E (zona vencedora OR liga Big-5/UCL para contexto)
    _ligas_focus_substr = ("premier league", "laliga", "serie a", "serie b",
                            "bundesliga", "ligue 1", "ligue 2", "liga portugal",
                            "champions league", "europa league", "conference league",
                            "championship", "hypermotion", "mls", "regionalliga")

    def _passa_focus(liga: str) -> bool:
        n = (liga or "").lower()
        return any(k in n for k in _ligas_focus_substr)

    eventos_filtrados = []
    for e in eventos:
        liga = (e.get("tournament") or {}).get("name", "")
        if analista.liga_na_blacklist(liga):
            continue
        if not _passa_focus(liga):
            continue
        eventos_filtrados.append(e)
    print(f"{len(eventos_filtrados)} jogos nas ligas-foco (apos blacklist).")
    eventos_filtrados = eventos_filtrados[: args.max_jogos]
    print(f"A analisar top {len(eventos_filtrados)} (limite {args.max_jogos}).\n")

    jogos_analisados = []
    for i, ev in enumerate(eventos_filtrados, 1):
        home = (ev.get("homeTeam") or {}).get("name", "?")
        away = (ev.get("awayTeam") or {}).get("name", "?")
        liga = (ev.get("tournament") or {}).get("name", "?")
        print(f"  [{i}/{len(eventos_filtrados)}] {home} vs {away}  ({liga}) ...", end=" ")
        try:
            resultado = _analisar_jogo(ev)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ ({exc})")
            continue
        if not resultado:
            print("sem contexto")
            continue
        n_sinais = len(resultado["sinais"])
        if n_sinais:
            estrelas_max = max(s["estrelas"] for s in resultado["sinais"])
            print(f"✓ {n_sinais} sinal(ais), top {estrelas_max}⭐")
        else:
            print("sem sinais fortes (mostrado para contexto)")
        jogos_analisados.append(resultado)

    jogos_com_sinais = [j for j in jogos_analisados if j["sinais"]]
    jogos_sem_sinais = [j for j in jogos_analisados if not j["sinais"]]

    # Ordena com-sinais por melhor estrela
    jogos_com_sinais.sort(
        key=lambda j: -max(s["estrelas"] for s in j["sinais"])
    )

    print(f"\n{'=' * 90}")
    print(f"  JOGOS COM SINAIS PRE-MATCH  —  {len(jogos_com_sinais)}")
    print("=" * 90)
    for j in jogos_com_sinais:
        print()
        print(_formatar_jogo(j))

    if jogos_sem_sinais:
        print(f"\n{'=' * 90}")
        print(f"  OUTROS JOGOS DO DIA (sem sinal forte)  —  {len(jogos_sem_sinais)}")
        print("=" * 90)
        for j in jogos_sem_sinais:
            print()
            print(_formatar_jogo(j))

    if args.telegram and jogos_com_sinais:
        print(f"\n{'─' * 90}")
        print("  Enviando para Telegram...")
        cab = f"📅 *PRE-LIVE {data_iso}*  ·  {len(jogos_com_sinais)} jogo(s) com sinais\n"
        try:
            cliente().enviar(cab)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha a enviar cabecalho: %s", exc)
        for j in jogos_com_sinais:
            try:
                cliente().enviar(_formatar_jogo(j))
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha a enviar jogo %s: %s", j.get("event_id"), exc)
        print(f"  {len(jogos_com_sinais)} jogo(s) enviado(s).")

    print()


if __name__ == "__main__":
    main()
