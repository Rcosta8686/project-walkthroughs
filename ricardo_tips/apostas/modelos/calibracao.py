"""Calibração de probabilidades: Platt scaling e isotonic regression.

O backtest em dados reais mostrou que o modelo GAP está mal calibrado:
quando diz 60-70% prob, a realidade é só 37.5%. Isto não é bug do modelo;
é um problema conhecido em modelos derivados de Poisson. A solução é
transformar as probabilidades do modelo com uma função fittada em dados
históricos (hold-out) antes de usar para EV.

Dois métodos:
  - Platt scaling: sigmoid(a + b * logit(p)), paramétrico (2 parâmetros)
  - Isotonic regression: não-paramétrico, aproxima qualquer função
    monotónica. Mais flexível, precisa de mais dados para não overfit.

Uso típico:
    cal = Calibrador("platt")
    cal.fit(probs_modelo_treino, labels_treino)
    probs_cal = [cal.transform(p) for p in probs_modelo_teste]
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


@dataclass
class Calibrador:
    """Platt scaling (default) ou isotonic regression.

    Chamar `fit(probs, labels)` com um conjunto de validação; depois
    `transform(p)` para cada probabilidade do modelo em produção.
    """

    metodo: str = "platt"  # "platt" | "isotonic" | "nenhuma"

    def __post_init__(self) -> None:
        self._fitted = False
        self._platt: LogisticRegression | None = None
        self._iso: IsotonicRegression | None = None

    def fit(self, probs: list[float], labels: list[int]) -> None:
        if self.metodo == "nenhuma":
            self._fitted = True
            return
        if len(probs) < 20:
            raise ValueError(
                f"Calibração precisa de >= 20 observações; recebeu {len(probs)}"
            )
        if len(probs) != len(labels):
            raise ValueError("probs e labels têm comprimentos diferentes")

        if self.metodo == "platt":
            # Entrada: logit(p); saída: sigmoid(a + b * logit(p))
            # Clamp para evitar logit(0) ou logit(1).
            X = [[_logit_safe(p)] for p in probs]
            self._platt = LogisticRegression(C=1e6, solver="lbfgs")
            self._platt.fit(X, labels)
        elif self.metodo == "isotonic":
            self._iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            self._iso.fit(probs, labels)
        else:
            raise ValueError(f"Método de calibração desconhecido: {self.metodo}")

        self._fitted = True

    def transform(self, p: float) -> float:
        if not self._fitted:
            raise RuntimeError("Calibrador não foi fittado")
        if self.metodo == "nenhuma":
            return p
        if self.metodo == "platt":
            x = _logit_safe(p)
            return float(self._platt.predict_proba([[x]])[0, 1])
        return float(self._iso.transform([p])[0])


def _logit_safe(p: float, eps: float = 1e-6) -> float:
    p = max(eps, min(1.0 - eps, p))
    return math.log(p / (1.0 - p))
