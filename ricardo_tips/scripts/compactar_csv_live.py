"""Compactacao do sugestoes_live_v2.csv.

Com o scanner a correr 24/7 em todas as ligas, o CSV cresce ~1k linhas
por dia. Em 6 meses chega a 200k+, o que torna leitura dos relatorios
mais lenta e aumenta risco de corrupcao.

Estrategia: mantem detalhado os ultimos N dias (default 90). Linhas mais
antigas sao movidas para `sugestoes_live_v2_arquivo.csv` (append-only).

Nao perde dados — apenas separa recente de antigo.

Uso:
  python scripts/compactar_csv_live.py                   # default 90 dias
  python scripts/compactar_csv_live.py --manter 180
  python scripts/compactar_csv_live.py --dry-run         # so mostra counts
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.utils.config import project_root  # noqa: E402


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manter", type=int, default=90,
                        help="Dias de detalhe a manter (default 90).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nao escreve, so imprime contagens.")
    args = parser.parse_args()

    csv_path = project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv"
    arquivo_path = csv_path.with_name("sugestoes_live_v2_arquivo.csv")

    if not csv_path.exists():
        print(f"CSV live nao existe em {csv_path}")
        return

    agora = datetime.utcnow()
    limiar = agora - timedelta(days=args.manter)
    print(f"\n╔ Compactar — manter linhas desde {limiar:%Y-%m-%d} ╗\n")

    with csv_path.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        colunas = reader.fieldnames or []
        linhas = list(reader)

    recentes = []
    antigas = []
    sem_data = []
    for row in linhas:
        ts = _parse_dt(row.get("ts_scan") or "")
        if ts is None:
            sem_data.append(row)
        elif ts >= limiar:
            recentes.append(row)
        else:
            antigas.append(row)

    print(f"  Total linhas: {len(linhas)}")
    print(f"  Recentes (>= {limiar:%Y-%m-%d}): {len(recentes)}")
    print(f"  Antigas: {len(antigas)}  → arquivo")
    print(f"  Sem data (mantidas no principal): {len(sem_data)}")

    if args.dry_run:
        print("\n[dry-run] Nada escrito.")
        return

    if not antigas:
        print("\nNada a arquivar — CSV ja compacto.")
        return

    # Append antigas ao arquivo (cria com header se nao existe)
    arquivo_novo = not arquivo_path.exists()
    with arquivo_path.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
        if arquivo_novo:
            writer.writeheader()
        writer.writerows(antigas)
    print(f"  → {len(antigas)} linhas arquivadas em {arquivo_path.name}")

    # Reescreve o principal com recentes + sem_data (atomico via tmp)
    tmp = csv_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=colunas, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(recentes + sem_data)
    tmp.replace(csv_path)
    print(f"  → CSV principal reescrito com {len(recentes) + len(sem_data)} linhas")

    print("\n✅ Compactacao OK.")


if __name__ == "__main__":
    main()
