"""Health check — chamado pelo Task Scheduler cada 30 min.

Verifica flaresolverr + CSV live + BD + fichas. Se algo falhar, envia 1
mensagem Telegram. Se tudo OK, fica em silencio.

**Dedupe**: nao envia o mesmo problema duas vezes seguidas. Guarda o
hash dos problemas em `dados/health_check_estado.json`:
  - estado mudou (novo problema OU problema diferente) → envia
  - estado igual + <6h desde ultimo envio → silencio
  - estado igual + >=6h → re-envia como lembrete
  - problema resolveu (tudo_ok e estado anterior tinha problemas) →
    envia 1 mensagem "✅ Resolvido"

Uso:
  python scripts/health_check.py                   # dedupe activo
  python scripts/health_check.py --sempre          # imprime sempre
  python scripts/health_check.py --forcar          # ignora dedupe
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from apostas.alertas.health_check import (  # noqa: E402
    correr_health_check, formatar_telegram,
)
from apostas.utils.config import project_root  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402

_ESTADO_PATH = project_root() / "dados" / "health_check_estado.json"
_HORAS_RELEMBRAR = 6

# Horas (local Lisboa) em que o health check envia alertas. Fora desta
# janela e horario sem jogos interessantes (madrugada + manha), por isso
# qualquer alerta e silenciado. Problemas detectados sao sempre impressos
# no stdout, apenas nao vao para Telegram.
_HORA_ENVIO_INICIO = 16   # 16:00
_HORA_ENVIO_FIM = 2       # 02:00 (do dia seguinte)


def _dentro_janela_envio(agora_local=None) -> bool:
    """True se a hora local de Lisboa esta na janela 16:00-02:00."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        if agora_local is None:
            agora_local = datetime.now(ZoneInfo("Europe/Lisbon"))
        h = agora_local.hour
    except Exception:  # noqa: BLE001
        return True  # na duvida, deixa enviar
    # Janela 16:00-02:00 cruza a meia-noite: 16-23 OU 0-1
    return (h >= _HORA_ENVIO_INICIO) or (h < _HORA_ENVIO_FIM)


def _hash_problemas(problemas: list[str]) -> str:
    """Hash estavel dos problemas (ignora detalhes variaveis como minutos)."""
    # Normaliza — remove numeros variaveis que fazem o hash mudar sem razao
    import re
    normalizados = []
    for p in sorted(problemas):
        # Remove numeros (minutos, percentagens) para que "sem update ha 30 min"
        # e "sem update ha 45 min" tenham o mesmo hash
        n = re.sub(r"\d+", "N", p)
        normalizados.append(n)
    raw = "\n".join(normalizados).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _ler_estado() -> dict:
    if not _ESTADO_PATH.exists():
        return {}
    try:
        return json.loads(_ESTADO_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _escrever_estado(d: dict) -> None:
    _ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _ESTADO_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] nao gravou estado: {exc}")


def _deve_enviar(resultado, estado: dict, forcar: bool) -> tuple[bool, str]:
    """Decide se envia a mensagem. Devolve (envia, motivo)."""
    if forcar:
        return True, "flag --forcar"
    anterior_ok = estado.get("tudo_ok", True)
    anterior_hash = estado.get("hash")
    anterior_ts = estado.get("ultimo_envio")

    if resultado.tudo_ok and not anterior_ok:
        return True, "problemas resolvidos"
    if resultado.tudo_ok:
        return False, "tudo OK (silencio)"

    # Silencio fora da janela 16:00-02:00 (horas sem jogos interessantes)
    if not _dentro_janela_envio():
        return False, "fora da janela 16h-02h (silencio nocturno/matinal)"

    # Com problemas na janela activa
    h_atual = _hash_problemas(resultado.problemas)
    if anterior_ok or anterior_hash != h_atual:
        return True, "estado mudou"
    # Mesmo estado — espera 6h antes de relembrar
    if anterior_ts:
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(anterior_ts)
            if agora_utc() - ts >= timedelta(hours=_HORAS_RELEMBRAR):
                return True, f"relembra ({_HORAS_RELEMBRAR}h passaram)"
        except Exception:  # noqa: BLE001
            return True, "ts anterior invalido"
    return False, "mesmo problema, <6h desde ultimo"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sempre", action="store_true",
                        help="Imprime relatorio mesmo quando tudo OK.")
    parser.add_argument("--forcar", action="store_true",
                        help="Ignora dedupe e envia sempre.")
    args = parser.parse_args()

    r = correr_health_check()
    texto = formatar_telegram(r)
    print(texto)

    estado = _ler_estado()
    envia, motivo = _deve_enviar(r, estado, forcar=args.forcar)
    print(f"\n[dedupe] {motivo}")

    if r.tudo_ok and not args.sempre and not envia:
        return 0

    if envia:
        try:
            from apostas.alertas.telegram_cliente import cliente
            # Se resolveu, prefixar
            msg = texto
            if r.tudo_ok:
                msg = "✅ Sistema OK — problemas anteriores resolvidos."
            cliente().enviar(msg)
            print("\n[OK] Mensagem enviada para Telegram.")
            _escrever_estado({
                "tudo_ok": r.tudo_ok,
                "hash": _hash_problemas(r.problemas) if r.problemas else "",
                "ultimo_envio": agora_utc().isoformat(),
                "problemas": r.problemas,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERRO] Nao conseguiu enviar Telegram: {exc}")
            return 2
    return 1 if not r.tudo_ok else 0


if __name__ == "__main__":
    sys.exit(main())
