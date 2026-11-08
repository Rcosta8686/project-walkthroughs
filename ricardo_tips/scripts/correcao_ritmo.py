"""Calcula ja a correccao do ritmo do resto do jogo (o incremento das 00:15
faz o mesmo todas as noites) e mostra o resultado.

Volta a correr o modelo em cada momento guardado dos jogos ao vivo ja
acabados, compara o que previa que faltava com o que aconteceu, e liga a
correccao so nos mercados em que ela melhora a previsao nos jogos mais
recentes. Nao faz pedidos ao Sofascore (so usa a BD).

Uso:
  venv\\Scripts\\python.exe scripts\\correcao_ritmo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: emojis sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.modelos import cartoes_finais, correcao_ritmo  # noqa: E402
from apostas.utils.db import abrir_sessao, criar_schema  # noqa: E402


def main() -> None:
    from apostas.utils.versao import versao_codigo
    print(f"Versao do codigo: {versao_codigo()}")
    criar_schema()
    print("A comparar o que o modelo previa com o que aconteceu (pode demorar 1-2 min)...\n")
    with abrir_sessao() as s:
        c = correcao_ritmo.recalcular_e_gravar(s)
        hist = cartoes_finais.recalcular_e_gravar(s)
    for m, d in correcao_ritmo.RECUPERADAS.items():
        print(f"📅 {m}: recuperei a data do modelo (desde {d:%d/%m}) da copia de seguranca")
    print(correcao_ritmo.formatar(c))
    from apostas.modelos import precisao_equipas
    print("\n" + precisao_equipas.formatar(precisao_equipas.ler()))
    from apostas.modelos import jogadores
    with abrir_sessao() as s:
        print("\n" + jogadores.formatar(jogadores.recalcular_e_gravar(s)))
    print("\n" + cartoes_finais.formatar_historia(hist))
    from apostas.relatorios import prime
    print("\n" + prime.formatar(prime.recalcular_e_gravar()))
    from apostas.relatorios import vs_casa
    print("\n" + vs_casa.formatar(vs_casa.recalcular_e_gravar()))
    from apostas.modelos import arbitros
    with abrir_sessao() as s:
        print("\n" + arbitros.formatar_ranking(arbitros.da_bd(s)))
    print(f"\nGravado em {correcao_ritmo.caminho()} — o scanner usa-o na proxima hora.")
    from apostas.utils.backup import aviso_sem_copias
    aviso = aviso_sem_copias()
    if aviso:
        print("\n" + aviso)


if __name__ == "__main__":
    main()
