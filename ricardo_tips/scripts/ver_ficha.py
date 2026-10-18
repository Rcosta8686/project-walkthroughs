"""Gera e mostra a ficha de uma equipa a partir do historico persistente.

Modos:
    # Pelo nome (procura em EstatisticaEquipaJogo.nome_equipa)
    python scripts/ver_ficha.py "AS Roma"
    python scripts/ver_ficha.py "Brighton"

    # Pelo sofascore_team_id directo
    python scripts/ver_ficha.py --id 30

    # Grava em docs/equipas/{nome}.md para abrir no Obsidian/VSCode
    python scripts/ver_ficha.py "AS Roma" --guardar

Dependencias: historico ja populado (correr popular_cerebros.py primeiro).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import select, func  # noqa: E402

from apostas.relatorios.ficha_equipa import gerar_ficha, ficha_markdown  # noqa: E402
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402


def _slug(nome: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_")
    return s or "equipa"


def _procurar_team_id(nome_query: str) -> list[tuple[int, str, int]]:
    """Devolve lista [(team_id, nome, n_jogos)] correspondendo ao query.

    Case-insensitive substring match.
    """
    with abrir_sessao() as s:
        rows = s.execute(
            select(
                EstatisticaEquipaJogo.sofascore_team_id,
                EstatisticaEquipaJogo.nome_equipa,
                func.count(EstatisticaEquipaJogo.id),
            )
            .group_by(
                EstatisticaEquipaJogo.sofascore_team_id,
                EstatisticaEquipaJogo.nome_equipa,
            )
        ).all()
    q = nome_query.lower()
    return [(r[0], r[1], r[2]) for r in rows if q in (r[1] or "").lower()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nome", nargs="?", help="Nome (ou parte) da equipa.")
    parser.add_argument("--id", type=int, default=None,
                        help="sofascore_team_id directo.")
    parser.add_argument("--n", type=int, default=20,
                        help="Jogos a usar (default 20).")
    parser.add_argument("--guardar", action="store_true",
                        help="Grava docs/equipas/{nome}.md em vez de imprimir.")
    args = parser.parse_args()

    if args.id is None and not args.nome:
        parser.error("Da-me um nome (ex: 'AS Roma') ou um --id.")

    if args.id is not None:
        team_id = args.id
    else:
        candidatos = _procurar_team_id(args.nome)
        if not candidatos:
            print(f"\n⚠ Nenhuma equipa com '{args.nome}' no historico.")
            print("Correr primeiro: scripts\\popular_cerebros.py")
            sys.exit(1)
        if len(candidatos) > 1:
            print(f"\nMais do que uma equipa corresponde a '{args.nome}':\n")
            for cid, cnome, cjogos in candidatos:
                print(f"  --id {cid}  ({cnome}, {cjogos} jogos)")
            print("\nUsa --id <N> para escolher.")
            sys.exit(1)
        team_id = candidatos[0][0]
        print(f"\nFicha de: {candidatos[0][1]} (id={team_id}, "
              f"{candidatos[0][2]} jogos no historico)")

    ficha = gerar_ficha(team_id, n_jogos=args.n)
    if ficha is None:
        print(f"\n⚠ So menos de 3 jogos para team_id={team_id}. "
              f"Correr popular_cerebros.py primeiro para esta equipa.")
        sys.exit(1)

    md = ficha_markdown(ficha)
    if args.guardar:
        pasta = project_root() / "docs" / "equipas"
        pasta.mkdir(parents=True, exist_ok=True)
        fname = _slug(ficha.nome) + ".md"
        destino = pasta / fname
        destino.write_text(md, encoding="utf-8")
        print(f"\n✅ Gravado em {destino}")
    else:
        print("\n" + md)


if __name__ == "__main__":
    main()
