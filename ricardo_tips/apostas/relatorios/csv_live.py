"""Formato do sugestoes_live_v2.csv e reparacao de linhas desalinhadas.

O scanner escreve cada linha pela ordem de CSV_COLUNAS, mas o cabecalho do
ficheiro fica o da data em que foi criado. As colunas mudaram 4 vezes
(event_id, estrelas, p_mercado/p_final/verificar, alert_id): num ficheiro
antigo as linhas novas ficam deslocadas em relacao ao cabecalho. Efeitos
vistos no PC do Ricardo:
  - alert_id ilegivel -> os ids recomecam em cada ciclo (/apostei 4 repetido);
  - a coluna "resultado" le outro valor -> o resolver acha que ja esta
    resolvida e nunca a resolve -> calibracao, ROI historico e filtros
    ficam presos aos dados antigos.

`realinhar` reconhece em que formato cada linha foi escrita (pelo conteudo:
data, marcador, mercado, lado nas posicoes de cada formato), reescreve tudo
no formato actual com copia de seguranca e numera os alertas de novo.
"""
from __future__ import annotations

import csv
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

_BASE_V1 = [
    "ts_scan", "liga", "home", "away", "minuto", "marcador",
    "mercado", "lado", "linha", "casa", "odd", "prob_modelo", "ev", "xg_casa", "xg_fora",
    "shots_on_target_c", "shots_on_target_f", "corners_c", "corners_f", "yellows_c", "yellows_f",
]
_RESOLVER = ["resultado", "lucro", "resolvido_em"]

COLUNAS_ACTUAIS = [
    "alert_id",
    "ts_scan", "event_id", "liga", "home", "away", "minuto", "marcador",
    "mercado", "lado", "linha", "casa", "odd",
    "prob_modelo", "p_mercado", "p_final", "verificar",
    "ev", "estrelas", "xg_casa", "xg_fora",
    "shots_on_target_c", "shots_on_target_f",
    "corners_c", "corners_f", "yellows_c", "yellows_f",
    # Preenchidos por resolver_sugestoes_live.py:
    "resultado", "lucro", "resolvido_em",
]

# Formatos com que o scanner ja escreveu linhas (git: ec74877 → 1ef4ae1).
FORMATOS: dict[str, list[str]] = {
    "v1 (11/10)": _BASE_V1 + _RESOLVER,  # o resolver acrescentava as 3 no fim
    "v2 (11/10, event_id)": _BASE_V1[:1] + ["event_id"] + _BASE_V1[1:] + _RESOLVER,
    "v3 (12/10, estrelas)": (_BASE_V1[:1] + ["event_id"] + _BASE_V1[1:13] + ["estrelas"]
                             + _BASE_V1[13:] + _RESOLVER),
    "v4 (14/10, p_mercado)": [c for c in COLUNAS_ACTUAIS if c != "alert_id"],
    "v5 (18/10, alert_id)": COLUNAS_ACTUAIS,
}

_MERCADOS = {"golos", "cantos", "cartoes", "cards", "remates", "remates_a_baliza", "sot",
             "btts", "1x2", "dupla", "handicap", "golos_1t", "cantos_1t", "cartoes_1t"}
_LADOS = {"over", "under", "casa", "fora", "empate", "sim", "nao", "1", "x", "2",
          "home", "away", "draw",
          "1x", "x2", "12", "yes", "no"}
_RE_MARCADOR = re.compile(r"^\d{1,2}-\d{1,2}$")
_LIMIAR_IRRECONHECIVEIS = 0.02  # acima disto nao se mexe no ficheiro


def _ts(v: str) -> bool:
    try:
        datetime.fromisoformat(v[:19])
        return True
    except ValueError:
        return False


def _num(v: str, lo: float | None = None, hi: float | None = None) -> bool:
    try:
        x = float(v)
    except ValueError:
        return False
    return (lo is None or x >= lo) and (hi is None or x <= hi)


def _inteiro(v: str, hi: int | None = None) -> bool:
    return v.isdigit() and (hi is None or int(v) <= hi)


