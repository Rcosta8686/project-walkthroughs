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
from apostas.alertas.filtros import ContextoFiltro, passa_filtros_m6  # noqa: E402
from apostas.alertas.telegram_cliente import cliente  # noqa: E402
from apostas.ingestao import sofascore_classificacao, sofascore_contexto, sofascore_live  # noqa: E402
from apostas.modelos import banca, clv_tracker, perfil_equipa  # noqa: E402
from apostas.modelos.banca import STAKE_FLAT_CHF_DEFAULT  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)

# Baselines europeus pre-match
LAM_GOLOS_BASE = 2.6
LAM_CANTOS_BASE = 10.5
LAM_CARDS_BASE = 4.0
LAM_SHOTS_BASE = 24.0           # 12 por equipa
LAM_SOT_BASE = 24.0 * 0.35      # ~8.4 (ratio liga)

STAKE_FLAT_CHF = STAKE_FLAT_CHF_DEFAULT    # 5 CHF por sinal (M13)


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

    # Perfil comportamental (M-doc user ask) — cache 24h via BD
    perfil_c = perfil_equipa.obter_perfil(home_id) if home_id else None
    perfil_f = perfil_equipa.obter_perfil(away_id) if away_id else None

    # Lambda estimates — AJUSTADO por H2H + forma + perfil (varia por jogo)
    media_h2h = h2h.get("media_golos_total")
    lam_golos = _ajustar_lam_com_h2h(LAM_GOLOS_BASE, media_h2h)
    lam_golos = _ajustar_lam_com_forma(lam_golos, forma_c, forma_f)

    # Ajuste de perfil: se uma das equipas tem caudal_atras alto e perfil
    # indica que vira jogos (>35%), aumenta ligeiramente lam_golos (jogos
    # mais abertos). Decisao conservadora para nao distorcer demasiado.
    caudal_bonus = 0.0
    if perfil_c and perfil_c.virar_jogos_pct and perfil_c.virar_jogos_pct > 0.35:
        caudal_bonus += 0.08
    if perfil_f and perfil_f.virar_jogos_pct and perfil_f.virar_jogos_pct > 0.35:
        caudal_bonus += 0.08
    lam_golos += caudal_bonus

    # Sinais nos mercados que REALMENTE sabemos modelar pre-match:
    # golos e BTTS (temos lam ajustado por equipa). Remates/SoT precisariam
    # de dados per-team de remates por jogo que nao temos pre-match.
    sinais = []

    # Over 2.5 golos
    p_over_25 = _p_over(2.5, lam_golos)
    if p_over_25 >= 0.60:
        sinais.append({
            "mercado": "golos", "linha": 2.5, "lado": "over",
            "prob": p_over_25, "emoji": _emoji_mercado("golos"),
        })

    # Over 1.5 golos (sinal mais seguro, threshold mais alto)
    if not sinais:  # so se nao emitiu Over 2.5
        p_over_15 = _p_over(1.5, lam_golos)
        if p_over_15 >= 0.78:
            sinais.append({
                "mercado": "golos", "linha": 1.5, "lado": "over",
                "prob": p_over_15, "emoji": _emoji_mercado("golos"),
            })

    # Under 3.5 (defensivo — para jogos com lam baixo)
    if lam_golos < 2.2:
        p_under_35 = _poisson_cdf(3, lam_golos)
        if p_under_35 >= 0.78:
            sinais.append({
                "mercado": "golos", "linha": 3.5, "lado": "under",
                "prob": p_under_35, "emoji": _emoji_mercado("golos"),
            })

    # BTTS (via H2H rate + Poisson com lam dividido 50/50)
    if h2h.get("n_jogos", 0) >= 5:
        btts_rate = h2h["btts_rate"]
        p_casa_marca = 1 - math.exp(-lam_golos / 2)
        p_fora_marca = 1 - math.exp(-lam_golos / 2)
        p_btts_poisson = p_casa_marca * p_fora_marca
        p_btts_final = 0.5 * p_btts_poisson + 0.5 * btts_rate
        if p_btts_final >= 0.60:
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
        "perfil_c": perfil_c,
        "perfil_f": perfil_f,
        "lam_golos": lam_golos,
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
        elif s["mercado"] == "golos":
            desc = f"{s['lado'].capitalize()} {linha_str} golos"
        else:
            desc = f"{s['lado'].capitalize()} {linha_str} {s['mercado']}"
        stake_txt = f" · stake {STAKE_FLAT_CHF:.0f} CHF" if s.get("stake") else ""
        linhas.append(f"{estrelas} {s['emoji']} {desc} · {s['prob']*100:.0f}%{stake_txt}")

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

    # Perfil comportamental (quando disponivel) — padroes importantes
    for nome, perfil in (("home", j.get("perfil_c")), ("away", j.get("perfil_f"))):
        if not perfil or perfil.n_jogos_analisados < 3:
            continue
        equipa = j["home"] if nome == "home" else j["away"]
        partes = []
        if perfil.virar_jogos_pct is not None and perfil.virar_jogos_pct >= 0.30:
            partes.append(f"vira {perfil.virar_jogos_pct*100:.0f}%")
        if perfil.remates_atras_90 is not None and perfil.remates_atras_90 >= 14:
            partes.append(f"{perfil.remates_atras_90:.0f} rem/90 atras")
        if perfil.cantos_atras_90 is not None and perfil.cantos_atras_90 >= 6:
            partes.append(f"{perfil.cantos_atras_90:.0f} cantos atras")
        if perfil.carga_jogos_7d is not None and perfil.carga_jogos_7d >= 3:
            partes.append(f"carga {perfil.carga_jogos_7d}×7d")
        if partes:
            linhas.append(f"  🔎 {equipa}: {', '.join(partes)}")

    # AI insights (max 2 em formato curto)
    for ins in j["ai_insights"][:2]:
        linhas.append(f"  ℹ {ins[:120]}")

    return "\n".join(linhas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None,
                        help="Data YYYY-MM-DD (default: hoje)")
    parser.add_argument("--dias", type=int, default=1,
                        help="Numero de dias a cobrir a partir de --data (default 1). "
                             "Use --dias 3 na sexta para apanhar sex+sab+dom.")
    parser.add_argument("--telegram", action="store_true",
                        help="Envia resumo para Telegram")
    parser.add_argument("--max-jogos", type=int, default=15,
                        help="Max jogos a analisar")
    args = parser.parse_args()

    data_iso = args.data or date.today().isoformat()

    print(f"\n{'=' * 90}")
    print(f"  PRE-LIVE DIARIO  —  {data_iso}")
    print("=" * 90)

    # ─── M13: banca gate antes de analisar ───
    try:
        estado = banca.estado_banca_actual()
        print(f"\n💰 Banca: CHF {estado.saldo_atual:.2f} | "
              f"perda hoje: CHF {estado.perda_dia:.2f} | "
              f"stake flat: CHF {STAKE_FLAT_CHF:.2f}")
        if not estado.pode_apostar:
            print(f"⛔ STOP: {estado.motivo} — nao envio sinais Telegram.")
            telegram_bloqueado_stop = True
        else:
            telegram_bloqueado_stop = False
    except Exception as exc:  # noqa: BLE001
        log.warning("Banca indisponivel: %s", exc)
        telegram_bloqueado_stop = False

    # Fetch jogos de 1+ dias (--dias)
    from datetime import timedelta  # noqa: PLC0415
    eventos: list[dict] = []
    try:
        d0 = datetime.fromisoformat(data_iso).date()
    except ValueError:
        d0 = date.today()
    for offset in range(args.dias):
        d_iso = (d0 + timedelta(days=offset)).isoformat()
        print(f"\n→ puxando jogos de {d_iso}...")
        evs = sofascore_live.listar_jogos_do_dia(d_iso, incluir_live=(offset == 0))
        eventos.extend(evs)
    # Dedup por id
    vistos = set()
    eventos_dedup = []
    for ev in eventos:
        eid = ev.get("id")
        if eid not in vistos:
            vistos.add(eid)
            eventos_dedup.append(ev)
    eventos = eventos_dedup
    print(f"\n{len(eventos)} jogos totais nos {args.dias} dia(s) a partir de {data_iso}.")

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

        # ─── M6: filtro final 8 pontos + anotacao stake + snapshot CLV ───
        sinais_passaram = []
        for s in resultado["sinais"]:
            ctx = ContextoFiltro(
                ev=0.05,  # placeholder — pre_live nao calcula EV sem odds
                odd=2.0,  # placeholder; proxima iteracao usa odd pre-match real
                mercado=s["mercado"],
                liga=liga,
                dados_recentes=True,
                momento="abertura",
                stake_pretendida_chf=STAKE_FLAT_CHF,
                stop_loss_hoje_atingido=telegram_bloqueado_stop,
            )
            rf = passa_filtros_m6(ctx)
            s["m6_passou"] = rf.passou
            s["m6_razao"] = rf.razao()
            s["stake"] = STAKE_FLAT_CHF if rf.passou else 0.0
            # Grava snapshot (momento "pre_live_12h") — sem odds reais ainda
            # mas marca o sinal como "foi emitido".
            if rf.passou and resultado.get("event_id"):
                try:
                    clv_tracker.gravar_snapshot(
                        sofascore_event_id=int(resultado["event_id"]),
                        mercado=s["mercado"], linha=s.get("linha"),
                        lado=s["lado"], odd=2.0,  # placeholder
                        casa="modelo_pre_live", momento="pre_live_12h",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("snapshot falhou: %s", exc)
            if rf.passou:
                sinais_passaram.append(s)
        resultado["sinais_filtrados"] = sinais_passaram

        n_total = len(resultado["sinais"])
        n_m6 = len(sinais_passaram)
        if n_m6:
            estrelas_max = max(s["estrelas"] for s in sinais_passaram)
            print(f"✓ {n_m6}/{n_total} sinal(ais) passam M6, top {estrelas_max}⭐")
        elif n_total:
            print(f"∅ {n_total} sinais mas 0 passam M6 (contexto so)")
        else:
            print("sem sinais fortes (contexto)")
        jogos_analisados.append(resultado)

    # Agora distinguimos entre: tem sinais QUE PASSARAM M6 vs so contexto
    jogos_com_sinais_m6 = [
        j for j in jogos_analisados if j.get("sinais_filtrados")
    ]
    jogos_sinais_rejeitados = [
        j for j in jogos_analisados
        if j["sinais"] and not j.get("sinais_filtrados")
    ]
    jogos_sem_sinais = [j for j in jogos_analisados if not j["sinais"]]

    # Ordena por melhor estrela entre sinais que passaram
    jogos_com_sinais_m6.sort(
        key=lambda j: -max(s["estrelas"] for s in j["sinais_filtrados"])
    )

    print(f"\n{'=' * 90}")
    print(f"  JOGOS COM SINAIS PRE-MATCH APROVADOS (M6)  —  {len(jogos_com_sinais_m6)}")
    print("=" * 90)
    for j in jogos_com_sinais_m6:
        # Mostra so sinais aprovados
        j_mostrar = dict(j, sinais=j["sinais_filtrados"])
        print()
        print(_formatar_jogo(j_mostrar))

    if jogos_sinais_rejeitados:
        print(f"\n{'=' * 90}")
        print(f"  SINAIS REJEITADOS PELO M6 (apenas contexto)  —  {len(jogos_sinais_rejeitados)}")
        print("=" * 90)
        for j in jogos_sinais_rejeitados:
            print()
            print(_formatar_jogo(j))
            # Mostra o motivo da rejeicao
            for s in j["sinais"]:
                if not s.get("m6_passou"):
                    print(f"    ⨯ {s['mercado']}: {s.get('m6_razao', 'rejeitado')}")

    if jogos_sem_sinais:
        print(f"\n{'=' * 90}")
        print(f"  OUTROS JOGOS DO DIA (sem sinal forte)  —  {len(jogos_sem_sinais)}")
        print("=" * 90)
        for j in jogos_sem_sinais:
            print()
            print(_formatar_jogo(j))

    # ─── Telegram: so sinais que passaram M6 e so se stop-loss nao bateu ───
    if args.telegram and jogos_com_sinais_m6 and not telegram_bloqueado_stop:
        print(f"\n{'─' * 90}")
        print("  Enviando para Telegram...")
        n_sinais = sum(len(j["sinais_filtrados"]) for j in jogos_com_sinais_m6)
        stake_total = n_sinais * STAKE_FLAT_CHF
        cab = (f"📅 *PRE-LIVE {data_iso}*  ·  {len(jogos_com_sinais_m6)} jogo(s), "
                f"{n_sinais} sinal(ais)\n💰 Stake total: CHF {stake_total:.0f}")
        try:
            cliente().enviar(cab)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha a enviar cabecalho: %s", exc)
        for j in jogos_com_sinais_m6:
            try:
                j_tg = dict(j, sinais=j["sinais_filtrados"])
                cliente().enviar(_formatar_jogo(j_tg))
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha a enviar jogo %s: %s", j.get("event_id"), exc)
        print(f"  {len(jogos_com_sinais_m6)} jogo(s) enviado(s).")
    elif args.telegram and telegram_bloqueado_stop:
        print("\n⛔ Telegram NAO enviado: stop-loss diario atingido.")

    print()


if __name__ == "__main__":
    main()
