"""O ritmo "normal" de um jogo a partir dos mini-cerebros das duas equipas.

O modelo live mistura o ritmo observado no jogo com o ritmo normal (ritmo
suavizado). Por defeito o normal e generico (4 cartoes, 10.5 cantos, 2.6 golos,
12 remates por equipa); aqui sai dos ultimos N_JOGOS de cada equipa na BD:

  - golos/cantos/cartoes: total desse mercado nos jogos de cada equipa (dela +
    do adversario), media das duas equipas;
  - remates de cada equipa: media entre o que ela remata e o que o adversario
    costuma deixar rematar.

Cada media e puxada para o valor generico quando ha poucos jogos (K_ENCOLHIMENTO
jogos ficticios). Com `antes` so contam jogos anteriores a essa data (para medir
nos jogos passados sem usar o proprio resultado).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

N_JOGOS = 20
K_ENCOLHIMENTO = 5
GENERICO = {"golos": 2.6, "cantos": 10.5, "cartoes": 4.0, "remates": 12.0}


@dataclass
class PriorsJogo:
    golos: float
    cantos: float
    cartoes: float
    remates_casa: float   # por 90 minutos
    remates_fora: float
    n_casa: int           # jogos usados de cada equipa
    n_fora: int

    def kwargs(self) -> dict:
        """Argumentos para live_predictor.prever."""
        return {"lam_golos_pre": self.golos, "lam_cantos_pre": self.cantos,
                "lam_cartoes_pre": self.cartoes,
                "remates_pre": (self.remates_casa, self.remates_fora)}


def _v(row, attr: str) -> float | None:
    x = getattr(row, attr, None)
    return float(x) if x is not None else None


class HistoricoEquipas:
    """Jogos por equipa (com a linha do adversario) para calcular priors."""

    def __init__(self, linhas):
        por_evento: dict[int, list] = {}
        for r in linhas:
            por_evento.setdefault(int(r.sofascore_event_id), []).append(r)
        self._jogos: dict[int, list[tuple[datetime, object, object | None]]] = {}
        for rows in por_evento.values():
            for r in rows:
                opp = next((o for o in rows if o.sofascore_team_id != r.sofascore_team_id), None)
                self._jogos.setdefault(int(r.sofascore_team_id), []).append(
                    (r.data_jogo, r, opp))
        for lista in self._jogos.values():
            lista.sort(key=lambda x: x[0], reverse=True)

    def jogos(self, equipa: int, antes: datetime | None = None, n: int = N_JOGOS):
        lista = self._jogos.get(int(equipa), [])
        if antes is not None:
            lista = [j for j in lista if j[0] < antes]
        return lista[:n]

    def priors(self, casa_id: int | None, fora_id: int | None,
               antes: datetime | None = None, n: int = N_JOGOS) -> PriorsJogo | None:
        if casa_id is None or fora_id is None:
            return None
        jc, jf = self.jogos(casa_id, antes, n), self.jogos(fora_id, antes, n)
        if not jc and not jf:
            return None

        def media(jogos, valor, generico: float) -> float:
            xs = [x for _, own, opp in jogos if (x := valor(own, opp)) is not None]
            return (sum(xs) + K_ENCOLHIMENTO * generico) / (len(xs) + K_ENCOLHIMENTO)

        def total(attr):
            def f(own, opp):
                a, b = _v(own, attr), _v(opp, attr) if opp is not None else None
                return a + b if a is not None and b is not None else None
            return f

        def golos(own, _opp):
            return float(own.golos_marcados or 0) + float(own.golos_sofridos or 0)

        def remates_feitos(own, _opp):
            return _v(own, "remates")

        def remates_sofridos(_own, opp):
            return _v(opp, "remates") if opp is not None else None

        g = GENERICO
        rem = g["remates"]
        return PriorsJogo(
            golos=(media(jc, golos, g["golos"]) + media(jf, golos, g["golos"])) / 2,
            cantos=(media(jc, total("cantos"), g["cantos"])
                    + media(jf, total("cantos"), g["cantos"])) / 2,
            cartoes=(media(jc, total("cards_amarelos"), g["cartoes"])
                     + media(jf, total("cards_amarelos"), g["cartoes"])) / 2,
            remates_casa=(media(jc, remates_feitos, rem) + media(jf, remates_sofridos, rem)) / 2,
            remates_fora=(media(jf, remates_feitos, rem) + media(jc, remates_sofridos, rem)) / 2,
            n_casa=len(jc), n_fora=len(jf),
        )


def carregar_do_bd(sess, equipas: list[int], antes: datetime | None = None,
                   n: int = N_JOGOS) -> HistoricoEquipas:
    """Historico so destas equipas (os seus ultimos n jogos + adversarios)."""
    from sqlalchemy import select

    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    eventos: set[int] = set()
    for eq in equipas:
        q = select(EEJ.sofascore_event_id).where(EEJ.sofascore_team_id == int(eq))
        if antes is not None:
            q = q.where(EEJ.data_jogo < antes)
        eventos |= set(sess.scalars(q.order_by(EEJ.data_jogo.desc()).limit(n)).all())
    if not eventos:
        return HistoricoEquipas([])
    return HistoricoEquipas(sess.scalars(
        select(EEJ).where(EEJ.sofascore_event_id.in_(list(eventos)))).all())
