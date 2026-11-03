"""Calibrador por (mercado, lado): corrige a probabilidade com os resultados reais.

A calibracao antiga (live_predictor.calibrar_prob) usa so a faixa de
probabilidade, global. Com 6110 alertas resolvidos viu-se que os UNDER
prometem 15-24pp a mais e os OVER acertam acima do prometido: os dois
enviesamentos anulam-se na media global e a correccao quase nao actua.

Aqui cada (fonte, mercado, lado) tem a sua curva isotonica (prob prevista ->
taxa real), encolhida para a identidade quando ha poucos dados:
    p_cal = w * iso(p) + (1 - w) * p,   w = n / (n + K)
Grupos com menos de MIN_N alertas caem para (mercado, lado) sem fonte, e
depois para a identidade.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from apostas.utils.logger import get_logger

log = get_logger(__name__)

MIN_N = 100
K_ENCOLHIMENTO = 200
_P_MIN, _P_MAX = 0.02, 0.98
_MERCADO_CANONICO = {"cards": "cartoes", "sot": "remates_a_baliza"}


@dataclass
class _Curva:
    xs: np.ndarray
    ys: np.ndarray
    n: int

    def aplicar(self, p: float) -> float:
        iso = float(np.interp(p, self.xs, self.ys))
        w = self.n / (self.n + K_ENCOLHIMENTO)
        return w * iso + (1 - w) * p


@dataclass
class Calibrador:
    curvas: dict[tuple, _Curva] = field(default_factory=dict)

    def calibrar(self, prob: float, mercado: str, lado: str,
                 fonte: str | None = None) -> float:
        m = _MERCADO_CANONICO.get(mercado.lower(), mercado.lower())
        lado = lado.lower()
        curva = self.curvas.get((fonte, m, lado)) or self.curvas.get((None, m, lado))
        if curva is None:
            return prob
        return min(_P_MAX, max(_P_MIN, curva.aplicar(prob)))


def _ajustar_curva(probs: list[float], ganhou: list[bool]) -> _Curva | None:
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        log.warning("Calibrador: scikit-learn indisponivel, sem calibracao por mercado")
        return None
    iso = IsotonicRegression(y_min=_P_MIN, y_max=_P_MAX, out_of_bounds="clip")
    iso.fit(np.asarray(probs), np.asarray(ganhou, dtype=float))
    return _Curva(xs=iso.X_thresholds_, ys=iso.y_thresholds_, n=len(probs))


def treinar(rows: list[dict]) -> Calibrador:
    """rows: dicts com prob, ganhou, mercado, lado e (opcional) fonte."""
    grupos: dict[tuple, list[dict]] = {}
    for r in rows:
        m = _MERCADO_CANONICO.get(r["mercado"], r["mercado"])
        grupos.setdefault((r.get("fonte"), m, r["lado"]), []).append(r)
        grupos.setdefault((None, m, r["lado"]), []).append(r)
    cal = Calibrador()
    for chave, g in grupos.items():
        if len(g) < MIN_N:
            continue
        curva = _ajustar_curva([r["prob"] for r in g], [r["ganhou"] for r in g])
        if curva is not None:
            cal.curvas[chave] = curva
    return cal


def _calibrar_row(cal: Calibrador, r: dict) -> float:
    return cal.calibrar(r["prob"], r["mercado"], r["lado"], r.get("fonte"))


def avaliar_fora_da_amostra(rows: list[dict], frac_treino: float = 0.7,
                            min_teste: int = 200) -> dict | None:
    """Treina nos alertas mais antigos e mede nos mais recentes (nunca vistos).

    Devolve Brier bruto vs calibrado no teste e, por grupo com >= 50 alertas
    no teste, prob media bruta, calibrada e taxa real.
    """
    ordenados = sorted(rows, key=lambda r: r.get("ts") or "")
    corte = int(len(ordenados) * frac_treino)
    treino, teste = ordenados[:corte], ordenados[corte:]
    if len(teste) < min_teste:
        return None
    cal = treinar(treino)

    def _brier(ps: list[float]) -> float:
        return sum((p - (1.0 if r["ganhou"] else 0.0)) ** 2
                   for p, r in zip(ps, teste)) / len(teste)

    probs_cal = [_calibrar_row(cal, r) for r in teste]
    grupos: dict[tuple[str, str], list[tuple[dict, float]]] = {}
    for r, pc in zip(teste, probs_cal):
        grupos.setdefault((r["mercado"], r["lado"]), []).append((r, pc))
    por_grupo = []
    for (m, lado), g in grupos.items():
        if len(g) < 50:
            continue
        por_grupo.append({
            "mercado": m, "lado": lado, "n": len(g),
            "prob_bruta": sum(r["prob"] for r, _ in g) / len(g),
            "prob_cal": sum(pc for _, pc in g) / len(g),
            "taxa_real": sum(1 for r, _ in g if r["ganhou"]) / len(g),
        })
    por_grupo.sort(key=lambda d: -d["n"])
    return {
        "n_treino": len(treino), "n_teste": len(teste),
        "brier_bruto": _brier([r["prob"] for r in teste]),
        "brier_cal": _brier(probs_cal),
        "por_grupo": por_grupo,
    }
