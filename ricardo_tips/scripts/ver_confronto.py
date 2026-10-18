"""Analisa um confronto entre 2 equipas, com padroes cruzados.

Modos:
    # Nome de ambas as equipas (procura em EstatisticaEquipaJogo)
    python scripts/ver_confronto.py "AS Roma" "Genoa"

    # Ids directos
    python scripts/ver_confronto.py --id-casa 2702 --id-fora 2713

    # Com estado live (score + minuto)
    python scripts/ver_confronto.py "AS Roma" "Genoa" --score 1-0 --minuto 56

    # Guarda em docs/confrontos/{casa}_vs_{fora}.md
    python scripts/ver_confronto.py "AS Roma" "Genoa" --guardar

Dependencia: popular_cerebros.py ja correu para as 2 equipas.
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

from apostas.alertas.confronto import (  # noqa: E402
    analisar_confronto, formatar_confronto_markdown,
)
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.db import abrir_sessao  # noqa: E402
from apostas.utils.schema import EstatisticaEquipaJogo  # noqa: E402


def _slug(nome: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_")
    return s or "equipa"


def _procurar_id(nome_query: str) -> int | None:
    """Devolve o team_id mais provavel. None se ambiguo ou nao encontrado."""
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
    candidatos = [(r[0], r[1], r[2]) for r in rows if q in (r[1] or "").lower()]
    if not candidatos:
        return None
    if len(candidatos) > 1:
        print(f"\n⚠ Mais do que uma equipa com '{nome_query}':")
        for cid, cnome, cjogos in candidatos:
            print(f"  --id {cid}  ({cnome}, {cjogos} jogos)")
        return None
    return candidatos[0][0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("casa", nargs="?", help="Nome da equipa da casa.")
    parser.add_argument("fora", nargs="?", help="Nome da equipa de fora.")
    parser.add_argument("--id-casa", type=int, default=None)
    parser.add_argument("--id-fora", type=int, default=None)
    parser.add_argument("--score", default=None,
                        help="Score 'X-Y' se jogo em curso.")
    parser.add_argument("--minuto", type=int, default=None)
    parser.add_argument("--guardar", action="store_true")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    if args.id_casa is None:
        if not args.casa:
            parser.error("Da-me 'casa' + 'fora' ou --id-casa + --id-fora.")
        id_casa = _procurar_id(args.casa)
        if id_casa is None:
            print(f"\n⚠ Equipa de casa '{args.casa}' nao encontrada.")
            sys.exit(1)
    else:
        id_casa = args.id_casa

    if args.id_fora is None:
        if not args.fora:
            parser.error("Da-me 'fora' tambem.")
        id_fora = _procurar_id(args.fora)
        if id_fora is None:
            print(f"\n⚠ Equipa de fora '{args.fora}' nao encontrada.")
            sys.exit(1)
    else:
        id_fora = args.id_fora

    score_c = score_f = None
    if args.score:
        m = re.match(r"(\d+)-(\d+)", args.score)
        if m:
            score_c = int(m.group(1))
            score_f = int(m.group(2))

    res = analisar_confronto(
        id_casa, id_fora,
        minuto=args.minuto, score_casa=score_c, score_fora=score_f,
        n_jogos_ficha=args.n,
    )
    if res is None:
        print(f"\n⚠ Nao consigo gerar confronto — pelo menos 1 das equipas "
              f"tem <3 jogos no historico.")
        sys.exit(1)

    md = formatar_confronto_markdown(res)
    if args.guardar:
        pasta = project_root() / "docs" / "confrontos"
        pasta.mkdir(parents=True, exist_ok=True)
        fname = f"{_slug(res.nome_casa)}_vs_{_slug(res.nome_fora)}.md"
        destino = pasta / fname
        destino.write_text(md, encoding="utf-8")
        print(f"\n✅ Gravado em {destino}")
    else:
        print("\n" + md)


if __name__ == "__main__":
    main()
