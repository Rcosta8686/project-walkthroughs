"""Health check do sistema Ricardo Tips.

Verifica em cascata:
  1. Flaresolverr container responde.
  2. CSV live foi actualizado nos ultimos N minutos (scanner vivo).
  3. BD sqlite nao esta corrompida (query trivial).

Devolve um relatorio textual e um `tudo_ok` boolean. O script
chamador decide se envia por Telegram (so quando algo esta mal, para
nao gastar atencao).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


# Limiar: se o CSV live nao for actualizado em >N minutos, scanner
# provavelmente morreu/congelou.
MINUTOS_SEM_CSV_ALERTA = 30


@dataclass
class ResultadoHealthCheck:
    """Resultado agregado."""
    tudo_ok: bool = True
    problemas: list[str] = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)


def _check_flaresolverr(url: str = None) -> tuple[bool, str]:
    """Faz GET ao flaresolverr. Devolve (ok, mensagem_erro)."""
    try:
        from apostas.ingestao._flaresolverr import _url_flaresolverr
        import urllib.request
        import json as _json

        base = url or _url_flaresolverr()
        # Flaresolverr responde a POST /v1 com cmd=sessions.list ou GET /
        req = urllib.request.Request(
            base,
            data=_json.dumps({"cmd": "sessions.list"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return True, ""
            return False, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _check_csv_vivo(
    csv_path: Path, minutos_limite: int = MINUTOS_SEM_CSV_ALERTA,
    agora: datetime | None = None,
) -> tuple[bool, str, dict]:
    """CSV foi tocado nos ultimos N minutos?"""
    agora = agora or datetime.utcnow()
    if not csv_path.exists():
        return False, "CSV live nao existe", {}
    try:
        mtime = datetime.utcfromtimestamp(csv_path.stat().st_mtime)
    except OSError as exc:
        return False, f"stat falhou: {exc}", {}
    delta_min = (agora - mtime).total_seconds() / 60
    detalhes = {
        "csv_mtime": mtime.isoformat(timespec="seconds"),
        "minutos_desde_update": round(delta_min, 1),
    }
    if delta_min > minutos_limite:
        return (
            False,
            f"CSV live sem update ha {delta_min:.0f} min "
            f"(>{minutos_limite}) — scanner pode estar parado",
            detalhes,
        )
    return True, "", detalhes


def _check_bd(db_path: Path) -> tuple[bool, str]:
    """Query trivial ao sqlite para detectar corrupcao."""
    if not db_path.exists():
        return False, f"BD nao existe em {db_path}"
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"BD falhou: {exc}"


def _check_fichas_desactualizadas(min_jogos: int = 5) -> tuple[bool, str, dict]:
    """Alerta se mais de 50% das equipas de ligas prioritarias tem ficha
    com < min_jogos. Se o populator parou, isto detecta.

    Devolve (ok, erro, detalhes).
    """
    try:
        from sqlalchemy import select, func
        from apostas.utils.db import abrir_sessao
        from apostas.utils.schema import EstatisticaEquipaJogo
    except Exception as exc:  # noqa: BLE001
        return True, "", {"fichas_check": f"nao disponivel: {exc}"}

    try:
        with abrir_sessao() as sess:
            total_jogos = sess.execute(
                select(func.count(EstatisticaEquipaJogo.id))
            ).scalar_one() or 0
            # Equipas com pelo menos min_jogos de historico
            team_counts = sess.execute(
                select(EstatisticaEquipaJogo.sofascore_team_id,
                       func.count(EstatisticaEquipaJogo.id).label("n"))
                .group_by(EstatisticaEquipaJogo.sofascore_team_id)
            ).all()
            fracas = sum(1 for _, n in team_counts if n < min_jogos)
            total_equipas = len(team_counts)
    except Exception as exc:  # noqa: BLE001
        return True, "", {"fichas_check": f"query falhou: {exc}"}

    det = {
        "fichas_total_jogos": total_jogos,
        "fichas_total_equipas": total_equipas,
        "fichas_fracas": fracas,
    }
    if total_equipas == 0:
        return False, "fichas vazias — popular_cerebros nunca correu", det
    pct_fracas = fracas / total_equipas
    det["fichas_pct_fracas"] = round(pct_fracas * 100, 1)
    if pct_fracas > 0.5:
        return (
            False,
            f"fichas desactualizadas — {int(pct_fracas*100)}% das equipas "
            f"com <{min_jogos} jogos",
            det,
        )
    return True, "", det


def correr_health_check(
    *,
    csv_path: Path | None = None,
    db_path: Path | None = None,
    agora: datetime | None = None,
) -> ResultadoHealthCheck:
    """Corre todos os checks e devolve ResultadoHealthCheck."""
    from apostas.utils.config import db_path as _db_path, project_root

    r = ResultadoHealthCheck()
    csv_path = csv_path or (project_root() / "dados" / "tracking" / "sugestoes_live_v2.csv")
    db_path = db_path or _db_path()

    # 1. Flaresolverr
    ok, erro = _check_flaresolverr()
    r.detalhes["flaresolverr_ok"] = ok
    if not ok:
        r.tudo_ok = False
        r.problemas.append(f"❌ Flaresolverr: {erro}")

    # 2. CSV vivo
    ok, erro, det = _check_csv_vivo(csv_path, agora=agora)
    r.detalhes.update(det)
    if not ok:
        r.tudo_ok = False
        r.problemas.append(f"⚠ {erro}")

    # 3. BD
    ok, erro = _check_bd(db_path)
    r.detalhes["bd_ok"] = ok
    if not ok:
        r.tudo_ok = False
        r.problemas.append(f"❌ Base de dados: {erro}")

    # 4. Fichas desactualizadas
    ok, erro, det = _check_fichas_desactualizadas()
    r.detalhes.update(det)
    if not ok:
        r.tudo_ok = False
        r.problemas.append(f"⚠ Fichas: {erro}")

    return r


def formatar_telegram(r: ResultadoHealthCheck) -> str:
    """Mensagem Telegram-ready — so chamada quando ha problema."""
    if r.tudo_ok:
        return "✅ Sistema OK — todos os checks passaram."
    linhas = ["🚨 *Sistema com problemas*", ""]
    linhas.extend(r.problemas)
    if r.detalhes:
        linhas.append("")
        linhas.append("_Detalhes:_")
        for k, v in r.detalhes.items():
            linhas.append(f"  {k}: `{v}`")
    return "\n".join(linhas)
