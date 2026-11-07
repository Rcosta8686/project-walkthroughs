"""Copia de seguranca diaria dos dados (corre no incremento das 00:15).

A BD (fichas das equipas, momentos dos jogos ao vivo, projeccoes) e os
ficheiros de dados/tracking (alertas, resultados, registo das dicas enviadas,
correccoes aprendidas) sao meses de estatistica acumulada. Todas as noites:

  dados/backups/AAAA-MM-DD/
      ricardo_tips.db.zip     (copia consistente via API de backup do SQLite,
                               mesmo com o scanner a escrever; comprimida)
      *.csv, *.json           (dados/tracking)

Mantem as ultimas MANTER_DIAS noites e apaga as mais antigas.
"""
from __future__ import annotations

import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

MANTER_DIAS = 7


@dataclass
class ResultadoBackup:
    pasta: Path
    ficheiros: int
    tamanho_mb: float
    apagados: int

    def texto(self) -> str:
        return (f"💾 Copia de seguranca: {self.ficheiros} ficheiros, {self.tamanho_mb:.0f} MB em "
                f"{self.pasta}" + (f" ({self.apagados} copias antigas apagadas)"
                                   if self.apagados else ""))


def _copiar_bd(origem: Path, destino_zip: Path) -> None:
    tmp = destino_zip.with_suffix("")  # ricardo_tips.db
    src = sqlite3.connect(str(origem))
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with zipfile.ZipFile(destino_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, arcname=origem.name)
    tmp.unlink()


def fazer_backup(agora: datetime | None = None, raiz: Path | None = None,
                 bd: Path | None = None) -> ResultadoBackup:
    from apostas.utils.config import db_path, project_root
    agora = agora or datetime.now()
    raiz = raiz or project_root()
    bd = bd or db_path()
    base = raiz / "dados" / "backups"
    pasta = base / f"{agora:%Y-%m-%d}"
    pasta.mkdir(parents=True, exist_ok=True)

    n = 0
    if bd.exists():
        _copiar_bd(bd, pasta / f"{bd.name}.zip")
        n += 1
    tracking = raiz / "dados" / "tracking"
    if tracking.exists():
        for f in tracking.iterdir():
            if (f.is_file() and f.suffix in (".csv", ".json")
                    and ".backup-" not in f.name and ".irreconheciveis-" not in f.name):
                shutil.copy2(f, pasta / f.name)
                n += 1

    apagados = 0
    limite = (agora - timedelta(days=MANTER_DIAS)).date()
    for d in base.iterdir():
        try:
            dia = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d.is_dir() and dia <= limite:
            shutil.rmtree(d, ignore_errors=True)
            apagados += 1
    tamanho = sum(f.stat().st_size for f in pasta.iterdir() if f.is_file()) / 1e6
    return ResultadoBackup(pasta=pasta, ficheiros=n, tamanho_mb=tamanho, apagados=apagados)
