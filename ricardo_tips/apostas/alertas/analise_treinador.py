"""Analise estilo 'ponto de vista do treinador' para cada equipa.

Dado o estado live (minuto, score, SoT/cantos/cards actuais) + ficha
da equipa (via confronto), gera uma linha curta como se o treinador
estivesse a comentar o que quer da sua equipa agora.

A linha e composta por:
  - Prefixo com o estado do score ("a ganhar 1 ao 70'")
  - Postura escolhida pela regra (controlar, atacar, fechar, ...)
  - Variante da frase da postura (varia por nome + minuto, parte a
    repeticao de 100% dos alertas no mesmo jogo)
  - Observacao NOW opcional: le o ritmo actual (remates/cantos/cards)
    vs baseline da equipa e devolve uma frase do que esta a acontecer

Exemplos:
  'Treinador Roma: a ganhar 1 ao 70'. Gerir ritmo, nao oferecer
   transicoes. Remates acima do normal — a pressionar.'
  'Treinador Milan: a perder 1 ao 65'. Atacar, procurar o golo.
   Amarelos acima do normal — cuidado.'

Nao gera nada quando a ficha tem <5 jogos (ruido demasiado).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Minimo de jogos no historico para arriscar a analise. Abaixo disto
# a ficha nao e fiavel e e melhor ficar em silencio.
MIN_JOGOS = 5


@dataclass
class VozTreinador:
    """Uma linha de 'o que o treinador pensa agora'."""
    nome_equipa: str
    postura: str   # 'atacar' | 'controlar' | 'defender' | 'equilibrar' | 'desespero'
    texto: str


def _diff_do_lado(score_casa: int, score_fora: int, lado: str) -> int:
    """Diferenca de golos do ponto de vista desta equipa."""
    if lado == "casa":
        return score_casa - score_fora
    return score_fora - score_casa


def _decidir_postura(
    diff: int, minuto: int, perfil_dominante: bool, perfil_chasing: bool,
) -> str:
    """Devolve a chave da postura."""
    if diff >= 2:
        if minuto >= 60:
            return "fechar"
        return "controlar"
    if diff == 1:
        if minuto >= 75:
            return "defender" if not perfil_dominante else "controlar"
        if minuto >= 60:
            return "controlar"
        return "equilibrar"
    if diff == 0:
        if minuto >= 75:
            return "arriscar"
        if minuto >= 45:
            return "equilibrar"
        return "equilibrar"
    if diff == -1:
        if minuto >= 75:
            return "desespero" if perfil_chasing else "atacar"
        if minuto >= 60:
            return "atacar"
        return "atacar"
    # diff <= -2
    if minuto >= 70:
        return "aceitar" if not perfil_chasing else "desespero"
    return "atacar"


# Variantes por postura. Escolha deterministica por hash(nome, minuto//10).
# Da variedade entre equipas e ao longo do jogo sem perder repetibilidade
# dentro de uma janela de 10 min.
_VARIANTES = {
    "controlar": [
        "controlo com posse, poupar transicoes",
        "gerir ritmo, nao oferecer o contra-ataque",
        "manter bola, baixar intensidade",
    ],
    "fechar": [
        "fechar o jogo, nao oferecer espacos",
        "baixar bloco, matar o tempo",
        "controlar parciais, acabar com o resultado",
    ],
    "defender": [
        "defender o resultado, aceitar menos bola",
        "baixar linhas, aguentar o que ha",
        "fechar o meio, confiar na transicao",
    ],
    "equilibrar": [
        "equilibrio, esperar oportunidade",
        "ritmo moderado, puxar o jogo para fora",
        "paciencia, forcar erros no meio-campo deles",
    ],
    "atacar": [
        "atacar, procurar o golo",
        "subir blocos, mais homens no ultimo terco",
        "pressao alta, acelerar transicoes",
    ],
    "arriscar": [
        "arriscar, subir blocos no ataque",
        "ir ao resultado, nao aceitar o empate",
        "ganhar as 2as bolas, forcar o momento",
    ],
    "desespero": [
        "desespero final, tudo em cima",
        "lancar tudo no banco, pressao total",
        "guarda-redes a sair nos cantos, no limite",
    ],
    "aceitar": [
        "jogo ja perdido, poupar jogadores para a proxima",
        "aceitar, nao esticar o resultado",
        "gerir amarelos, baixar intensidade",
    ],
}


def _escolher_variante(postura: str, nome: str, minuto: int) -> str:
    opcoes = _VARIANTES.get(postura)
    if not opcoes:
        return ""
    # Hash estavel: mesmo (nome, decada do minuto) → mesma frase; sinais
    # consecutivos no mesmo intervalo nao oscilam, mas 2 jogos diferentes
    # ou a 15' de diferenca ja mostram variantes diferentes.
    idx = (hash((nome, minuto // 10)) & 0xFFFFFFFF) % len(opcoes)
    return opcoes[idx]


def _obs_now(
    minuto: int,
    *,
    remates_atuais: int | None = None,
    cantos_atuais: int | None = None,
    cards_atuais: int | None = None,
    baseline_remates: float | None = None,
    baseline_cantos: float | None = None,
    baseline_cards: float | None = None,
) -> str | None:
    """Observacao NOW baseada em live stats vs baseline por 90'.

    Devolve 1 frase curta ou None se nao ha dados suficientes.
    Prioridade: cards (mais tactico) → remates → cantos.
    """
    if minuto <= 0:
        return None
    rat_minuto = minuto / 90.0

    # Cards: se ja levou mais que o normal, aviso tactico
    if cards_atuais is not None and baseline_cards:
        esperado = baseline_cards * rat_minuto
        if esperado > 0 and cards_atuais >= esperado * 1.6 and cards_atuais >= 2:
            return "amarelos acima do normal — cuidado com expulsoes"

    # Remates: ritmo acima/abaixo do normal
    if remates_atuais is not None and baseline_remates:
        esperado = baseline_remates * rat_minuto
        if esperado > 0:
            if remates_atuais >= esperado * 1.4 and remates_atuais >= 3:
                return "remates acima do normal — a pressionar"
            if remates_atuais <= esperado * 0.5 and minuto >= 30:
                return "jogo preso — poucos remates ate agora"

    # Cantos
    if cantos_atuais is not None and baseline_cantos:
        esperado = baseline_cantos * rat_minuto
        if esperado > 0:
            if cantos_atuais >= esperado * 1.5 and cantos_atuais >= 3:
                return "cantos acima do normal — a atacar pelas pontas"
    return None


def _prefixo_estado(diff: int, minuto: int) -> str:
    if diff >= 2:
        return f"a ganhar por {diff} ao {minuto}'"
    if diff == 1:
        return f"a ganhar por 1 ao {minuto}'"
    if diff == 0:
        return f"empatados ao {minuto}'"
    if diff == -1:
        return f"a perder por 1 ao {minuto}'"
    return f"a perder por {abs(diff)} ao {minuto}'"


def analise_treinador(
    nome: str,
    *,
    diff: int,
    minuto: int,
    jogos_lidos: int,
    remates_a_perder: float | None = None,
    remates_media: float | None = None,
    posse_media: float | None = None,
    cards_media: float | None = None,
    cantos_media: float | None = None,
    # Live NOW (opcional) para a observacao de momentum
    sot_atuais: int | None = None,
    remates_atuais: int | None = None,
    cantos_atuais: int | None = None,
    cards_atuais: int | None = None,
) -> Optional[VozTreinador]:
    """Produz 1 linha estilo treinador. None se nao ha dados suficientes."""
    if jogos_lidos < MIN_JOGOS:
        return None

    perfil_dominante = posse_media is not None and posse_media >= 56
    perfil_chasing = (
        remates_a_perder is not None and remates_media is not None
        and remates_a_perder > remates_media * 1.15
    )

    postura = _decidir_postura(diff, minuto, perfil_dominante, perfil_chasing)
    accao = _escolher_variante(postura, nome, minuto)
    prefixo = _prefixo_estado(diff, minuto)

    # Tag de perfil quando util (nao duplica o que a variante ja disse)
    tag = ""
    if postura in ("atacar", "desespero") and perfil_chasing:
        tag = " · perfil mostra +remates quando atras"
    elif postura == "controlar" and perfil_dominante:
        tag = " · dominante com posse"
    elif postura == "defender" and not perfil_dominante:
        tag = " · nao e perfil de posse, fechar e OK"

    # Observacao NOW: usa SoT se disponivel (mais apertado), senao remates.
    obs_fonte_remates = sot_atuais if sot_atuais is not None else remates_atuais
    obs = _obs_now(
        minuto,
        remates_atuais=obs_fonte_remates,
        cantos_atuais=cantos_atuais,
        cards_atuais=cards_atuais,
        baseline_remates=remates_media,
        baseline_cantos=cantos_media,
        baseline_cards=cards_media,
    )
    obs_str = f" · {obs}" if obs else ""

    texto = f"Treinador {nome}: {prefixo}. {accao}.{tag}{obs_str}"
    return VozTreinador(nome_equipa=nome, postura=postura, texto=texto)


def gerar_para_ambas(
    home_id: int,
    away_id: int,
    *,
    minuto: int,
    score_casa: int,
    score_fora: int,
    sot_c: int | None = None,
    sot_f: int | None = None,
    cantos_c: int | None = None,
    cantos_f: int | None = None,
    cards_c: int | None = None,
    cards_f: int | None = None,
    confronto_fn=None,
) -> tuple[Optional[VozTreinador], Optional[VozTreinador]]:
    """Dado 2 team_ids, consulta confronto.analisar_confronto e devolve
    (voz_casa, voz_fora). Qualquer uma pode ser None se dados insuficientes.
    """
    if confronto_fn is None:
        try:
            from apostas.alertas.confronto import analisar_confronto
            confronto_fn = analisar_confronto
        except Exception:  # noqa: BLE001
            return None, None

    try:
        res = confronto_fn(
            home_id, away_id,
            minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        )
    except Exception:  # noqa: BLE001
        return None, None
    if res is None:
        return None, None

    diff_casa = _diff_do_lado(score_casa, score_fora, "casa")
    diff_fora = _diff_do_lado(score_casa, score_fora, "fora")

    fc, ff = res.ficha_casa, res.ficha_fora
    voz_c = analise_treinador(
        fc.nome, diff=diff_casa, minuto=minuto,
        jogos_lidos=fc.jogos_lidos,
        remates_a_perder=fc.por_resultado.remates_L,
        remates_media=fc.baseline.remates,
        posse_media=fc.baseline.posse_pct,
        cards_media=fc.baseline.cards_amarelos,
        cantos_media=fc.baseline.cantos,
        sot_atuais=sot_c,
        cantos_atuais=cantos_c,
        cards_atuais=cards_c,
    )
    voz_f = analise_treinador(
        ff.nome, diff=diff_fora, minuto=minuto,
        jogos_lidos=ff.jogos_lidos,
        remates_a_perder=ff.por_resultado.remates_L,
        remates_media=ff.baseline.remates,
        posse_media=ff.baseline.posse_pct,
        cards_media=ff.baseline.cards_amarelos,
        cantos_media=ff.baseline.cantos,
        sot_atuais=sot_f,
        cantos_atuais=cantos_f,
        cards_atuais=cards_f,
    )
    return voz_c, voz_f


def linhas_formatadas_para_telegram(
    home_id: int,
    away_id: int,
    *,
    minuto: int,
    score_casa: int,
    score_fora: int,
    sot_c: int | None = None,
    sot_f: int | None = None,
    cantos_c: int | None = None,
    cantos_f: int | None = None,
    cards_c: int | None = None,
    cards_f: int | None = None,
    confronto_fn=None,
) -> str:
    """Devolve 0, 1 ou 2 linhas prefixadas com '🎙 ...' prontas a juntar.

    Comeca com '\\n' quando ha pelo menos 1 linha, para colar atras do
    corpo de uma mensagem Telegram. Vazio quando ambas as fichas sao
    insuficientes.
    """
    voz_c, voz_f = gerar_para_ambas(
        home_id, away_id,
        minuto=minuto, score_casa=score_casa, score_fora=score_fora,
        sot_c=sot_c, sot_f=sot_f,
        cantos_c=cantos_c, cantos_f=cantos_f,
        cards_c=cards_c, cards_f=cards_f,
        confronto_fn=confronto_fn,
    )
    linhas = []
    if voz_c is not None:
        linhas.append(f"🎙 {voz_c.texto}")
    if voz_f is not None:
        linhas.append(f"🎙 {voz_f.texto}")
    if not linhas:
        return ""
    return "\n" + "\n".join(linhas)
