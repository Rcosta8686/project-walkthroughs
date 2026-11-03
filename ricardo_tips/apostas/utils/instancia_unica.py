"""Garante uma unica instancia de um script: a mais nova mata as antigas.

No Windows, Stop-ScheduledTask mata o launcher do venv mas pode deixar o
processo Python filho vivo. Cada restart (watchdog, Task Scheduler) junta
mais um scanner orfao, e varios scanners na mesma sessao do flaresolverr
dao HTTP 500, alertas em duplicado e "encravamentos".

A instancia nova ganha porque quem a lancou (watchdog ou utilizador)
assume que a antiga esta encravada.
"""
from __future__ import annotations

import base64
import os
import signal
import subprocess
import sys
from typing import Callable

from apostas.utils.logger import get_logger

log = get_logger(__name__)

Processo = tuple[int, str]  # (pid, linha de comando)

_PS_LISTAR = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
    "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
)


def _parse_saida_powershell(texto: str) -> list[Processo]:
    procs: list[Processo] = []
    for linha in texto.splitlines():
        pid_str, sep, cmd = linha.rstrip("\r\n").partition("\t")
        pid_str = pid_str.strip()
        if not sep or not pid_str.isdigit():
            continue
        procs.append((int(pid_str), cmd))
    return procs


def _listar_windows() -> list[Processo]:
    # -EncodedCommand evita problemas de aspas ao passar o script ao PowerShell.
    encoded = base64.b64encode(_PS_LISTAR.encode("utf-16-le")).decode("ascii")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return _parse_saida_powershell(out)


def _listar_posix() -> list[Processo]:
    procs: list[Processo] = []
    for nome in os.listdir("/proc"):
        if not nome.isdigit():
            continue
        try:
            with open(f"/proc/{nome}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if cmd:
            procs.append((int(nome), cmd))
    return procs


def _matar(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    else:
        os.kill(pid, signal.SIGTERM)


def garantir_instancia_unica(
    marcador: str,
    *,
    listar: Callable[[], list[Processo]] | None = None,
    matar: Callable[[int], None] | None = None,
    pid: int | None = None,
    ppid: int | None = None,
) -> list[int]:
    """Mata outros processos cuja linha de comando contem `marcador`.

    Exclui o proprio processo e o pai (no Windows o pai e o launcher do venv,
    que tem a mesma linha de comando). Devolve os PIDs mortos. Nunca levanta:
    se nao conseguir listar processos, segue sem matar nada.
    """
    if listar is None:
        listar = _listar_windows if sys.platform == "win32" else _listar_posix
    matar = matar or _matar
    proprio = {pid if pid is not None else os.getpid(),
               ppid if ppid is not None else os.getppid()}

    try:
        procs = listar()
    except Exception as exc:  # noqa: BLE001
        log.warning("Instancia unica: nao consegui listar processos (%s)", exc)
        return []

    mortos: list[int] = []
    for p, cmd in procs:
        if p in proprio or marcador not in cmd:
            continue
        try:
            matar(p)
            mortos.append(p)
        except Exception as exc:  # noqa: BLE001
            log.warning("Instancia unica: falhou matar PID %s (%s)", p, exc)
    if mortos:
        log.warning("Instancia unica: terminei %d processo(s) antigo(s) de %s: %s",
                    len(mortos), marcador, mortos)
    return mortos
