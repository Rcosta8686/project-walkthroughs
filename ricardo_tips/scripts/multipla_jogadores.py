"""Multipla de jogadores antes do jogo (remates, remates a baliza, amarelo).

Para os jogos das proximas horas com o onze ja conhecido no Sofascore: o
mini-cerebro de cada titular (a parte dele nos remates da equipa, amarelos por
90') x o normal das equipas (mini-cerebros) x os minutos que se espera que
jogue. Escolhe as selecoes mais provaveis e monta duas multiplas:

  - segura: poucas selecoes, todas com probabilidade alta;
  - maluca: mais selecoes, odd grande, para uns centimos.

Mostra a probabilidade de cada selecao e da multipla, e a odd justa (1/p): se a
casa pagar acima disso, ha valor. As previsoes ficam guardadas e sao medidas
de noite como as do live.

Uso:
  venv\\Scripts\\python.exe scripts\\multipla_jogadores.py
  venv\\Scripts\\python.exe scripts\\multipla_jogadores.py --horas 6 --telegram
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        getattr(_stream, "reconfigure")(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from apostas.modelos import jogadores  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


@dataclass
class Selecao:
    jogo: str
    event_id: int
    jogador: str
    player_id: int
    descricao: str
    p: float
    mercado: str = ""

    @property
    def odd_justa(self) -> float:
        return 1 / self.p if self.p > 0 else 0.0

    def texto(self) -> str:
        return (f"{self.jogador} ({self.jogo}) · {self.descricao} · {self.p * 100:.0f}% "
                f"· odd justa {self.odd_justa:.2f}")


_LINHAS = {"remates": (3, 2, 1), "sot": (2, 1), "cartao": (1,)}
_NOME_M = {"remates": "remates", "sot": "remates a baliza", "cartao": "amarelo"}


def candidatas(prevs: list[jogadores.PrevJogador], jogo: str, event_id: int,
               p_min: float = 0.30, p_max: float = 0.93) -> list[Selecao]:
    """Todas as linhas de cada jogador e mercado com p entre p_min e p_max
    (a segura escolhe as mais provaveis; a maluca as que pagam mais)."""
    out = []
    for pj in prevs:
        for alvo in _LINHAS[pj.mercado]:
            p = pj.p_total(alvo)
            if p_min <= p <= p_max:
                desc = ("ve amarelo" if pj.mercado == "cartao"
                        else f"{alvo}+ {_NOME_M[pj.mercado]}")
                out.append(Selecao(jogo, event_id, pj.nome, pj.id, desc, p, pj.mercado))
    return out


def montar(cands: list[Selecao], n: int, p_min: float, p_max: float = 1.0,
           por_jogo: int = 2, por_mercado: int | None = None,
           sem_jogadores: set[int] | None = None) -> list[Selecao]:
    """As mais provaveis entre p_min e p_max: cada jogador uma vez, no maximo
    `por_jogo` do mesmo jogo (menos dependencia) e `por_mercado` do mesmo
    mercado (variedade); `sem_jogadores` ficam de fora."""
    escolhidas: list[Selecao] = []
    jogadores_usados: set[int] = set(sem_jogadores or ())
    por_evento: dict[int, int] = {}
    por_m: dict[str, int] = {}
    for c in sorted(cands, key=lambda c: -c.p):
        if not p_min <= c.p <= p_max or c.player_id in jogadores_usados:
            continue
        if por_evento.get(c.event_id, 0) >= por_jogo:
            continue
        if por_mercado is not None and por_m.get(c.mercado, 0) >= por_mercado:
            continue
        escolhidas.append(c)
        jogadores_usados.add(c.player_id)
        por_evento[c.event_id] = por_evento.get(c.event_id, 0) + 1
        por_m[c.mercado] = por_m.get(c.mercado, 0) + 1
        if len(escolhidas) >= n:
            break
    return escolhidas


def texto_multipla(titulo: str, sel: list[Selecao]) -> str:
    if not sel:
        return f"*{titulo}*: sem selecoes suficientes agora."
    p = 1.0
    for s in sel:
        p *= s.p
    linhas = [f"*{titulo}* ({len(sel)} selecoes · {p * 100:.1f}% · odd justa {1 / p:.1f})"]
    linhas += [f"  {i}. {s.texto()}" for i, s in enumerate(sel, 1)]
    return "\n".join(linhas)


def _jogos(horas: float) -> list[dict]:
    from apostas.ingestao import sofascore_live
    agora = datetime.now(timezone.utc)
    ids = tuple(sofascore_live.LIGAS_SOFASCORE_IDS.values())
    out = []
    for dia in {agora.date().isoformat(),
                datetime.fromtimestamp(agora.timestamp() + horas * 3600, timezone.utc)
                .date().isoformat()}:
        for ev in sofascore_live.listar_jogos_do_dia(dia, ids_ligas=ids):
            ts = ev.get("startTimestamp") or 0
            codigo = (ev.get("status") or {}).get("code") or 0
            if codigo == 0 and agora.timestamp() <= ts <= agora.timestamp() + horas * 3600:
                out.append(ev)
    return sorted({e["id"]: e for e in out}.values(), key=lambda e: e.get("startTimestamp") or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horas", type=float, default=12.0, help="Jogos que comecam nas proximas N horas")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    from apostas.ingestao import sofascore_live
    from apostas.modelos import priors_equipas
    from apostas.utils.db import abrir_sessao, criar_schema
    criar_schema()
    med = jogadores.ler()
    jogos = _jogos(args.horas)
    print(f"{len(jogos)} jogos nas proximas {args.horas:.0f}h.\n")
    cands: list[Selecao] = []
    sem_onze = 0
    with abrir_sessao() as s:
        prior_pos = jogadores.priors_da_bd(s)
    for ev in jogos:
        eid = int(ev["id"])
        casa, fora = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        hora = datetime.fromtimestamp(ev.get("startTimestamp") or 0).strftime("%H:%M")
        jogo = f"{casa.get('shortName') or casa.get('name')}–{fora.get('shortName') or fora.get('name')} {hora}"
        lineups = sofascore_live._get_json(f"/event/{eid}/lineups")
        lineup = sofascore_live.jogadores_das_lineups(lineups or {})
        if not lineup:
            sem_onze += 1
            continue
        with abrir_sessao() as s:
            hist = jogadores.carregar_do_bd(s, [j["id"] for j in lineup], prior=prior_pos)
            pr = priors_equipas.carregar_do_bd(s, [int(casa["id"]), int(fora["id"])]).priors(
                int(casa["id"]), int(fora["id"]))
        rem = (pr.remates_casa, pr.remates_fora) if pr else (12.0, 12.0)
        sot = ((pr.sot_casa or 4.2, pr.sot_fora or 4.2) if pr else (4.2, 4.2))
        prevs = jogadores.prever_pre_jogo(lineup, {}, hist, rem, sot)
        for pj in prevs:
            pj.lam *= med.mult(pj.mercado, 0)
        try:
            with abrir_sessao() as s:   # medido de noite como o live
                jogadores.registar(s, eid, 0, prevs, {"casa": casa.get("name") or "",
                                                     "fora": fora.get("name") or ""})
        except Exception as exc:  # noqa: BLE001
            log.debug("Registo das previsoes de %s falhou: %s", eid, exc)
        cands += candidatas(prevs, jogo, eid)
    if sem_onze:
        print(f"({sem_onze} jogos ainda sem o onze no Sofascore: corre outra vez ~1h antes)\n")
    # Segura: provaveis mas nao triviais (65-85%; "1+ remate" a 95% nao paga nada).
    segura = montar(cands, n=4, p_min=0.65, p_max=0.85)
    # Maluca: linhas que pagam mais (40-65%), outros jogadores, mercados misturados.
    maluca = montar(cands, n=8, p_min=0.40, p_max=0.65, por_mercado=3,
                    sem_jogadores={s.player_id for s in segura})
    texto = "\n\n".join([texto_multipla("🎯 Multipla segura de jogadores", segura),
                         texto_multipla("🎰 Multipla maluca (uns centimos)", maluca),
                         "_Probabilidades do modelo; odd justa = 1/p. Se a casa pagar acima, "
                         "ha valor. Selecoes de jogadores no mesmo jogo nao sao independentes._"])
    print(texto)
    if args.telegram:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)
        print("\n[OK] Enviado para o Telegram.")


if __name__ == "__main__":
    main()
