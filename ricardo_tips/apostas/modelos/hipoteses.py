"""M21 — Hypothesis Ledger: registo cientifico de hipoteses.

Workflow:
  1. Antes de alterar o modelo, grava uma hipotese ("casas suicas no
     sabado 21h+ pagam mais em overs"). Estado = ativa.
  2. Observa N alertas durante um periodo definido.
  3. Resolve com `confirmar / refutar / inconclusivo` + evidencia textual.
  4. A decisao_final e o veredito que fica para historico. Se confirmada,
     o modelo pode usar a regra; se refutada, mantem-se registada para
     nao voltar a tentar.

Nao mexe em modelos — e um livro de registo. A integracao com o
scanner, se existir, e feita pelo chamador que le hipoteses confirmadas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from apostas.utils.db import abrir_sessao
from apostas.utils.schema import Hipotese
from apostas.utils.tempo import agora_utc


ESTADOS_VALIDOS = ("ativa", "confirmada", "refutada", "inconclusiva")


@dataclass
class ResumoHipotese:
    """Vista leve de uma hipotese para o CLI."""
    id: int
    titulo: str
    estado: str
    criado_em: datetime
    resolvido_em: datetime | None


def adicionar(titulo: str, descricao: str) -> int:
    """Cria uma nova hipotese em estado 'ativa'. Devolve o id."""
    if not titulo.strip():
        raise ValueError("Titulo obrigatorio.")
    if not descricao.strip():
        raise ValueError("Descricao obrigatoria.")
    with abrir_sessao() as s:
        h = Hipotese(
            titulo=titulo.strip()[:200],
            descricao=descricao.strip(),
            estado="ativa",
        )
        s.add(h)
        s.flush()
        return h.id


def listar(estado: str | None = None) -> list[ResumoHipotese]:
    """Lista hipoteses — opcionalmente filtrando por estado."""
    with abrir_sessao() as s:
        q = select(Hipotese).order_by(Hipotese.criado_em.desc())
        if estado:
            q = q.where(Hipotese.estado == estado)
        rows = s.scalars(q).all()
        return [
            ResumoHipotese(
                id=r.id, titulo=r.titulo, estado=r.estado,
                criado_em=r.criado_em, resolvido_em=r.resolvido_em,
            )
            for r in rows
        ]


def resolver(
    hipotese_id: int,
    estado: str,
    *,
    evidencia_a_favor: str | None = None,
    evidencia_contra: str | None = None,
    decisao_final: str | None = None,
) -> bool:
    """Resolve uma hipotese (confirmada / refutada / inconclusiva).

    `evidencia_a_favor` e `evidencia_contra` sao notas textuais livres.
    `decisao_final` resume o veredito final.
    """
    if estado not in ESTADOS_VALIDOS or estado == "ativa":
        raise ValueError(
            f"Estado invalido: {estado}. Usa confirmada/refutada/inconclusiva."
        )
    with abrir_sessao() as s:
        row = s.get(Hipotese, hipotese_id)
        if row is None:
            return False
        row.estado = estado
        if evidencia_a_favor is not None:
            row.evidencia_a_favor = evidencia_a_favor
        if evidencia_contra is not None:
            row.evidencia_contra = evidencia_contra
        if decisao_final is not None:
            row.decisao_final = decisao_final
        row.resolvido_em = agora_utc()
    return True


def obter(hipotese_id: int) -> Hipotese | None:
    """Devolve a hipotese completa (fora da sessao, para apresentacao)."""
    with abrir_sessao() as s:
        row = s.get(Hipotese, hipotese_id)
        if row is None:
            return None
        s.expunge(row)
    return row


def formatar_lista_terminal(resumos: list[ResumoHipotese]) -> str:
    if not resumos:
        return "_Sem hipoteses registadas._"
    linhas = ["ID  Estado         Criada        Titulo", "-" * 60]
    for r in resumos:
        linhas.append(
            f"{r.id:>3}  {r.estado:<13}  {r.criado_em:%Y-%m-%d}  {r.titulo}"
        )
    return "\n".join(linhas)


def formatar_detalhe(h: Hipotese) -> str:
    partes = [
        f"Hipotese #{h.id}  [{h.estado}]",
        f"  {h.titulo}",
        "",
        h.descricao,
        "",
        f"Criada: {h.criado_em:%Y-%m-%d %H:%M}",
    ]
    if h.resolvido_em:
        partes.append(f"Resolvida: {h.resolvido_em:%Y-%m-%d %H:%M}")
    if h.evidencia_a_favor:
        partes.append("\nEvidencia a favor:")
        partes.append(f"  {h.evidencia_a_favor}")
    if h.evidencia_contra:
        partes.append("\nEvidencia contra:")
        partes.append(f"  {h.evidencia_contra}")
    if h.decisao_final:
        partes.append("\nDecisao final:")
        partes.append(f"  {h.decisao_final}")
    return "\n".join(partes)
