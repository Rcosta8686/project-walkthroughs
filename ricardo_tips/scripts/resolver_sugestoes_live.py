"""Resolver sugestoes live: puxar resultado final + marcar W/L + lucro.

Fluxo:
  1. Le dados/tracking/sugestoes_live_v2.csv
  2. Agrupa linhas por event_id (varios mercados do mesmo jogo = 1 chamada)
  3. Para cada event_id com resultado vazio:
       - Chama Sofascore /event/{event_id} para o estado final (marcador,
         FT status) e /event/{event_id}/statistics para totais (cantos,
         remates, cartoes totais ao fim).
       - Para cada mercado da linha, aplica a logica W/L:
         golos:   total golos finais > linha ?
         btts:    ambas marcaram ?
         cantos:  total cantos finais > linha ?
         cartoes: total amarelos finais > linha ?
         remates: total shots finais > linha ?
         remates_a_baliza: SoT finais > linha ?
       - Lucro: W → (odd-1)*stake_implicito=1 ; L → -1 ; empate push → 0
  4. Grava o CSV actualizado (atomico: temp file + rename).

Uso:
    python scripts/resolver_sugestoes_live.py

Idempotente: linhas ja com resultado nao sao re-resolvidas.
Dependencias: flaresolverr a correr (necessario para a Sofascore).
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apostas.utils.tempo import agora_utc  # noqa: E402

from sqlalchemy import select  # noqa: E402

from apostas.alertas import mega_cerebro  # noqa: E402
from apostas.ingestao import sofascore_live  # noqa: E402
from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.modelos import clv_tracker, historico_equipas, historico_jogadores  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.schema import ApostaRealLive  # noqa: E402

log = get_logger(__name__)

CSV_PATH = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"


def _puxar_resultado_final(event_id: int) -> dict | None:
    """Devolve dict com marcador final + totais finais, ou None se ainda nao acabou."""
    try:
        estado = sofascore_live.obter_estado_live(int(event_id))
    except FlaresolverrIndisponivel:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("event %s erro: %s", event_id, exc)
        return None
    if estado is None:
        return None
    # So resolvemos se o jogo acabou (minuto >=90 + status terminal) ou estado terminal explicito
    terminais = ("ft", "ended", "finished", "aet", "pen", "full time", "fim de jogo")
    desc = str(estado.estado or "").lower()
    acabado = estado.minuto >= 90 or any(t in desc for t in terminais) or estado.estado in (100, 110, 120)
    if not acabado:
        return None
    return {
        "golos_total": estado.marcador_casa + estado.marcador_fora,
        "golos_casa": estado.marcador_casa,
        "golos_fora": estado.marcador_fora,
        "btts": estado.marcador_casa > 0 and estado.marcador_fora > 0,
        "cantos_total": (estado.corners_casa or 0) + (estado.corners_fora or 0),
        "cards_amarelos_total": estado.yellow_cards_casa + estado.yellow_cards_fora,
        "cards_vermelhos_total": estado.red_cards_casa + estado.red_cards_fora,
        "shots_total": (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0),
        "shots_on_target_total": (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0),
    }


def _wl_por_mercado(mercado: str, lado: str, linha: str, res: dict) -> str | None:
    """Devolve 'W' / 'L' / 'push' ou None se nao conseguir determinar."""
    try:
        if mercado == "golos":
            total = res["golos_total"]
            linha_f = float(linha)
            if total == linha_f:
                return "push"
            if lado == "over":
                return "W" if total > linha_f else "L"
            return "W" if total < linha_f else "L"
        if mercado == "btts":
            return "W" if (lado == "sim") == res["btts"] else "L"
        if mercado == "cantos":
            total = res["cantos_total"]
            linha_f = float(linha)
            if total == linha_f:
                return "push"
            if lado == "over":
                return "W" if total > linha_f else "L"
            return "W" if total < linha_f else "L"
        if mercado == "cartoes":
            total = res["cards_amarelos_total"]
            linha_f = float(linha)
            if total == linha_f:
                return "push"
            if lado == "over":
                return "W" if total > linha_f else "L"
            return "W" if total < linha_f else "L"
        if mercado == "remates":
            total = res["shots_total"]
            linha_f = float(linha)
            if total == linha_f:
                return "push"
            if lado == "over":
                return "W" if total > linha_f else "L"
            return "W" if total < linha_f else "L"
        if mercado == "remates_a_baliza":
            total = res["shots_on_target_total"]
            linha_f = float(linha)
            if total == linha_f:
                return "push"
            if lado == "over":
                return "W" if total > linha_f else "L"
            return "W" if total < linha_f else "L"
        if mercado == "dupla":
            # 1X = casa ou empate; X2 = empate ou fora; 12 = casa ou fora
            c = res["golos_casa"]
            f = res["golos_fora"]
            if lado == "1x":
                return "W" if c >= f else "L"
            if lado == "x2":
                return "W" if f >= c else "L"
            if lado == "12":
                return "W" if c != f else "L"
        if mercado == "1x2":
            # Resultado final: lado = casa / empate / fora
            c = res["golos_casa"]
            f = res["golos_fora"]
            if lado == "casa":
                return "W" if c > f else "L"
            if lado == "fora":
                return "W" if f > c else "L"
            if lado == "empate":
                return "W" if c == f else "L"
        if mercado == "golo_equipa":
            # Regressao xG: equipa marca pelo menos 1 golo
            # lado = "marca_casa" ou "marca_fora"
            if lado == "marca_casa":
                return "W" if res["golos_casa"] >= 1 else "L"
            if lado == "marca_fora":
                return "W" if res["golos_fora"] >= 1 else "L"
    except (ValueError, KeyError):
        return None
    return None


def _lucro(resultado: str, odd: float, stake: float = 1.0) -> float:
    if resultado == "W":
        return round((odd - 1) * stake, 3)
    if resultado == "L":
        return -stake
    return 0.0  # push


def _resolver_apostas_reais_live(alert_ids: list[int], wl_por_alerta: dict,
                                   odd_por_alerta: dict) -> int:
    """Fecha as linhas de ``ApostaRealLive`` para os alert_ids resolvidos.

    Para cada alert_id resolvido:
      - resultado: 'W' / 'L' / 'push' (do CSV)
      - lucro: usa a stake e odd_executada do user (nao a odd recomendada)
      - clv: `clv_tracker.clv_live(odd_executada, odd_recomendada)`

    Idempotente: so toca linhas com resultado='pendente'.
    """
    if not alert_ids:
        return 0

    atualizadas = 0
    with abrir_sessao() as s:
        rows = s.scalars(
            select(ApostaRealLive)
            .where(ApostaRealLive.alert_id.in_(alert_ids))
            .where(ApostaRealLive.resultado == "pendente")
        ).all()
        for row in rows:
            wl = wl_por_alerta.get(row.alert_id)
            if wl is None:
                continue
            row.resultado = wl
            row.lucro = _lucro(wl, row.odd_executada, row.stake)
            # CLV: compara odd_executada vs odd_recomendada
            if row.odd_recomendada is not None:
                row.clv = clv_tracker.clv_live(row.odd_executada, row.odd_recomendada)
            atualizadas += 1
    return atualizadas


def main() -> None:
    if not CSV_PATH.exists():
        print(f"Sem CSV live em {CSV_PATH}")
        return

    with CSV_PATH.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        linhas = list(reader)
        colunas = reader.fieldnames or []

    # Garante que as colunas resultado/lucro/resolvido_em existem
    for extra in ("resultado", "lucro", "resolvido_em"):
        if extra not in colunas:
            colunas.append(extra)

    pendentes = [l for l in linhas if not l.get("resultado")]
    print(f"Total linhas: {len(linhas)}. Pendentes: {len(pendentes)}.")
    if not pendentes:
        return

    # Agrupa por event_id para 1 chamada Sofascore por jogo
    por_evento: dict[str, list] = {}
    for row in pendentes:
        eid = row.get("event_id") or ""
        if not eid:
            continue
        por_evento.setdefault(eid, []).append(row)

    print(f"{len(por_evento)} jogos distintos para resolver.\n")
    resolvidos_jogos = 0
    resolvidos_mercados = 0
    wl_por_alerta: dict[int, str] = {}
    for eid, linhas_grupo in por_evento.items():
        try:
            res = _puxar_resultado_final(int(eid))
        except FlaresolverrIndisponivel as exc:
            print(f"⚠ Flaresolverr: {exc}")
            print("Para continuar: `docker start flaresolverr`")
            break
        if res is None:
            print(f"  [skip] event {eid}: ainda nao acabou (ou nao encontrado)")
            continue
        h = linhas_grupo[0].get("home", "?")
        a = linhas_grupo[0].get("away", "?")
        print(f"  [OK]  event {eid}: {h} {res['golos_casa']}-{res['golos_fora']} {a}  "
              f"(cantos={res['cantos_total']} cards={res['cards_amarelos_total']} shots={res['shots_total']})")

        # Grava historico persistente por equipa (append-only). Puxa stats
        # por parte (1T/2T) do Sofascore e grava 2 linhas (casa + fora).
        # Idempotente — ja existe no-op.
        try:
            n_add = historico_equipas.gravar_jogo_terminado(int(eid))
            if n_add:
                print(f"        → historico equipas: +{n_add} linhas")
        except Exception as exc:  # noqa: BLE001
            log.warning("Historico event %s falhou: %s", eid, exc)

        # Grava historico persistente por jogador (append-only). Puxa
        # /lineups + /incidents e grava 1 linha por titular/suplente usado.
        # Idempotente.
        try:
            n_jog = historico_jogadores.gravar_jogadores_terminado(int(eid))
            if n_jog:
                print(f"        → historico jogadores: +{n_jog} linhas")
        except Exception as exc:  # noqa: BLE001
            log.warning("Historico jogadores event %s falhou: %s", eid, exc)

        # Fase D: resolve sinais pendentes do mega-cerebro para este jogo
        try:
            with abrir_sessao() as sess:
                n_mega = mega_cerebro.resolver_sinais_do_evento(sess, int(eid), res)
                if n_mega:
                    print(f"        → sinais mega-cerebro: {n_mega} resolvidos")
        except Exception as exc:  # noqa: BLE001
            log.warning("Resolver mega event %s falhou: %s", eid, exc)

        agora = agora_utc().isoformat(timespec="seconds")
        for row in linhas_grupo:
            wl = _wl_por_mercado(row["mercado"], row["lado"], row.get("linha") or "", res)
            if wl is None:
                continue
            row["resultado"] = wl
            try:
                odd = float(row.get("odd", 0))
            except ValueError:
                odd = 0.0
            row["lucro"] = str(_lucro(wl, odd))
            row["resolvido_em"] = agora
            resolvidos_mercados += 1
            # Memoriza wl por alert_id (para fechar ApostaRealLive abaixo)
            try:
                aid = int(row.get("alert_id") or 0)
                if aid:
                    wl_por_alerta[aid] = wl
            except (TypeError, ValueError):
                pass
        resolvidos_jogos += 1

    # Grava atomicamente
    tmp = CSV_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(linhas)
    tmp.replace(CSV_PATH)

    # Fecha as apostas reais live correspondentes (idempotente — so toca
    # linhas em estado 'pendente'). Calcula CLV = odd_executada / odd_rec - 1.
    if wl_por_alerta:
        try:
            n = _resolver_apostas_reais_live(
                list(wl_por_alerta.keys()), wl_por_alerta, {},
            )
            if n:
                print(f"  → {n} apostas reais live fechadas (resultado + CLV)")
        except Exception as exc:  # noqa: BLE001
            log.warning("Fecho ApostaRealLive falhou: %s", exc)

    print(f"\n{resolvidos_jogos} jogos resolvidos → {resolvidos_mercados} linhas W/L marcadas.")


if __name__ == "__main__":
    main()