# Validador por coluna. Vazio e aceite onde a coluna pode ficar por preencher.
# Validam-se todas as colunas com conteudo previsivel: um formato errado poe
# quase sempre um texto/decimal numa coluna inteira (ou vice-versa) e cai.
_VALIDADORES: dict[str, tuple[bool, Callable[[str], bool]]] = {  # coluna: (aceita_vazio, teste)
    "alert_id": (True, _inteiro),
    "ts_scan": (False, _ts),
    "event_id": (True, _inteiro),
    "minuto": (False, lambda v: _inteiro(v, 150)),
    "marcador": (False, lambda v: bool(_RE_MARCADOR.match(v))),
    "mercado": (False, lambda v: v.lower() in _MERCADOS),
    "lado": (False, lambda v: v.lower() in _LADOS),
    "odd": (True, lambda v: _num(v, 0.0, 1000.0)),
    "prob_modelo": (True, lambda v: _num(v, 0.0, 1.0)),
    "p_mercado": (True, lambda v: _num(v, 0.0, 1.0)),
    "p_final": (True, lambda v: _num(v, 0.0, 1.0)),
    "verificar": (True, lambda v: v in ("True", "False", "0", "1")),
    "ev": (True, _num),
    "estrelas": (True, lambda v: _inteiro(v, 5)),
    "xg_casa": (True, lambda v: _num(v, 0.0, 15.0)),
    "xg_fora": (True, lambda v: _num(v, 0.0, 15.0)),
    "shots_on_target_c": (True, lambda v: _inteiro(v, 60)),
    "shots_on_target_f": (True, lambda v: _inteiro(v, 60)),
    "corners_c": (True, lambda v: _inteiro(v, 40)),
    "corners_f": (True, lambda v: _inteiro(v, 40)),
    "yellows_c": (True, lambda v: _inteiro(v, 15)),
    "yellows_f": (True, lambda v: _inteiro(v, 15)),
    "resultado": (True, lambda v: v in ("W", "L", "push")),
    "lucro": (True, lambda v: _num(v, -1000.0, 1000.0)),
    "resolvido_em": (True, _ts),
}


def _pontuar(formato: list[str], valores: list[str]) -> int | None:
    """Campos preenchidos e validos se a linha foi escrita neste formato;
    None se algum campo contradiz o formato."""
    pontos = 0
    for i, col in enumerate(formato):
        regra = _VALIDADORES.get(col)
        if regra is None:
            continue
        aceita_vazio, teste = regra
        v = valores[i].strip() if i < len(valores) else ""
        if not v:
            if not aceita_vazio:
                return None
            continue
        if not teste(v):
            return None
        pontos += 1
    # valores a mais do que o formato tem colunas: tem de ser lixo vazio
    if any(x.strip() for x in valores[len(formato):]):
        return None
    return pontos


def detectar_formato(valores: list[str], cabecalho: list[str]) -> list[str] | None:
    """Formato em que a linha foi escrita. Em empate ganha o cabecalho do ficheiro
    (o resolver escreve resultado/lucro pelas posicoes do cabecalho)."""
    candidatos = [cabecalho] + [f for f in FORMATOS.values() if f != cabecalho]
    melhor, melhor_pts = None, -1
    for f in candidatos:
        pts = _pontuar(f, valores)
        if pts is not None and pts > melhor_pts:
            melhor, melhor_pts = f, pts
    return melhor


def _nome_formato(f: list[str]) -> str:
    for nome, cols in FORMATOS.items():
        if cols == f:
            return nome
    return "cabecalho do ficheiro"


@dataclass
class Diagnostico:
    cabecalho_actual: bool
    n_linhas: int = 0
    por_formato: Counter = field(default_factory=Counter)
    irreconheciveis: int = 0
    desalinhadas: int = 0          # linhas cujo formato != cabecalho do ficheiro
    duplicadas: int = 0
    ids_repetidos: int = 0
    reparado: bool = False
    recusado: bool = False
    backup: Path | None = None

    @property
    def precisa_reparar(self) -> bool:
        return (not self.cabecalho_actual or self.desalinhadas > 0
                or self.duplicadas > 0 or self.ids_repetidos > 0)

    def texto(self) -> str:
        linhas = [f"Linhas: {self.n_linhas} · cabecalho "
                  f"{'actual' if self.cabecalho_actual else 'ANTIGO'}"]
        for nome, n in sorted(self.por_formato.items()):
            linhas.append(f"  {nome}: {n}")
        if self.irreconheciveis:
            linhas.append(f"  irreconheciveis: {self.irreconheciveis}")
        linhas.append(f"Desalinhadas: {self.desalinhadas} · duplicadas: {self.duplicadas}"
                      f" · alert_id repetidos: {self.ids_repetidos}")
        if self.recusado:
            linhas.append("NAO reparado: demasiadas linhas irreconheciveis (nada foi alterado).")
        elif self.reparado:
            linhas.append(f"REPARADO. Copia de seguranca: {self.backup}")
        elif not self.precisa_reparar:
            linhas.append("Ficheiro em ordem, nada a fazer.")
        return "\n".join(linhas)


