"""Analise estilo 'ponto de vista do treinador' para cada equipa.

Dado o estado live (minuto, score) + ficha da equipa (via confronto),
gera uma linha curta como se o treinador estivesse a comentar o que
quer da sua equipa agora.

Exemplos de output:
  'Treinador Roma: a ganhar 2-1 ao 70. Equipa dominante, controlo
   com posse. Fechar jogo sem oferecer transicoes.'
  'Treinador Milan: a perder 1-2. Perfil mostra +18% remates quando
   atras — vamos atacar com tudo nos ultimos 20min.'

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
        # 2 golos acima — gestao
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


_TEXTOS = {
    "controlar": "controlo com posse, poupar transicoes",
    "fechar": "fechar o jogo, nao oferecer espacos",
    "defender": "defender o resultado, aceitar menos bola",
    "equilibrar": "equilibrio, esperar oportunidade",
    "atacar": "atacar, procurar o golo",
    "arriscar": "arriscar, subir blocos no ataque",
    "desespero": "desespero final, tudo em cima",
    "aceitar": "jogo ja perdido, poupar jogadores para a proxima",
}


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
) -> Optional[VozTreinador]:
    """Produz 1 linha estilo treinador. None se nao ha dados suficientes.

    `remates_a_perder` e `remates_media` ajudam a decidir se a equipa
    'perfil chasing' (empurra quando atras). `posse_media` detecta
    perfil dominante.
    """
    if jogos_lidos < MIN_JOGOS:
        return None

    # Perfis derivados da ficha
    perfil_dominante = posse_media is not None and posse_media >= 56
    perfil_chasing = (
        remates_a_perder is not None and remates_media is not None
        and remates_a_perder > remates_media * 1.15
    )

    postura = _decidir_postura(diff, minuto, perfil_dominante, perfil_chasing)
    accao = _TEXTOS[postura]

    # Verbo inicial muda consoante estado do score
    if diff >= 2:
        prefixo = f"a ganhar por {diff} ao {minuto}'"
    elif diff == 1:
        prefixo = f"a ganhar por 1 ao {minuto}'"
    elif diff == 0:
        prefixo = f"empatados ao {minuto}'"
    elif diff == -1:
        prefixo = f"a perder por 1 ao {minuto}'"
    else:
        prefixo = f"a perder por {abs(diff)} ao {minuto}'"

    # Nota sobre o perfil quando util
    tag = ""
    if postura in ("atacar", "desespero") and perfil_chasing:
        tag = " · perfil mostra +remates quando atras"
    elif postura == "controlar" and perfil_dominante:
        tag = " · dominante com posse"
    elif postura == "defender" and not perfil_dominante:
        tag = " · nao e perfil de posse, fechar e OK"

    texto = f"Treinador {nome}: {prefixo}. {accao}.{tag}"
    return VozTreinador(nome_equipa=nome, postura=postura, texto=texto)


def gerar_para_ambas(
    home_id: int,
    away_id: int,
    *,
    minuto: int,
    score_casa: int,
    score_fora: int,
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
    )
    voz_f = analise_treinador(
        ff.nome, diff=diff_fora, minuto=minuto,
        jogos_lidos=ff.jogos_lidos,
        remates_a_perder=ff.por_resultado.remates_L,
        remates_media=ff.baseline.remates,
        posse_media=ff.baseline.posse_pct,
        cards_media=ff.baseline.cards_amarelos,
    )
    return voz_c, voz_f
