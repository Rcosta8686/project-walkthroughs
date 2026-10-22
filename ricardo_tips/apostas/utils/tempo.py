"""Helpers de tempo neutros ao timezone.

`datetime.utcnow()` esta deprecated no Python 3.14. Esta funcao devolve
o equivalente (datetime naive em UTC) via API nao-deprecated.

Serve para:
  - Timestamps em DataFrames / CSV
  - Defaults de colunas SQLAlchemy que esperam datetime naive
  - Comparacoes com datetimes ja gravados na BD (todos naive UTC)

Nao mudar para datetime.now(timezone.utc) (aware) a nao ser que todas as
comparacoes sejam actualizadas tambem.
"""
from datetime import datetime, timezone


def agora_utc() -> datetime:
    """Datetime naive em UTC — substitui `datetime.utcnow()`."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
