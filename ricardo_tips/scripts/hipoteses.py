"""M21 — Hypothesis Ledger: CLI simples.

Uso:
    python scripts/hipoteses.py add "titulo" "descricao"
    python scripts/hipoteses.py list [--estado ativa]
    python scripts/hipoteses.py show 7
    python scripts/hipoteses.py resolver 7 confirmada \\
        --favor "ROI +12% em n=40" --final "Integrar como filtro"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.modelos import hipoteses  # noqa: E402


def _cmd_add(args) -> int:
    hid = hipoteses.adicionar(args.titulo, args.descricao)
    print(f"OK: hipotese #{hid} criada (estado=ativa).")
    return 0


def _cmd_list(args) -> int:
    resumos = hipoteses.listar(estado=args.estado)
    print(hipoteses.formatar_lista_terminal(resumos))
    return 0


def _cmd_show(args) -> int:
    h = hipoteses.obter(args.id)
    if h is None:
        print(f"Hipotese #{args.id} nao encontrada.")
        return 1
    print(hipoteses.formatar_detalhe(h))
    return 0


def _cmd_resolver(args) -> int:
    ok = hipoteses.resolver(
        args.id, args.estado,
        evidencia_a_favor=args.favor,
        evidencia_contra=args.contra,
        decisao_final=args.final,
    )
    if ok:
        print(f"OK: hipotese #{args.id} marcada como {args.estado}.")
        return 0
    print(f"Hipotese #{args.id} nao encontrada.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="M21 — Hypothesis Ledger.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Criar nova hipotese.")
    p_add.add_argument("titulo")
    p_add.add_argument("descricao")
    p_add.set_defaults(func=_cmd_add)

    p_list = sub.add_parser("list", help="Listar hipoteses.")
    p_list.add_argument("--estado", choices=hipoteses.ESTADOS_VALIDOS, default=None)
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="Ver 1 hipotese em detalhe.")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=_cmd_show)

    p_res = sub.add_parser("resolver", help="Resolver hipotese.")
    p_res.add_argument("id", type=int)
    p_res.add_argument("estado", choices=("confirmada", "refutada", "inconclusiva"))
    p_res.add_argument("--favor", default=None, help="Evidencia a favor.")
    p_res.add_argument("--contra", default=None, help="Evidencia contra.")
    p_res.add_argument("--final", default=None, help="Decisao final.")
    p_res.set_defaults(func=_cmd_resolver)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
