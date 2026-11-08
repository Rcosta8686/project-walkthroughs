"""Incremento diario (tarefa das 00:15): nenhum jogo terminado fica de fora.

O Windows da 4 horas a esta tarefa. Primeiro o que e rapido e local (fica
feito mesmo que a internet demore), depois o que vai ao Sofascore, com prazo:

1. Copia de seguranca da BD e de dados/tracking (dados/backups, 7 dias).
2. Tira amigaveis/seleccoes que tenham entrado.
3. Aprendizagem com o que ja esta na BD: ritmo do resto do jogo, precisao por
   equipa, cartao no fim, PRIME, nos vs casas.
4. Para cada liga que o scanner segue, grava estatisticas de equipa e de
   jogadores dos jogos terminados nos ultimos --dias (default 3). O que ja esta
   na BD e saltado. Para quando acaba o prazo (--horas, default 3).
5. Resolver dos alertas desses dias (tambem com o prazo).
6. Se entraram jogos novos e ainda ha tempo, aprende outra vez com eles.
7. Graficos para o Telegram do que as dicas acertaram e falharam: o do dia
   todas as noites, o da semana a segunda, o do mes no dia 1 (cada um uma vez
   so), e as multiplas de ontem selecao a selecao.

Cada passo fica no log (logs/ricardo_tips.log) com a hora, para se ver ate
onde chegou.

Uso:
  venv\\Scripts\\python.exe scripts\\incremento_diario.py
  venv\\Scripts\\python.exe scripts\\incremento_diario.py --dias 7 --telegram
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS.parent))
sys.path.insert(0, str(_SCRIPTS))

for _stream in (sys.stdout, sys.stderr):  # consola Windows: emojis sem crash
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass

from apostas.ingestao._flaresolverr import FlaresolverrIndisponivel  # noqa: E402
from apostas.modelos import incremento_diario  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402
from apostas.utils.tempo import agora_utc  # noqa: E402

log = get_logger(__name__)


def _passo(nome: str, fn) -> None:
    """Corre um passo sem deixar que um erro pare os outros; fica no log."""
    log.info("Noite: %s...", nome)
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("Noite: %s falhou: %s", nome, exc)
        print(f"⚠ {nome} falhou: {exc}")


def _copia() -> None:
    from apostas.utils.backup import fazer_backup
    print("\n" + fazer_backup().texto())


def _limpeza() -> None:
    from apostas.utils.limpeza_competicoes import limpar
    r = limpar()
    if not r.vazio:
        print("\n" + r.texto())


def _aprender() -> None:
    """Tudo o que aprende com a BD (local, uns minutos)."""
    from apostas.modelos import cartoes_finais, correcao_ritmo, precisao_equipas
    from apostas.utils.db import abrir_sessao

    def ritmo() -> None:
        with abrir_sessao() as s:
            c = correcao_ritmo.recalcular_e_gravar(s)
        print("\n" + correcao_ritmo.formatar(c))
        print("\n" + precisao_equipas.formatar(precisao_equipas.ler()))

    def cartoes() -> None:
        with abrir_sessao() as s:
            print("\n" + cartoes_finais.formatar_historia(cartoes_finais.recalcular_e_gravar(s)))

    def prime_() -> None:
        from apostas.relatorios import prime
        print("\n" + prime.formatar(prime.recalcular_e_gravar()))

    def casas() -> None:
        from apostas.relatorios import vs_casa
        print("\n" + vs_casa.formatar(vs_casa.recalcular_e_gravar()))
    def jogadores_() -> None:
        from apostas.modelos import jogadores
        with abrir_sessao() as s:
            print("\n" + jogadores.formatar(jogadores.recalcular_e_gravar(s)))
    _passo("ritmo e precisao por equipa", ritmo)
    _passo("jogadores", jogadores_)
    _passo("cartao no fim", cartoes)
    _passo("PRIME", prime_)
    _passo("nos vs casas", casas)


def _graficos(agora: datetime) -> None:
    """Grafico(s) de acertos/falhas e as multiplas de ontem, para o Telegram."""
    from apostas.alertas.telegram_cliente import cliente
    from apostas.relatorios import grafico_acertos as ga
    from apostas.relatorios import multiplas
    from apostas.utils.db import abrir_sessao
    ate = datetime(agora.year, agora.month, agora.day)
    for periodo in ga.periodos_da_noite(ate):
        if ga.ja_enviado(periodo, ate):
            continue
        g = ga.gerar(periodo, ate)
        ga.gravar(g)
        print("\n" + g.texto)
        if not g.vazio:
            ga.enviar(g)
            ga.marcar_enviado(periodo, ate)
    if ga.ja_enviado("multiplas", ate):
        return
    with abrir_sessao() as s:
        ontem = multiplas.carregar_resolvidas(s, ate - timedelta(days=1), ate)
        todas = multiplas.carregar_resolvidas(s) if ontem else []
    if ontem:
        texto = "\n\n".join([multiplas.texto_multiplas(
            ontem, f"🎰 Multiplas de {ate - timedelta(days=1):%d/%m}"), multiplas.formatar(todas)])
        print("\n" + texto)
        cliente().enviar(texto)
        ga.marcar_enviado("multiplas", ate)


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremento diario dos mini-cerebros.")
    parser.add_argument("--dias", type=int, default=3)
    parser.add_argument("--horas", type=float, default=3.0,
                        help="Tempo maximo para ir ao Sofascore (o Windows corta as 4h)")
    parser.add_argument("--sem-resolver", action="store_true")
    parser.add_argument("--telegram", action="store_true", help="Envia o resumo")
    parser.add_argument("--sem-graficos", action="store_true",
                        help="Nao envia os graficos de acertos/falhas")
    args = parser.parse_args()

    import time as _time

    from apostas.utils import prazo
    from apostas.utils.db import criar_schema
    from apostas.utils.versao import versao_codigo
    t0 = _time.monotonic()
    log.info("Noite: arrancou (versao %s)", versao_codigo())
    criar_schema()

    # 1-3. Rapido e local: fica feito mesmo que o Sofascore demore.
    _passo("copia de seguranca", _copia)
    _passo("limpeza de amigaveis/seleccoes", _limpeza)
    _aprender()

    # 4-5. Sofascore, com prazo (o Windows mata a tarefa as 4 horas).
    prazo.definir(datetime.now() + timedelta(hours=args.horas))
    agora = agora_utc()
    print(f"\nIncremento diario: jogos terminados desde "
          f"{agora - timedelta(days=args.dias):%d/%m %H:%M} UTC\n")
    texto = ""
    novos = 0
    log.info("Noite: jogos terminados das ligas...")
    try:
        resumo = incremento_diario.correr(dias=args.dias, agora=agora)
        texto, novos = resumo.texto(), resumo.jogos_novos
        print("\n" + texto)
        log.info("Incremento diario: %s", texto.replace("\n", " | "))
    except FlaresolverrIndisponivel as exc:
        print(f"⚠ Flaresolverr indisponivel ({exc}). Volta a tentar amanha;"
              " para ja: docker start flaresolverr")
        log.warning("Noite: flaresolverr indisponivel: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Noite: incremento falhou: %s", exc)

    if not args.sem_resolver and not prazo.esgotado():
        import resolver_sugestoes_live
        inicio = datetime(agora.year, agora.month, agora.day) - timedelta(days=args.dias)
        print(f"\nA resolver alertas desde {inicio:%d/%m}...\n")
        _passo("resolver", lambda: resolver_sugestoes_live.resolver(desde=inicio))

    # 6. Jogos novos e ainda ha tempo: aprende tambem com eles.
    if novos and not prazo.esgotado():
        _aprender()
    prazo.definir(None)

    # 7. Local e rapido: os graficos saem mesmo que o Sofascore tenha falhado.
    if not args.sem_graficos:
        _passo("graficos de acertos", lambda: _graficos(agora_utc()))
    log.info("Noite: terminou em %.0f min", (_time.monotonic() - t0) / 60)

    if args.telegram and texto:
        from apostas.alertas.telegram_cliente import cliente
        cliente().enviar(texto)


if __name__ == "__main__":
    main()
