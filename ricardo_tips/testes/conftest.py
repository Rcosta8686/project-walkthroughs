"""Configuração partilhada do pytest.

Faz com que o pacote `apostas` seja importável a partir dos testes,
sem precisar de o instalar.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
