"""Deteca jogos duplicados com orientacao invertida.

Problema: se football-data gravou "Elche vs Celta" (Elche casa) e a Odds
API nos chegou com "Celta vs Elche" (Celta casa), o lookup antigo nao
encontrava match e criava um segundo registo com os lados invertidos.

Este script lista esses pares (jogo original + duplicado invertido).
Nao apaga nada — mostra-te o que ha. Se confirmares que sao mesmo
duplicados, podes limpar manualmente com o SQL que o script sugere.

A nova logica em odds_api._processar_jogo ja previne futuros duplicados.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import Equipa, Jogo, OddsCorrentes  # noqa: E402


def main() -> None:
    with abrir_sessao() as s:
        todos = s.scalars(select(Jogo).order_by(Jogo.data_utc)).all()
        print(f"\nTotal jogos em DB: {len(todos)}")

        duplicados = []
        indexados: dict[tuple, Jogo] = {}
        for j in todos:
            # chave: (liga, data truncada em dia, {par_equipas})
            key = (
                j.liga_id,
                j.data_utc.strftime("%Y-%m-%d"),
                frozenset((j.casa_id, j.fora_id)),
            )
            if key in indexados:
                outro = indexados[key]
                if (outro.casa_id, outro.fora_id) == (j.fora_id, j.casa_id):
                    duplicados.append((outro, j))
                continue
            indexados[key] = j

        if not duplicados:
            print("Zero duplicados invertidos. BD limpa.")
            return

        print(f"\n⚠ {len(duplicados)} pares duplicados com orientacao invertida:\n")
        for primeiro, segundo in duplicados:
            c1 = s.get(Equipa, primeiro.casa_id).nome
            f1 = s.get(Equipa, primeiro.fora_id).nome
            c2 = s.get(Equipa, segundo.casa_id).nome
            f2 = s.get(Equipa, segundo.fora_id).nome
            odds1 = s.scalar(
                select(OddsCorrentes.id).where(OddsCorrentes.jogo_id == primeiro.id)
            )
            odds2 = s.scalar(
                select(OddsCorrentes.id).where(OddsCorrentes.jogo_id == segundo.id)
            )
            print(f"  Data: {primeiro.data_utc.strftime('%Y-%m-%d %H:%M')}")
            print(f"    jogo_id={primeiro.id}: {c1} vs {f1}  "
                  f"(estado={primeiro.estado}, odds={'si' if odds1 else 'nao'})")
            print(f"    jogo_id={segundo.id}: {c2} vs {f2}  "
                  f"(estado={segundo.estado}, odds={'si' if odds2 else 'nao'})")
            # Heuristica: o duplicado "errado" normalmente e o que foi criado
            # pela Odds API (estado=agendado + odds correntes mas sem resultado)
            # e o original e o football-data (estado=encerrado ou tem resultado).
            if primeiro.estado == "agendado" and segundo.estado != "agendado":
                errado, correcto = primeiro, segundo
            elif segundo.estado == "agendado" and primeiro.estado != "agendado":
                errado, correcto = segundo, primeiro
            else:
                # Ambos agendados: fica a cargo do utilizador decidir
                errado, correcto = None, None
            if errado is not None:
                print(f"    → sugestao: ficar com jogo_id={correcto.id}, "
                      f"apagar jogo_id={errado.id}")
            else:
                print(f"    → ambos sao futuros, verifica manualmente qual tem orientacao real")
            print()

        print("Para apagar um jogo e as suas odds, abre a BD com sqlite3 e corre:")
        print("  DELETE FROM odds_correntes WHERE jogo_id = <ID>;")
        print("  DELETE FROM odds_fecho WHERE jogo_id = <ID>;")
        print("  DELETE FROM sugestoes WHERE jogo_id = <ID>;")
        print("  DELETE FROM jogos WHERE id = <ID>;")


if __name__ == "__main__":
    main()
