"""Graficos do que as dicas enviadas ao Telegram acertaram e falharam.

Tres graficos: por dia (ultimos 30 dias), por semana (ultimas 12) e por mes
(ultimos 12), e um quarto com a curva de aprendizagem das previsoes por equipa
("Porto acaba com 10, Benfica 3": a ±1 em quantas equipas, noite a noite). Cada um tem as barras verdes/vermelhas, a percentagem de acerto,
o lucro acumulado e, em baixo, o acerto de cada mercado. Ficam gravados em
dados\\graficos\\ (acertos_dia.png, acertos_semana.png, acertos_mes.png,
acertos_precisao.png).
Mostra tambem o balanco das multiplas enviadas.

A tarefa da noite ja os envia sozinha (o do dia todas as noites, o da semana a
segunda-feira, o do mes no dia 1). Este script e para os ver quando quiseres.

O grafico comeca no primeiro dia com dicas registadas; datas sempre com o ano
(dd/mm/aaaa, "nov 2026").

Uso:
  venv\\Scripts\\python.exe scripts\\grafico_acertos.py                 # envia os 3 ao Telegram
  venv\\Scripts\\python.exe scripts\\grafico_acertos.py --periodo semana
  venv\\Scripts\\python.exe scripts\\grafico_acertos.py --so-ver --abrir # nao envia, abre no PC
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: emojis sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.relatorios import grafico_acertos as ga  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Graficos de acertos e falhas das dicas.")
    parser.add_argument("--periodo", choices=("dia", "semana", "mes", "precisao", "todos"),
                        default="todos")
    parser.add_argument("--so-ver", action="store_true", help="Nao envia para o Telegram")
    parser.add_argument("--telegram", action="store_true", help=argparse.SUPPRESS)  # antigo
    parser.add_argument("--abrir", action="store_true", help="Abre as imagens (Windows)")
    args = parser.parse_args()
    enviar = not args.so_ver

    agora = agora_utc()
    ate = datetime(agora.year, agora.month, agora.day) + timedelta(days=1)  # inclui hoje
    periodos = (["dia", "semana", "mes", "precisao"] if args.periodo == "todos"
                else [args.periodo])
    if not ga.tem_matplotlib():
        print("⚠ Sem o matplotlib nao ha imagem, so texto. Para a imagem:\n"
              "   venv\\Scripts\\pip install matplotlib\n")
    for periodo in periodos:
        if periodo == "precisao":
            from apostas.relatorios import grafico_precisao
            g = grafico_precisao.gerar()
        else:
            g = ga.gerar(periodo, ate)
        print(g.texto + "\n")
        caminho = ga.gravar_png(g.png, periodo)
        if caminho is not None:
            print(f"🖼  {caminho}\n")
            abrir = getattr(os, "startfile", None)   # so existe no Windows
            if args.abrir and abrir is not None:
                abrir(str(caminho))
        if enviar:
            ga.enviar(g)

    try:
        from apostas.relatorios import multiplas
        from apostas.utils.db import abrir_sessao
        with abrir_sessao() as s:
            print(multiplas.formatar(multiplas.carregar_resolvidas(s)))
    except Exception as exc:  # noqa: BLE001
        print(f"(multiplas: {exc})")
    if enviar:
        print("\n[OK] Enviado para o Telegram.")


if __name__ == "__main__":
    main()
