"""Calibracao dos alertas: prob prevista vs taxa real de acerto."""
from __future__ import annotations

import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apostas.relatorios.calibracao_alertas import (
    LinhaCalibracao, brier, calibrar_por_faixa, calibrar_por_mercado,
    carregar_resolvidos, formatar_relatorio,
)

COLS = ["ts_scan", "mercado", "lado", "prob_modelo", "resultado", "lucro"]


def _escrever(path: Path, linhas: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for l in linhas:
            w.writerow({"ts_scan": "2026-11-01T20:00:00", **l})
    return path


def _bloco(mercado, lado, prob, n_w, n_l, odd):
    """n_w vitorias e n_l derrotas com a mesma prob e odd (stake 1)."""
    return ([{"mercado": mercado, "lado": lado, "prob_modelo": prob,
              "resultado": "W", "lucro": odd - 1}] * n_w
            + [{"mercado": mercado, "lado": lado, "prob_modelo": prob,
                "resultado": "L", "lucro": -1}] * n_l)


def _rows(prob, n_w, n_l, mercado="cartoes", lado="over"):
    return [{"mercado": mercado, "lado": lado, "prob": prob, "ganhou": i < n_w,
             "lucro": 0.4 if i < n_w else -1.0} for i in range(n_w + n_l)]


# ─── carregar_resolvidos ─────────────────────────────────────────────────

def test_carrega_so_w_e_l_com_prob_valida(tmp_path):
    p = _escrever(tmp_path / "a.csv", [
        {"mercado": "cards", "lado": "over", "prob_modelo": "0.7", "resultado": "W", "lucro": "0.4"},
        {"mercado": "golos", "lado": "under", "prob_modelo": "0.6", "resultado": "L", "lucro": "-1"},
        {"mercado": "golos", "lado": "over", "prob_modelo": "0.6", "resultado": "push", "lucro": "0"},
        {"mercado": "golos", "lado": "over", "prob_modelo": "0.6", "resultado": "", "lucro": ""},
        {"mercado": "golos", "lado": "over", "prob_modelo": "", "resultado": "W", "lucro": "1"},
        {"mercado": "golos", "lado": "over", "prob_modelo": "1.0", "resultado": "W", "lucro": "1"},
    ])
    rows = carregar_resolvidos(p)
    assert len(rows) == 2
    assert rows[0]["mercado"] == "cartoes"  # cards normalizado
    assert rows[0]["ganhou"] is True and rows[1]["ganhou"] is False


def test_csv_inexistente_devolve_vazio(tmp_path):
    assert carregar_resolvidos(tmp_path / "nao_existe.csv") == []


def test_filtro_desde(tmp_path):
    p = tmp_path / "a.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerow({"ts_scan": "2026-09-01T20:00:00", "mercado": "golos", "lado": "over",
                    "prob_modelo": "0.6", "resultado": "W", "lucro": "1"})
        w.writerow({"ts_scan": "2026-11-01T20:00:00", "mercado": "golos", "lado": "over",
                    "prob_modelo": "0.6", "resultado": "L", "lucro": "-1"})
    assert len(carregar_resolvidos(p, desde=datetime(2026, 10, 1))) == 1


# ─── Veredicto ───────────────────────────────────────────────────────────

def test_veredicto_calibrado():
    l = LinhaCalibracao("x", n=100, prob_media=0.70, taxa_real=0.70, roi=0.02)
    assert l.veredicto == "calibrado"


def test_veredicto_sobrestima():
    """Caso tipico remates: diz 75%, acerta 58% em 100 alertas."""
    l = LinhaCalibracao("x", n=100, prob_media=0.75, taxa_real=0.58, roi=-0.2)
    assert l.veredicto == "sobrestima"


def test_veredicto_subestima():
    l = LinhaCalibracao("x", n=100, prob_media=0.60, taxa_real=0.78, roi=0.3)
    assert l.veredicto == "subestima"


def test_desvio_pequeno_dentro_da_margem_e_calibrado():
    """65% prometido, 60% real em 100: dentro de 1.96 sigma (~9pp)."""
    l = LinhaCalibracao("x", n=100, prob_media=0.65, taxa_real=0.60, roi=0.0)
    assert l.veredicto == "calibrado"


def test_poucos_dados_nao_acusa():
    l = LinhaCalibracao("x", n=10, prob_media=0.80, taxa_real=0.30, roi=-0.5)
    assert l.veredicto == "poucos dados"


# ─── Agrupamentos ────────────────────────────────────────────────────────

def test_por_faixa_separa_probabilidades():
    rows = _rows(0.55, 5, 5) + _rows(0.75, 7, 3)
    faixas = {l.grupo: l for l in calibrar_por_faixa(rows)}
    assert faixas["50-60%"].n == 10 and faixas["50-60%"].taxa_real == 0.5
    assert faixas["70-80%"].n == 10 and faixas["70-80%"].taxa_real == 0.7


def test_por_mercado_respeita_min_n():
    rows = _rows(0.7, 8, 4, "cartoes", "over") + _rows(0.7, 2, 1, "cantos", "over")
    grupos = [l.grupo for l in calibrar_por_mercado(rows, min_n=10)]
    assert grupos == ["🟨 cartoes over"]


def test_brier_moeda_ao_ar():
    assert abs(brier(_rows(0.5, 5, 5)) - 0.25) < 1e-9


def test_brier_vazio():
    assert brier([]) is None


# ─── Relatorio ───────────────────────────────────────────────────────────

def test_relatorio_vazio():
    assert "Ainda nao ha alertas resolvidos" in formatar_relatorio([])


def test_relatorio_confia_no_calibrado_e_corta_o_sobrestimado(tmp_path):
    p = _escrever(tmp_path / "a.csv",
                  _bloco("cards", "over", 0.70, 70, 30, odd=1.6)        # calibrado, ROI +12%
                  + _bloco("remates", "over", 0.78, 55, 45, odd=1.3))  # sobrestima, ROI -28.5%
    txt = formatar_relatorio(carregar_resolvidos(p))
    confiar = next(l for l in txt.splitlines() if l.strip().startswith("Confiar"))
    cortar = next(l for l in txt.splitlines() if l.strip().startswith("Cortar"))
    assert "cartoes over" in confiar and "remates" not in confiar
    assert "remates over" in cortar and "cartoes" not in cortar


def test_script_end_to_end(tmp_path):
    p = _escrever(tmp_path / "a.csv", _bloco("cards", "over", 0.70, 21, 9, odd=1.6))
    script = Path(__file__).resolve().parent.parent / "scripts" / "relatorio_calibracao.py"
    out = subprocess.run([sys.executable, str(script), "--csv", str(p)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "30 alertas resolvidos" in out.stdout
