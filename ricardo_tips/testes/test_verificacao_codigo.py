"""Verificacao estatica (pyright): falha com qualquer erro NOVO no codigo.

Apanha a classe de bug que deixou o scanner 2 dias sem sinais: `f.n_jogos`
num objecto que so tem `jogos_lidos` levantava AttributeError, o try/except
engolia e o filtro bloqueava tudo. O pyright ve isto sem correr o codigo.

Erros ja revistos (falsos alarmes) estao em pyright_baseline.json. So erros
novos fazem falhar. Se um erro novo for falso alarme comprovado, actualizar:
    python testes/test_verificacao_codigo.py --atualizar-baseline

Salta se o pyright nao estiver instalado (ex: PC Windows sem Node).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "pyright_baseline.json"


def _erros_actuais() -> Counter:
    out = subprocess.run(["pyright", "--outputjson"], cwd=RAIZ,
                         capture_output=True, text=True, timeout=300)
    dados = json.loads(out.stdout)
    erros: Counter = Counter()
    for d in dados["generalDiagnostics"]:
        if d["severity"] != "error":
            continue
        ficheiro = Path(d["file"]).resolve().relative_to(RAIZ).as_posix()
        msg = d["message"].splitlines()[0]
        erros[f'{ficheiro} | {d.get("rule", "?")} | {msg}'] += 1
    return erros


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright nao instalado")
def test_sem_erros_novos_do_pyright():
    base = Counter(json.loads(BASELINE.read_text(encoding="utf-8")))
    novos = _erros_actuais() - base
    assert not novos, "Erros novos do pyright (bug provavel):\n" + "\n".join(
        f"  {k} (x{n})" for k, n in sorted(novos.items()))


if __name__ == "__main__" and "--atualizar-baseline" in sys.argv:
    BASELINE.write_text(
        json.dumps(dict(sorted(_erros_actuais().items())), ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"Baseline actualizada: {BASELINE}")
