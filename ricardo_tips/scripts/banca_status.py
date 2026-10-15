"""Mostra o estado actual da banca + decide se podemos continuar a apostar.

Correr:
    venv\\Scripts\\python.exe scripts\\banca_status.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.modelos.banca import (  # noqa: E402
    BANCA_INICIAL_CHF_DEFAULT,
    STAKE_FLAT_CHF_DEFAULT,
    STOP_LOSS_DIA_PCT,
    STOP_LOSS_SEMANA_PCT,
    estado_banca_actual,
)


def main() -> None:
    e = estado_banca_actual()
    sinal = "✅" if e.pode_apostar else "⛔"
    print(f"\n{'=' * 60}")
    print(f"  {sinal} ESTADO BANCA  (Ricardo Tips)")
    print(f"{'=' * 60}")
    print(f"  Saldo atual:         CHF {e.saldo_atual:7.2f}")
    print(f"  Perda hoje:          CHF {e.perda_dia:7.2f}  (stop-loss {STOP_LOSS_DIA_PCT*100:.0f}%)")
    print(f"  Perda semana:        CHF {e.perda_semana:7.2f}  (stop-loss {STOP_LOSS_SEMANA_PCT*100:.0f}%)")
    print(f"  Apostas abertas:     {e.apostas_abertas_num} × CHF total {e.apostas_abertas_stake:.2f}")
    print(f"  Stake por sinal:     CHF {STAKE_FLAT_CHF_DEFAULT:.2f} (flat)")
    print(f"\n  Decisao: {'podemos apostar' if e.pode_apostar else 'STOP — ' + e.motivo}")
    print(f"{'=' * 60}\n")
    sys.exit(0 if e.pode_apostar else 2)


if __name__ == "__main__":
    main()
