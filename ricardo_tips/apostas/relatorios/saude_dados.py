"""Saude dos dados: o historico esta mesmo a crescer?

Por dia (UTC), o que entrou na BD e nos registos:
  - jogos: jogos terminados guardados (fichas das equipas, EstatisticaEquipaJogo);
  - jogadores: fichas de jogadores guardadas (EstatisticaJogadorJogo);
  - momentos: momentos ao vivo guardados pelo scanner (EstadoLiveSnapshot);
  - dicas: dicas enviadas ao Telegram (alertas_enviados).
Mais o total guardado, quando correu a ultima noite e a ultima copia de seguranca.

Vai para o Telegram no fim de cada noite (se nao chegar, e porque a noite nao
correu), e o scanner avisa se a noite deixar de correr (vigiar_noite).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

DIAS = 7
HORAS_NOITE_ATRASADA = 30      # a noite corre as 00:15: 30h sem correr = falhou uma


@dataclass
class Dia:
    data: date
    jogos: int = 0
    jogadores: int = 0
    momentos: int = 0
    dicas: int = 0


@dataclass
class Saude:
    agora: datetime
    dias: list[Dia] = field(default_factory=list)
    total_jogos: int = 0
    total_jogadores: int = 0
    total_momentos: int = 0
    ultima_noite: dict | None = None
    ultima_copia: date | None = None

    def noite_atrasada(self) -> bool:
        fim = _quando(self.ultima_noite)
        return fim is None or self.agora - fim > timedelta(hours=HORAS_NOITE_ATRASADA)

    def problemas(self) -> list[str]:
        out = []
        fim = _quando(self.ultima_noite)
        if fim is None:
            out.append("a tarefa da noite ainda nao deixou registo (corre as 00:15)")
        elif self.noite_atrasada():
            out.append(f"a tarefa da noite nao corre desde {fim:%d/%m %H:%M}: os jogos novos "
                       "nao estao a ser guardados nem se aprende")
        if self.ultima_copia is None or (self.agora.date() - self.ultima_copia).days > 2:
            q = f"{self.ultima_copia:%d/%m}" if self.ultima_copia else "nunca"
            out.append(f"copia de seguranca: a ultima e de {q}")
        ontem = next((d for d in self.dias if d.data == self.agora.date() - timedelta(days=1)),
                     None)
        if ontem is not None:
            if ontem.momentos == 0:
                out.append("ontem o scanner nao guardou nenhum momento ao vivo (estava ligado?)")
            if ontem.jogos == 0:
                out.append("ontem nao entrou nenhum jogo terminado na BD")
        return out


def _quando(noite: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str((noite or {}).get("fim"))[:19])
    except ValueError:
        return None


def _por_dia(sess, coluna, contar, desde: datetime) -> dict[date, int]:
    from sqlalchemy import func, select
    out: dict[date, int] = {}
    for d, n in sess.execute(select(func.date(coluna), contar)
                             .where(coluna >= desde).group_by(func.date(coluna))).all():
        try:
            out[date.fromisoformat(str(d))] = int(n or 0)
        except ValueError:
            continue
    return out


def calcular(sess, agora: datetime | None = None, dias: int = DIAS,
             enviados: Path | None = None, raiz: Path | None = None) -> Saude:
    from sqlalchemy import func, select

    from apostas.relatorios import alertas_enviados
    from apostas.utils.schema import EstadoLiveSnapshot as Snap
    from apostas.utils.schema import EstatisticaEquipaJogo as EEJ
    from apostas.utils.schema import EstatisticaJogadorJogo as EJJ
    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    inicio = datetime(agora.year, agora.month, agora.day) - timedelta(days=dias - 1)
    jogos = _por_dia(sess, EEJ.data_ingestao,
                     func.count(func.distinct(EEJ.sofascore_event_id)), inicio)
    jogadores = _por_dia(sess, EJJ.data_ingestao, func.count(), inicio)
    momentos = _por_dia(sess, Snap.ts, func.count(), inicio)
    dicas: dict[date, int] = {}
    for r in alertas_enviados.carregar(enviados):
        if r["ts"] >= inicio:
            dicas[r["ts"].date()] = dicas.get(r["ts"].date(), 0) + 1
    s = Saude(agora=agora)
    for i in range(dias):
        d = (inicio + timedelta(days=i)).date()
        s.dias.append(Dia(d, jogos.get(d, 0), jogadores.get(d, 0), momentos.get(d, 0),
                          dicas.get(d, 0)))
    s.total_jogos = int(sess.scalar(select(func.count(func.distinct(EEJ.sofascore_event_id))))
                        or 0)
    s.total_jogadores = int(sess.scalar(select(func.count()).select_from(EJJ)) or 0)
    s.total_momentos = int(sess.scalar(select(func.count()).select_from(Snap)) or 0)
    s.ultima_noite = ler_noite(raiz)
    s.ultima_copia = ultima_copia(raiz)
    return s


# ─── Registo da ultima noite ───────────────────────────────────────────────

def caminho_noite(raiz: Path | None = None) -> Path:
    from apostas.utils.config import project_root
    return (raiz or project_root()) / "dados" / "tracking" / "ultima_noite.json"


def gravar_noite(inicio: datetime, fim: datetime, jogos_novos: int, versao: str = "",
                 raiz: Path | None = None) -> None:
    p = caminho_noite(raiz)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"inicio": inicio.isoformat(timespec="seconds"),
                             "fim": fim.isoformat(timespec="seconds"),
                             "minutos": round((fim - inicio).total_seconds() / 60),
                             "jogos_novos": jogos_novos, "versao": versao},
                            ensure_ascii=False), encoding="utf-8")


def ler_noite(raiz: Path | None = None) -> dict | None:
    try:
        return json.loads(caminho_noite(raiz).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ultima_copia(raiz: Path | None = None) -> date | None:
    from apostas.utils.config import project_root
    base = (raiz or project_root()) / "dados" / "backups"
    dias = []
    if base.exists():
        for d in base.iterdir():
            try:
                dias.append(datetime.strptime(d.name, "%Y-%m-%d").date())
            except ValueError:
                continue
    return max(dias) if dias else None


# ─── Texto ─────────────────────────────────────────────────────────────────

def _mil(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def formatar(s: Saude) -> str:
    linhas = [f"*🩺 Saude dos dados* ({s.agora:%d/%m/%Y})"]
    fim = _quando(s.ultima_noite)
    if fim is not None and s.ultima_noite is not None:
        icone = "⚠" if s.noite_atrasada() else "✅"
        linhas.append(f"{icone} Noite: correu {fim:%d/%m} as {fim:%H:%M} UTC "
                      f"({s.ultima_noite.get('minutos', '?')} min, "
                      f"+{s.ultima_noite.get('jogos_novos', 0)} jogos novos)")
    if s.ultima_copia is not None:
        icone = "✅" if (s.agora.date() - s.ultima_copia).days <= 2 else "⚠"
        linhas.append(f"{icone} Copia de seguranca: {s.ultima_copia:%d/%m/%Y}")
    linhas.append("Entrou por dia (jogos · jogadores · momentos ao vivo · dicas):")
    tabela = [f"{d.data:%d/%m}  {d.jogos:>4} · {_mil(d.jogadores):>6} · "
              f"{_mil(d.momentos):>6} · {d.dicas:>3}" for d in s.dias]
    linhas.append("```\n" + "\n".join(tabela) + "\n```")
    linhas.append(f"Total guardado: {_mil(s.total_jogos)} jogos · "
                  f"{_mil(s.total_jogadores)} fichas de jogadores · "
                  f"{_mil(s.total_momentos)} momentos ao vivo")
    probs = s.problemas()
    if probs:
        linhas += [f"⚠ {p}" for p in probs]
    else:
        linhas.append("✅ O historico esta a crescer todos os dias.")
    return "\n".join(linhas)


def aviso_noite(agora: datetime | None = None, raiz: Path | None = None) -> str:
    """O aviso que o scanner manda se a noite deixar de correr (vazio se esta bem
    ou se ainda nao ha registo nenhum - o primeiro sai na proxima noite)."""
    from apostas.utils.tempo import agora_utc
    agora = agora or agora_utc()
    fim = _quando(ler_noite(raiz))
    if fim is None or agora - fim <= timedelta(hours=HORAS_NOITE_ATRASADA):
        return ""
    return (f"⚠ *A tarefa da noite nao corre desde {fim:%d/%m %H:%M} UTC.* Os jogos novos nao "
            "estao a ser guardados (nem as fichas dos jogadores) e o sistema nao aprende.\n"
            "No PC, numa janela PowerShell:\n"
            "`Start-ScheduledTask -TaskName \"RicardoTips_IncrementoDiario\"`")