def _chave(d: dict) -> tuple:
    return (d.get("home", "") + "|" + d.get("away", ""), d.get("mercado", ""),
            d.get("lado", ""), d.get("linha", ""), d.get("casa", ""), d.get("minuto", ""))


def _ler(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8", newline="") as fp:
        r = csv.reader(fp)
        cabecalho = next(r, [])
        return cabecalho, [v for v in r if any(x.strip() for x in v)]


def realinhar(path: Path, aplicar: bool = True,
              registo_enviados: Path | None = None) -> Diagnostico:
    """Diagnostica e (se `aplicar`) reescreve o CSV no formato actual.

    Nunca altera nada se mais de 2% das linhas forem irreconheciveis. Antes
    de escrever copia o ficheiro para `<nome>.backup-AAAAMMDD-HHMMSS.csv`.
    Os alert_id sao renumerados 1..N (os antigos repetiam-se); por isso os
    alertas no registo de enviados deixam de ser fiaveis e saem dele (as
    projeccoes ficam).
    """
    if not path.exists():
        return Diagnostico(cabecalho_actual=True)
    cabecalho, linhas = _ler(path)
    diag = Diagnostico(cabecalho_actual=cabecalho == COLUNAS_ACTUAIS, n_linhas=len(linhas))

    convertidas: dict[tuple, dict] = {}
    irreconheciveis: list[list[str]] = []
    ids: Counter = Counter()
    for valores in linhas:
        formato = detectar_formato(valores, cabecalho)
        if formato is None:
            diag.irreconheciveis += 1
            irreconheciveis.append(valores)
            continue
        diag.por_formato[_nome_formato(formato)] += 1
        if formato != cabecalho:
            diag.desalinhadas += 1
        d = {c: (valores[i] if i < len(valores) else "") for i, c in enumerate(formato)}
        if d.get("alert_id"):
            ids[d["alert_id"]] += 1
        k = _chave(d)
        antiga = convertidas.get(k)
        if antiga is not None:
            diag.duplicadas += 1
            if antiga.get("resultado") or not d.get("resultado"):
                continue
        convertidas[k] = d
    diag.ids_repetidos = sum(n - 1 for n in ids.values() if n > 1)

    if not aplicar or not diag.precisa_reparar:
        return diag
    if diag.n_linhas and diag.irreconheciveis / diag.n_linhas > _LIMIAR_IRRECONHECIVEIS:
        diag.recusado = True
        return diag

    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    diag.backup = path.with_name(f"{path.stem}.backup-{carimbo}{path.suffix}")
    shutil.copy2(path, diag.backup)
    if irreconheciveis:
        with path.with_name(f"{path.stem}.irreconheciveis-{carimbo}{path.suffix}").open(
                "w", encoding="utf-8", newline="") as fp:
            csv.writer(fp).writerows([cabecalho] + irreconheciveis)

    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=COLUNAS_ACTUAIS, extrasaction="ignore")
        w.writeheader()
        for n, d in enumerate(convertidas.values(), start=1):
            d["alert_id"] = str(n)
            w.writerow(d)
    tmp.replace(path)
    diag.reparado = True
    _limpar_alertas_do_registo(registo_enviados, carimbo)
    return diag


def _limpar_alertas_do_registo(registo: Path | None, carimbo: str) -> None:
    if registo is None or not registo.exists():
        return
    with registo.open(encoding="utf-8", newline="") as fp:
        rows = list(csv.reader(fp))
    if not rows:
        return
    cab = rows[0]
    i_tipo = cab.index("tipo") if "tipo" in cab else None
    manter = [r for r in rows[1:] if i_tipo is not None and len(r) > i_tipo
              and r[i_tipo] == "projeccao"]
    shutil.copy2(registo, registo.with_name(f"{registo.stem}.backup-{carimbo}{registo.suffix}"))
    with registo.open("w", encoding="utf-8", newline="") as fp:
        csv.writer(fp).writerows([cab] + manter)
