"""Historico por tipo de alerta — M16 (parte 2, melhorias scanner).

Agrega sugestoes_live_v2.csv por (liga, mercado, lado) e devolve:
  - n_alertas: quantos sinais ja saíram
  - clv_medio: media de (odd_apostada / odd_fecho - 1) × 100
  - pct_clv_positivo: % de sinais com CLV >= 0
  - roi: % de retorno sobre stake (apenas resolvidos)
  - categoria_forte: True se >=100 alertas E (CLV>0 OU ROI>0)
  - stake_mult: 1.0 (categoria forte), 0.5 (fraca), 0.0 (bloqueada)

No alerta Telegram adiciona:
  '📊 Historico: 214 alertas · CLV +2.4% · ROI +3.1% · ✅ categoria forte'

Nao faz I/O extra para cada alerta: carrega CSV uma vez em memoria,
devolve dict por chave.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PerfilAlerta:
    """Perfil historico de 1 tipo de alerta (liga, mercado, lado)."""
    liga: str
    mercado: str
    lado: str
    n_alertas: int
    n_resolvidos: int
    clv_medio: float | None       # % (ex: +2.4 = +2.4%)
    pct_clv_positivo: float | None  # 0.0-1.0
    roi: float | None               # % (ex: +3.1 = +3.1%)
    categoria_forte: bool
    stake_mult: float  # 1.0 (normal), 0.5 (metade), 0.0 (nao enviar)


# ─────────── Agregacao ──────────────────────────────────────────────────

_MIN_ALERTAS_CATEGORIA_FORTE = 100
_MIN_ALERTAS_PARA_METADE = 20
_LIMIAR_CLV_BLOQUEAR = -3.0  # CLV% <= -3 → nao enviar


def _parse_float(s) -> float | None:
    try:
        v = float(s)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def carregar_perfis(csv_path: Path) -> dict[tuple[str, str, str], PerfilAlerta]:
    """Carrega o CSV e devolve dict {(liga, mercado, lado): PerfilAlerta}.

    Chaves tudo lowercase sem espacos extras para match consistente.
    """
    perfis: dict[tuple[str, str, str], PerfilAlerta] = {}
    if not csv_path.exists():
        return perfis

    # Buckets {chave: dict agregador}
    buckets: dict[tuple[str, str, str], dict] = {}

    with csv_path.open(encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            liga = (row.get("liga") or "").strip()
            mercado = (row.get("mercado") or "").strip().lower()
            lado = (row.get("lado") or "").strip().lower()
            if not mercado or not lado:
                continue
            chave = (liga, mercado, lado)
            b = buckets.setdefault(chave, {
                "n": 0, "n_resolvidos": 0,
                "clvs": [], "lucros": [], "clv_positivos": 0,
            })
            b["n"] += 1

            # CLV se existir coluna
            clv = _parse_float(row.get("clv"))
            if clv is not None:
                b["clvs"].append(clv)
                if clv >= 0:
                    b["clv_positivos"] += 1

            # ROI: lucro / stake (lucro ja vem calculado, stake = 1 implicito)
            resultado = (row.get("resultado") or "").strip()
            if resultado in ("W", "L", "push"):
                b["n_resolvidos"] += 1
                lucro = _parse_float(row.get("lucro"))
                if lucro is not None:
                    b["lucros"].append(lucro)

    for chave, b in buckets.items():
        liga, mercado, lado = chave
        # CLV no CSV e fracao (0.03). Guarda em % (3.0) para display.
        clv_medio = (sum(b["clvs"]) / len(b["clvs"])) * 100 if b["clvs"] else None
        pct_clv_pos = b["clv_positivos"] / len(b["clvs"]) if b["clvs"] else None
        roi = (sum(b["lucros"]) / len(b["lucros"])) * 100 if b["lucros"] else None

        # Categoria forte: >=100 alertas E (CLV positivo OU ROI positivo)
        forte = (
            b["n"] >= _MIN_ALERTAS_CATEGORIA_FORTE
            and ((clv_medio is not None and clv_medio > 0)
                 or (roi is not None and roi > 0))
        )

        # Stake multiplier
        stake_mult = 1.0  # default
        if clv_medio is not None and clv_medio <= _LIMIAR_CLV_BLOQUEAR:
            stake_mult = 0.0  # bloqueia envio
        elif b["n"] < _MIN_ALERTAS_PARA_METADE:
            stake_mult = 0.5  # poucos dados → metade
        elif not forte and clv_medio is not None and clv_medio < 0:
            stake_mult = 0.5  # CLV negativo mas pouco → metade
        elif forte:
            stake_mult = 1.0

        perfis[chave] = PerfilAlerta(
            liga=liga, mercado=mercado, lado=lado,
            n_alertas=b["n"], n_resolvidos=b["n_resolvidos"],
            clv_medio=clv_medio, pct_clv_positivo=pct_clv_pos,
            roi=roi, categoria_forte=forte, stake_mult=stake_mult,
        )
    return perfis


def perfil_para(perfis: dict, liga: str, mercado: str, lado: str) -> PerfilAlerta | None:
    """Procura perfil por (liga, mercado, lado) — case-insensitive."""
    chave = (liga.strip(), mercado.strip().lower(), lado.strip().lower())
    return perfis.get(chave)


@dataclass
class PerfilMercado:
    """Agregado por (liga, mercado) ignorando lado — usado pelo
    filtro de desempenho para blacklist dinamica."""
    liga: str
    mercado: str
    n_alertas: int
    n_resolvidos: int
    roi: float | None       # %
    clv_medio: float | None  # %


def agregar_por_mercado(perfis: dict) -> dict[tuple[str, str], PerfilMercado]:
    """Agrega perfis (liga, mercado, lado) para (liga, mercado).

    Devolve dict {(liga, mercado): PerfilMercado}. Serve o filtro de
    desempenho que decide bloquear uma combinacao (liga, mercado) com
    volume e ROI real conhecidos — menos sensivel ao lado especifico.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for (liga, mercado, _lado), p in perfis.items():
        chave = (liga, mercado)
        b = buckets.setdefault(chave, {
            "n": 0, "n_res": 0, "roi_num": 0.0, "roi_den": 0,
            "clv_num": 0.0, "clv_den": 0,
        })
        b["n"] += p.n_alertas
        b["n_res"] += p.n_resolvidos
        if p.roi is not None:
            # Pondera ROI pelo volume de resolvidos
            b["roi_num"] += p.roi * p.n_resolvidos
            b["roi_den"] += p.n_resolvidos
        if p.clv_medio is not None:
            b["clv_num"] += p.clv_medio * p.n_alertas
            b["clv_den"] += p.n_alertas

    out: dict[tuple[str, str], PerfilMercado] = {}
    for (liga, mercado), b in buckets.items():
        out[(liga, mercado)] = PerfilMercado(
            liga=liga, mercado=mercado,
            n_alertas=b["n"], n_resolvidos=b["n_res"],
            roi=(b["roi_num"] / b["roi_den"]) if b["roi_den"] else None,
            clv_medio=(b["clv_num"] / b["clv_den"]) if b["clv_den"] else None,
        )
    return out


def formatar_linha_telegram(p: PerfilAlerta) -> str:
    """Produz '📊 214 alertas · CLV +2.4% · ROI +3.1% · ✅ categoria forte'."""
    partes = [f"📊 {p.n_alertas} alertas"]
    if p.clv_medio is not None:
        sinal = "+" if p.clv_medio >= 0 else ""
        partes.append(f"CLV {sinal}{p.clv_medio:.1f}%")
    if p.roi is not None:
        sinal = "+" if p.roi >= 0 else ""
        partes.append(f"ROI {sinal}{p.roi:.1f}%")
    if p.categoria_forte:
        partes.append("✅ categoria forte")
    elif p.stake_mult == 0.5:
        partes.append("⚠ categoria fraca → stake/2")
    elif p.stake_mult == 0.0:
        partes.append("⛔ CLV negativo → nao apostar")
    return " · ".join(partes)
