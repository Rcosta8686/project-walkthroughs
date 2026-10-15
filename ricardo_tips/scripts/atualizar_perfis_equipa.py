"""Actualiza EquipaPerfil para equipas vistas nos jogos agendados de hoje.

Correr via Task Scheduler ou manualmente:
    venv\\Scripts\\python.exe scripts\\atualizar_perfis_equipa.py
    venv\\Scripts\\python.exe scripts\\atualizar_perfis_equipa.py --data 2026-10-18

Logica:
  1. Lista jogos agendados para a data (usa listar_jogos_do_dia).
  2. Extrai team_ids unicos (casa + fora).
  3. Para cada, chama atualizar_perfil(ttl 24h) que:
     - Se cache fresca -> salta
     - Senao -> puxa Sofascore ultimos 10 jogos + stats + grava

Custo: ~2 chamadas Sofascore por jogo * 10 jogos = 20 chamadas por equipa.
Com cache 24h e ~22 jogos por dia -> ~44 equipas = ~880 chamadas.
Flaresolverr aguenta — levar ~15 min no primeiro run, depois instantaneo.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import sofascore_live  # noqa: E402
from apostas.modelos import perfil_equipa  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None,
                        help="Data YYYY-MM-DD (default: hoje)")
    parser.add_argument("--ttl-horas", type=int, default=24,
                        help="TTL cache perfil (default 24h)")
    parser.add_argument("--max-equipas", type=int, default=100,
                        help="Max equipas a actualizar neste run")
    args = parser.parse_args()

    data_iso = args.data or date.today().isoformat()
    print(f"\n{'=' * 70}")
    print(f"  ACTUALIZAR PERFIS DE EQUIPA  —  {data_iso}")
    print("=" * 70)

    eventos = sofascore_live.listar_jogos_do_dia(data_iso, incluir_live=True)
    print(f"\n{len(eventos)} jogos agendados+live totais.")

    equipas: dict[int, tuple[str, str]] = {}  # team_id -> (nome, liga)
    for ev in eventos:
        liga = (ev.get("tournament") or {}).get("name", "")
        for lado in ("homeTeam", "awayTeam"):
            t = ev.get(lado) or {}
            tid = t.get("id")
            if tid and tid not in equipas:
                equipas[tid] = (t.get("name") or f"team_{tid}", liga)

    print(f"{len(equipas)} equipas unicas detectadas.")
    equipas_lista = list(equipas.items())[: args.max_equipas]
    print(f"A actualizar top {len(equipas_lista)} (max {args.max_equipas}).")
    print(f"TTL cache: {args.ttl_horas}h — perfis frescos sao saltados.\n")

    novos = 0
    saltados = 0
    falharam = 0
    for i, (tid, (nome, liga)) in enumerate(equipas_lista, 1):
        print(f"  [{i}/{len(equipas_lista)}] {nome[:40]:40s} ({liga[:30]:30s}) ...", end=" ")
        try:
            perfil = perfil_equipa.atualizar_perfil(tid, nome, liga,
                                                     ttl_horas=args.ttl_horas)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ ({exc})")
            falharam += 1
            continue
        if perfil.n_jogos_analisados == 0:
            print("⚠ 0 jogos")
            falharam += 1
            continue
        # Decide: foi recalculado ou veio do cache?
        from datetime import datetime, timedelta
        idade = datetime.utcnow() - perfil.data_atualizacao
        if idade < timedelta(minutes=5):
            print(f"✓ actualizado (n={perfil.n_jogos_analisados})")
            novos += 1
        else:
            print(f"→ cache (ultima: {idade})")
            saltados += 1

    print(f"\n{'=' * 70}")
    print(f"  RESUMO: {novos} actualizados, {saltados} cache, {falharam} falharam")
    print("=" * 70)


if __name__ == "__main__":
    main()
