"""Analista independente: emite sinais com reasoning narrativo.

Diferenca face a versao anterior: usa TODOS os sinais do Sofascore
(defesa fraca, momentum, qualidade de distribuicao, duelos, big chances,
etc.) para gerar racionais credíveis.

Cada Sinal traz um `racional` que o Telegram transforma em narrativa
tipo:

  "⚽ OVER 1.5 golos ao min 35 (72%)
   Girona a dominar: 62% posse, 3 big chances, keeper 4 defesas.
   Mallorca defesa a ceder: so 44% tackles ganhos, 11 clearances.
   Momentum nos ultimos 10min: Girona (xG 0.6 vs 0.1)
   Odd alvo 1.40 — procura em casa."

Nao e opiniao vazia — e observacao de dados concretos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apostas.ingestao.sofascore_live import EstadoLive
    from apostas.modelos.live_predictor import PrevisaoLive


# Linhas STANDARD oferecidas pelas casas de apostas (not inventadas pelo
# modelo). Modelo so emite para linhas que casas efectivamente abrem.
# Para cada mercado, lista as linhas tipicas do pre-match / live.
LINHAS_MERCADO = {
    "golos":     [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
    "cantos":    [6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5],
    "cartoes":   [2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5],
    "remates":   [16.5, 18.5, 20.5, 22.5, 24.5, 26.5, 28.5, 30.5],
    "remates_a_baliza": [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
}


# ──────── Contexto de mercado (Caan Berry: 5 hotspots) ────────
# Multiplicador aplicado ao score de ranking do sinal. Nao distorce
# a prob_modelo (nao mente sobre probabilidade); apenas prioriza
# sinais em ligas/mercados onde o edge do bookmaker e menor.

# Hotspot 1+5: ligas pouco monitoradas = pricing menos eficiente.
# Chaves em LOWERCASE para substring matching (Sofascore devolve nomes
# com variacoes: "Serie A" OR "Serie A (ITA)" OR "Campeonato Italiano").
# Primeira correspondencia vence (ordem importa — especıficos primeiro).
EFICIENCIA_LIGA = [
    # Segundas divisoes e menos eficientes (BOOST — mais edge)
    ("championship",         1.15),
    ("laliga 2",             1.15),
    ("hypermotion",          1.15),  # alias LaLiga 2
    ("serie b",              1.15),
    ("bundesliga 2",         1.15),
    ("ligue 2",              1.15),
    ("liga portugal 2",      1.18),
    # Tacas (cobertura ad-hoc, menos linhas, mais erros)
    ("fa cup",               1.08),
    ("copa del rey",         1.08),
    ("coppa italia",         1.08),
    ("dfb-pokal",            1.08),
    ("coupe de france",      1.08),
    ("taca de portugal",     1.10),
    ("taça de portugal",     1.10),
    # Conference / UEL (margens menos apertadas que UCL)
    ("conference league",    1.05),
    ("europa league",        1.02),
    # Liga Portugal (menor que Big-5)
    ("liga portugal",        1.05),
    ("portugal betclic",     1.05),
    # Baseline eficiente — Big-5 e UCL (margens apertadas)
    ("premier league",       1.00),
    ("champions league",     1.00),
    ("bundesliga",           1.00),
    ("ligue 1",              1.00),
    ("laliga",               1.00),
    ("serie a",              1.00),
    # Brasileirao: dados Ricardo mostraram -42% ROI em 190 sinais.
    # Downgrade severo ate termos modelo que entenda esse futebol.
    ("brasileir",            0.60),   # casa com stats menos previsiveis
    ("campeonato brasileiro",0.60),
]

# Ligas onde o modelo esta DEMONSTRAVELMENTE a perder dinheiro.
# Sinais destas ligas sao ainda emitidos para terminal, mas NAO para Telegram.
# Revisto 2026-10-12 com 1680 sinais: LaLiga 2 saiu (n=379, +6.8% ROI,
# a amostra inicial de 54 sinais era enganadora). Brasileirao continua
# com n=447 e ROI -14% — mantido.
LIGAS_BLACKLIST_TELEGRAM = frozenset({
    "brasileir",              # n=447, ROI -14%, perda -62u (ainda o pior)
    "campeonato brasileiro",
})


def _match_liga_substring(liga: str, mapa: list) -> float:
    """Procura primeira chave que e substring do nome da liga (case-insensitive)."""
    n = (liga or "").lower()
    for chave, valor in mapa:
        if chave in n:
            return valor
    return 1.0


def liga_na_blacklist(liga: str) -> bool:
    """True se a liga esta a perder sistematicamente nos dados reais."""
    n = (liga or "").lower()
    return any(k in n for k in LIGAS_BLACKLIST_TELEGRAM)

# Hotspot 2: equipas mediaticas atraem dinheiro recreacional por
# paixao → odds a favor comprimem, odds contra inflacionam. Fade.
# Match case-insensitive substring.
EQUIPAS_MEDIATICAS = frozenset({
    "real madrid", "barcelona", "atletico madrid", "atlético madrid",
    "manchester united", "manchester city", "liverpool", "arsenal",
    "chelsea", "tottenham",
    "juventus", "milan", "inter", "napoli",
    "bayern", "borussia dortmund", "dortmund",
    "paris saint-germain", "psg",
    "porto", "benfica", "sporting",
    "flamengo", "palmeiras", "corinthians", "são paulo", "santos",
})

# Hotspot 5 refinado: em Big-5, bookmakers focam em 1X2/golos.
# Mercados nicho (cantos, cartoes, SoT) tem pricing menos afiado.
# Lowercase substrings para matching robusto.
_LIGAS_BIG5_SUBSTR = ("premier league", "laliga", "serie a",
                      "bundesliga", "ligue 1")
_MERCADOS_NICHE = frozenset({"cantos", "cartoes", "remates", "remates_a_baliza"})


def _eh_big5(liga: str) -> bool:
    n = (liga or "").lower()
    # Exclui segundas divisoes: "laliga 2", "serie b", etc.
    if any(s in n for s in ("laliga 2", "hypermotion", "serie b",
                             "bundesliga 2", "ligue 2", "championship")):
        return False
    return any(s in n for s in _LIGAS_BIG5_SUBSTR)


def _eh_mediatica(nome_equipa: str) -> bool:
    """True se o nome contem alguma substring de equipa mediatica."""
    n = (nome_equipa or "").lower()
    return any(m in n for m in EQUIPAS_MEDIATICAS)


def fator_mercado(liga: str, home: str, away: str,
                   mercado: str, lado: str) -> float:
    """Multiplicador composto a aplicar ao score de ranking do sinal.

    - Ligas pouco eficientes: boost
    - Mercado nicho em Big-5: boost extra
    - Apostar CONTRA equipa mediatica: boost (odds inflacionadas)
    - Apostar A FAVOR de equipa mediatica: pequeno desconto

    Nao distorce a prob_modelo. So prioriza o ranking.
    """
    liga = liga or ""
    f = _match_liga_substring(liga, EFICIENCIA_LIGA)

    if _eh_big5(liga) and mercado in _MERCADOS_NICHE:
        f *= 1.10

    if mercado == "1x2":
        casa_med = _eh_mediatica(home)
        fora_med = _eh_mediatica(away)
        if lado == "casa":
            if casa_med and not fora_med:
                f *= 0.92
            elif fora_med and not casa_med:
                f *= 1.08
        elif lado == "fora":
            if fora_med and not casa_med:
                f *= 0.92
            elif casa_med and not fora_med:
                f *= 1.08
        elif lado == "empate" and (casa_med or fora_med):
            f *= 1.05

    elif mercado == "golo_equipa":
        lado_eh_mediatica = (
            (lado == "marca_casa" and _eh_mediatica(home))
            or (lado == "marca_fora" and _eh_mediatica(away))
        )
        if lado_eh_mediatica:
            f *= 0.94

    return f


def _fator_mercado(sinal: "Sinal", estado: "EstadoLive") -> float:
    """Variante sobre Sinal+EstadoLive. Delega em fator_mercado."""
    return fator_mercado(estado.liga, estado.home, estado.away,
                          sinal.mercado, sinal.lado)


# Hotspot 2 (Caan Berry): fade do favorito pos-intervalo.
# Underdog a ganhar 1-0 ao min 45-60 → publico martela comeback do
# favorito → odds do underdog aguentar INFLACIONAM. Boost prob.
_BOOST_FADE_HT = 0.06  # +6pp na prob do underdog aguentar


def _ajuste_fade_favorito(estado: "EstadoLive", prev: "PrevisaoLive") -> tuple[float, float, float, str | None]:
    """Devolve (p_casa_ajust, p_empate_ajust, p_fora_ajust, racional).

    Se nao se aplica, devolve as probs originais e racional=None.
    """
    minuto = estado.minuto
    if minuto < 45 or minuto > 65:
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None
    diff = estado.marcador_casa - estado.marcador_fora
    if abs(diff) != 1:
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None

    # Identificar favorito via rating medio OU equipa mediatica
    casa_med = _eh_mediatica(estado.home)
    fora_med = _eh_mediatica(estado.away)
    rating_c = estado.rating_medio_casa
    rating_f = estado.rating_medio_fora
    rating_delta = (rating_c - rating_f) if (rating_c and rating_f) else 0.0

    # Favorito = mediatica OR melhor rating (delta > 0.25)
    favorito_casa = (casa_med and not fora_med) or rating_delta > 0.25
    favorito_fora = (fora_med and not casa_med) or rating_delta < -0.25

    if not favorito_casa and not favorito_fora:
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None

    # Favorito a perder?
    favorito_a_perder = (favorito_casa and diff < 0) or (favorito_fora and diff > 0)
    if not favorito_a_perder:
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None

    # Momentum nao pode CONTRADIZER o underdog (se o favorito tem
    # momentum claro, nao vale fade — o jogo esta mesmo a virar).
    if favorito_casa and estado.momentum_actual == "casa":
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None
    if favorito_fora and estado.momentum_actual == "fora":
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None

    # Fade so e util quando o modelo tem incerteza (prob underdog 30-60%).
    # Se o modelo ja concorda fortemente (>60%), as odds nao estao viciadas
    # — nao ha arbitragem publica para explorar.
    p_c, p_x, p_f = prev.p_casa_vence, prev.p_empate, prev.p_fora_vence
    prob_underdog_actual = p_f if favorito_casa else p_c
    if prob_underdog_actual < 0.30 or prob_underdog_actual > 0.60:
        return prev.p_casa_vence, prev.p_empate, prev.p_fora_vence, None

    if favorito_casa:
        p_f = min(0.90, p_f + _BOOST_FADE_HT)
        p_c = max(0.05, p_c - _BOOST_FADE_HT)
    else:
        p_c = min(0.90, p_c + _BOOST_FADE_HT)
        p_f = max(0.05, p_f - _BOOST_FADE_HT)
    nome_underdog = estado.away if favorito_casa else estado.home
    nome_favorito = estado.home if favorito_casa else estado.away
    racional = (f"Fade sentimental: publico martela {nome_favorito} a reagir, "
                f"odds do {nome_underdog} aguentar inflacionam")
    return p_c, p_x, p_f, racional


@dataclass
class Sinal:
    tipo: str
    mercado: str
    lado: str
    linha: float | None
    prob_modelo: float
    odd_minima: float
    odd_valor: float
    racional: list[str] = field(default_factory=list)
    forca: str = "normal"
    titulo: str = ""  # headline que capture o insight principal

    @property
    def forca_num(self) -> int:
        return {"forte": 3, "normal": 2, "fraco": 1}.get(self.forca, 0)


def _classifica_forca(prob: float) -> str:
    if prob >= 0.85:
        return "forte"
    if prob >= 0.70:
        return "normal"
    if prob >= 0.55:
        return "fraco"
    return "ignora"


def _odds_minimas(prob: float, ev_desejado: float = 0.05) -> tuple[float, float]:
    prob_c = min(max(prob, 0.01), 0.95)
    return round(1.0 / prob_c, 2), round((1.0 + ev_desejado) / prob_c, 2)


def _add(out: list, tipo: str, mercado: str, lado: str, linha: float | None,
         prob: float, racional: list[str], titulo: str = "",
         estado=None, prev=None) -> None:
    # Calibracao: aplica blend 40/60 com hit rate historico do bucket.
    # Dados Ricardo mostraram shift -15pp sistematico em todos os buckets.
    try:
        from apostas.modelos.live_predictor import calibrar_prob
        prob = calibrar_prob(prob, mercado)
    except Exception:  # noqa: BLE001
        pass
    # Filtro contextual late game: estado real do jogo em vez de relogio.
    # Jogo sem stakes relaxa o filtro para under/BTTS NAO (ambos aceitam
    # ponto) e aperta para overs. Jogo importante faz o oposto.
    if estado is not None and prev is not None:
        valido, razao = _contexto_late_valido(estado, prev, mercado, lado)
        if not valido:
            return
    if prob > 0.92:
        return
    forca = _classifica_forca(prob)
    if forca == "ignora":
        return
    odd_be, odd_val = _odds_minimas(prob)
    if odd_val < 1.15:
        return
    out.append(Sinal(
        tipo=tipo, mercado=mercado, lado=lado, linha=linha,
        prob_modelo=prob, odd_minima=odd_be, odd_valor=odd_val,
        racional=racional, forca=forca, titulo=titulo,
    ))


# Minutos minimos por mercado. Dados Ricardo (1680 sinais) mostraram:
#   0-30'  hit 49.5%  ROI -27.8%
#   30-45' hit 63.2%  ROI  -6.5%
#   45-60' hit 75.0%  ROI +10.6%  ← sweet spot
#   60-75' hit 76.0%  ROI  +9.6%  ← sweet spot
#   75+'   hit 66.5%  ROI -11.3%  ← zona de erro (intensidade satura)
MINUTO_MIN_POR_MERCADO = {
    "1x2":              50,  # resultado final — favorito so emerge no 2o tempo
    "golos":            45,  # score shape consolida apos intervalo
    "btts":             45,
    "cantos":           40,
    "cartoes":          40,
    "remates":          40,
    "remates_a_baliza": 50,
}

# Safety net absoluta — nos ultimos minutos finais raramente ha
# tempo para novo evento. NAO e cutoff baseado em performance; e
# limite fisico. A analise contextual (_contexto_late_valido) decide
# o resto baseado no estado real do jogo.
MINUTO_MAX_POR_MERCADO = {
    "1x2":              88,
    "golos":            88,
    "btts":             88,
    "cantos":           88,
    "cartoes":          90,
    "remates":          88,
    "remates_a_baliza": 85,
}

# Conviccao minima (prob × odd_valor) para EMITIR o sinal no Telegram.
# Sinais abaixo deste limiar aparecem no terminal mas nao notificam.
CONVICCAO_MINIMA_TELEGRAM = 1.10  # ~10% edge esperado


def _estado_demasiado_cedo_para_sinal(estado: "EstadoLive", mercado: str = "golos") -> bool:
    """True se o jogo esta fora da janela util para este mercado.

    Minuto minimo e maximo sao safety nets (modelo nao fiavel cedo demais,
    tempo fisico esgotado tarde demais). O "cutoff do min 75" nao e um
    muro — ver `_contexto_late_valido` para julgar o 2o tempo final por
    estado do jogo em vez de pelo relogio.
    """
    minuto = estado.minuto
    min_min = MINUTO_MIN_POR_MERCADO.get(mercado, 25)
    min_max = MINUTO_MAX_POR_MERCADO.get(mercado, 88)
    return minuto < min_min or minuto > min_max


def _liga_id_para_classif(liga: str) -> int | None:
    """Procura o tournament_id Sofascore a partir do nome da liga."""
    try:
        from apostas.ingestao.sofascore_live import LIGAS_SOFASCORE_IDS
    except Exception:  # noqa: BLE001
        return None
    n = (liga or "").lower()
    for name, id_ in LIGAS_SOFASCORE_IDS.items():
        if name.lower() in n or n in name.lower():
            return id_
    return None


def importancia_do_jogo(estado) -> tuple[float, str | None]:
    """Factor 0.7-1.3 + descricao, baseado na classificacao da liga.

    Usa cache de 12h em disco (sofascore_classificacao). Falha silenciosa:
    devolve (1.0, None) se nao conseguir obter a tabela.
    """
    liga_id = _liga_id_para_classif(estado.liga)
    if not liga_id:
        return 1.0, None
    try:
        from apostas.ingestao.sofascore_classificacao import importancia_jogo
        return importancia_jogo(liga_id, estado.home, estado.away)
    except Exception:  # noqa: BLE001
        return 1.0, None


def _contexto_late_valido(estado: "EstadoLive", prev: "PrevisaoLive",
                           mercado: str, lado: str) -> tuple[bool, str | None]:
    """Analise contextual de 2a parte final (min 70+).

    Em vez de cortar por relogio, examina o ESTADO REAL do jogo:
      - Jogo decidido (diff >= 3): intensidade colapsa, nao emite overs
      - Empate late com baixo xG recent: ambos aceitam empate, nao emite
        overs (golos/BTTS/cantos/remates) mas ok para unders
      - Jogo apertado com chasing REAL (momentum do perdedor, big chances
        pendentes, xG recent alto): OPORTUNIDADE, mantem sinal
      - 1X2 com underdog a aguentar vantagem no fim: ok se nao for lixo
      - IMPORTANCIA DO JOGO (classificacao): title race/descida relaxa
        filtros (jogo importa, equipas vao arriscar), sem stakes aperta.

    Devolve (valido, razao_rejeicao). razao=None quando valido.
    Antes do minuto 70 passa sempre (ja filtrado por _estado_demasiado_cedo).
    """
    if estado.minuto < 70:
        return True, None

    # Importancia do jogo altera os cortes:
    #  - importancia >= 1.2 → jogo-chave, equipas vao arriscar → RELAXA
    #  - importancia <= 0.8 → sem stakes, aceita o ponto → APERTA
    importancia, _ = importancia_do_jogo(estado)

    diff = abs(estado.marcador_casa - estado.marcador_fora)
    xg_rec_c = estado.xg_recent_casa or 0
    xg_rec_f = estado.xg_recent_fora or 0
    xg_rec_tot = xg_rec_c + xg_rec_f
    bc_c = estado.big_chances_casa or 0
    bc_f = estado.big_chances_fora or 0
    bc_pendentes = max(0, (bc_c + bc_f) - (estado.marcador_casa + estado.marcador_fora))

    # 1. Jogo decidido (3+ golos de diferenca) → intensidade colapsa
    #    Qualquer "over" fica a depender de garbage time → cortar.
    if diff >= 3:
        if lado in ("over", "sim", "marca_casa", "marca_fora"):
            return False, "Jogo decidido (3+ golos diff) — intensidade final baixa"
        # Unders e 1X2 do lider ok

    # 2. Empate late (min 75+) com xG recente colapsado → ambos aceitam ponto
    #    Jogo importante (title race / descida): equipas vao arriscar mesmo
    #    com xG baixo → NAO corta. Jogo sem stakes: xG baixo e definitivo.
    if (estado.minuto >= 75 and diff == 0 and xg_rec_tot < 0.15
            and bc_pendentes == 0 and importancia < 1.2):
        if lado in ("over", "sim"):
            if importancia <= 0.8:
                return False, f"Empate ao {estado.minuto}' sem stakes — ambos aceitam ponto"
            return False, f"Empate ao {estado.minuto}' com xG ultimos 15min {xg_rec_tot:.2f} — jogo tranca"

    # 3. 1-golo late (min 80+) sem chasing real → favorito gere o tempo.
    #    Jogo importante: perdedor ARRISCA tudo, mesmo sem metricas de
    #    chasing acumuladas → relaxa. Sem stakes: perdedor desiste.
    if estado.minuto >= 80 and diff == 1 and importancia < 1.2:
        casa_perde = estado.marcador_casa < estado.marcador_fora
        xg_rec_perdedor = xg_rec_c if casa_perde else xg_rec_f
        mom_perdedor = (estado.momentum_actual == "casa" and casa_perde) \
                       or (estado.momentum_actual == "fora" and not casa_perde)
        bc_perdedor = bc_c if casa_perde else bc_f
        # Em jogo sem stakes, exige MAIS evidencia de chasing (threshold maior)
        xg_min_chasing = 0.4 if importancia <= 0.8 else 0.3
        chasing_real = mom_perdedor or bc_perdedor >= 2 or xg_rec_perdedor >= xg_min_chasing

        if not chasing_real:
            if lado in ("over", "sim", "marca_casa", "marca_fora"):
                suffix = " (jogo sem stakes)" if importancia <= 0.8 else ""
                return False, f"Lider gere {estado.minuto}+ sem chasing real{suffix} — jogo controlado"
            if mercado == "1x2":
                perdedor_lado = "casa" if casa_perde else "fora"
                if lado == perdedor_lado:
                    return False, f"Perdedor sem chasing ao {estado.minuto}' — virada improvavel"

    # 4. SoT especifico: late game SoT = tiros desesperados fora
    #    Se minuto >= 80 e nao ha pressao (xG recent baixo), SoT nao cai
    if mercado == "remates_a_baliza" and estado.minuto >= 80 and lado == "over":
        if xg_rec_tot < 0.2 and bc_pendentes == 0:
            return False, f"Pressao baixa ao {estado.minuto}' (xG recent {xg_rec_tot:.2f}) — SoT nao vem"

    return True, None


def _conviccao_alta(sinal: Sinal, estado: "EstadoLive") -> bool:
    """Criterio Paulo Rebelo: 'tenho edge matematico sobre o mercado?'

    Nao e 'tenho certeza'. E 'tenho esperança positiva com disciplina'.
    Volume de bets +EV ao longo do tempo vence. Filtros:

      1. Prob minima racional (sinal nao e random guess):
         - over/under: >= 58%
         - 1X2: >= 42% (favorito emerge)
         - btts: >= 58%
      2. Edge >= 1.03 (3% EV minimo)
      3. 2+ racionais concretos (nao fallbacks)
      4. Jogo com dados maduros:
         - over/under: minuto >= 30
         - 1X2: minuto >= 50 (favorito ja definido)
      5. Pelo menos UM sinal observacional, nao so numero cru
         (momentum, dominante, defesa a ceder, big chances, chasing,
         derby, severo, vermelho, em grande, a jogar melhor, a atacar)
    """
    if sinal.mercado == "1x2":
        if sinal.prob_modelo < 0.42:
            return False
    elif sinal.mercado == "btts":
        if sinal.prob_modelo < 0.58:
            return False
    else:
        if sinal.prob_modelo < 0.58:
            return False

    if sinal.prob_modelo * sinal.odd_valor < 1.03:
        return False

    racionais_credíveis = []
    for r in sinal.racional:
        rl = r.lower()
        if rl.startswith(("modelo ", "0 ")):
            continue
        racionais_credíveis.append(r)
    if len(racionais_credíveis) < 2:
        return False

    minuto_minimo = 50 if sinal.mercado == "1x2" else 30
    if estado.minuto < minuto_minimo:
        return False

    palavras_tom = ("momentum", "dominante", "em grande", "a jogar melhor",
                    "a vencer", "defesa a ceder", "big chances", "chasing",
                    "pressiona", "derby", "severo", "vermelho", "a atacar",
                    "jogo decidido", "equipas equilibradas", "jogo tranca",
                    "em grande (", "gr ", "fecha", "arbitro",
                    "unlucky", "regressao", "processo (xg)",
                    "title race", "6-pointer", "permanencia", "sem stakes",
                    "luta por europa")
    tem_tom = any(any(p in r.lower() for p in palavras_tom) for r in sinal.racional)
    if not tem_tom:
        return False

    return True


def pontuar_sinal(sinal: Sinal, estado: "EstadoLive | None" = None) -> float:
    """Score para ranking: edge x qualidade do racional x forca x contexto.

    Contexto de mercado (Caan Berry): sinais em ligas pouco monitoradas,
    mercados nicho em Big-5, ou contra equipas mediaticas sobrem a
    pontuacao sem distorcer a prob_modelo subjacente.
    """
    edge = sinal.prob_modelo * sinal.odd_valor
    qualidade = max(1, len(sinal.racional))
    fator = _fator_mercado(sinal, estado) if estado is not None else 1.0
    return edge * (qualidade ** 0.5) * sinal.forca_num * fator


def gerar_sinais(estado: "EstadoLive", prev: "PrevisaoLive",
                 prob_minima: float = 0.55) -> list[Sinal]:
    """Devolve sinais LIVE com reasoning rico.

    Cada mercado tem o seu minuto minimo (MINUTO_MIN_POR_MERCADO).
    Alem disso, cada sinal e marcado com forca_num:
      3 = "apostava o meu proprio dinheiro" (alta conviccao)
      2 = "worth watching" (media conviccao)
      1 = "nota" (pouca conviccao, so terminal)
    Scanner usa este criterio para decidir se envia para Telegram.
    """
    ctx = _construir_contexto(estado, prev)
    out: list[Sinal] = []

    # Cada mercado so e analisado se o jogo esta maduro o suficiente
    if not _estado_demasiado_cedo_para_sinal(estado, "1x2"):
        _sinal_1x2(estado, prev, ctx, out, prob_minima)
    if not _estado_demasiado_cedo_para_sinal(estado, "golos"):
        _sinal_golos(estado, prev, ctx, out, prob_minima)
    if not _estado_demasiado_cedo_para_sinal(estado, "btts"):
        _sinal_btts(estado, prev, ctx, out, prob_minima)
    if not _estado_demasiado_cedo_para_sinal(estado, "cantos"):
        _sinal_cantos(estado, prev, ctx, out, prob_minima)
    if not _estado_demasiado_cedo_para_sinal(estado, "cartoes"):
        _sinal_cartoes(estado, prev, ctx, out, prob_minima)
    if not _estado_demasiado_cedo_para_sinal(estado, "remates"):
        _sinal_remates(estado, prev, ctx, out, prob_minima)
    # Regressao xG: Rob Pizzola / Caan Berry. Processo (xG) > conversao (golos).
    if estado.minuto >= MINUTO_MIN_REGRESSAO:
        _sinal_regressao_xg(estado, prev, ctx, out, prob_minima)

    # Agrupar por (mercado, lado) e manter a linha MAIS ALTA (over) ou mais
    # BAIXA (under) com prob >= prob_minima. As casas abrem as linhas mais
    # desafiadoras cedo no jogo — emitir Over 2.5 cartoes quando a casa so
    # tem Over 3.5 e inutil. Preferimos a linha mais exigente onde o
    # modelo ainda e confiante.
    melhores: dict[tuple, Sinal] = {}
    for s in out:
        key = (s.mercado, s.lado)
        linha_s = s.linha if s.linha is not None else 0.0
        existente = melhores.get(key)
        if existente is None:
            melhores[key] = s
            continue
        linha_e = existente.linha if existente.linha is not None else 0.0
        # Para OVER: preferir linha ALTA (mais exigente, odd mais atractiva)
        if s.lado in ("over", "sim"):
            if linha_s > linha_e:
                melhores[key] = s
        # Para UNDER: preferir linha BAIXA (mais exigente, odd mais atractiva)
        else:  # under, nao
            if linha_s < linha_e:
                melhores[key] = s
    # 1X2 tem threshold proprio (PROB_MIN_1X2=0.35), outros usam prob_minima
    return [s for s in melhores.values()
            if s.prob_modelo >= (PROB_MIN_1X2 if s.mercado == "1x2" else prob_minima)]


# ──────────────────── Contexto ricamente observado ────────────────────

def _construir_contexto(estado, prev) -> dict:
    """Extrai observacoes qualitativas do estado para enriquecer rationales.

    Devolve dict com strings prontas a aparecer no Telegram.
    """
    ctx: dict = {}

    # Momentum
    if estado.momentum_actual == "casa":
        ctx["momentum"] = f"{estado.home} com momentum (ultimos 10min)"
    elif estado.momentum_actual == "fora":
        ctx["momentum"] = f"{estado.away} com momentum (ultimos 10min)"

    # Defesa fraca? (<60% tackles won e >10 clearances = pressao)
    def _defesa_fraca(tackles_pct, clearances) -> bool:
        return tackles_pct is not None and tackles_pct < 0.55 and (clearances or 0) >= 10

    if _defesa_fraca(estado.tackles_won_pct_casa, estado.clearances_casa):
        ctx["defesa_fraca_casa"] = f"{estado.home} defesa a ceder ({estado.tackles_won_pct_casa*100:.0f}% tackles ganhos, {estado.clearances_casa} clearances)"
    if _defesa_fraca(estado.tackles_won_pct_fora, estado.clearances_fora):
        ctx["defesa_fraca_fora"] = f"{estado.away} defesa a ceder ({estado.tackles_won_pct_fora*100:.0f}% tackles ganhos, {estado.clearances_fora} clearances)"

    # Dominio territorial (final third entries diferenca grande)
    if (estado.final_third_entries_casa or 0) > (estado.final_third_entries_fora or 0) * 1.4:
        ctx["dominio_territorial"] = f"{estado.home} dominante no final third ({estado.final_third_entries_casa} vs {estado.final_third_entries_fora})"
    elif (estado.final_third_entries_fora or 0) > (estado.final_third_entries_casa or 0) * 1.4:
        ctx["dominio_territorial"] = f"{estado.away} dominante no final third ({estado.final_third_entries_fora} vs {estado.final_third_entries_casa})"

    # Big chances pendentes (criadas mas nao convertidas)
    bc_total = (estado.big_chances_casa or 0) + (estado.big_chances_fora or 0)
    golos_tot = estado.marcador_casa + estado.marcador_fora
    pendentes = bc_total - golos_tot
    if pendentes >= 2:
        ctx["big_chances_pendentes"] = f"{pendentes} big chances criadas sem golo (um vai cair)"

    # Keeper em grande (saves altas)
    if estado.saves_casa is not None and estado.saves_casa >= 5:
        ctx["keeper_casa_grande"] = f"GR {estado.home} com {estado.saves_casa} defesas"
    if estado.saves_fora is not None and estado.saves_fora >= 5:
        ctx["keeper_fora_grande"] = f"GR {estado.away} com {estado.saves_fora} defesas"

    # Score chasing
    diff = abs(estado.marcador_casa - estado.marcador_fora)
    if diff in (1, 2) and prev.minutos_restantes <= 25:
        quem_atras = estado.away if estado.marcador_casa > estado.marcador_fora else estado.home
        ctx["chasing"] = f"{quem_atras} precisa marcar — vai arriscar"

    # Jogo a fechar (empate late ou diferenca grande late)
    if estado.minuto >= 80 and (diff == 0 or diff >= 3):
        if diff == 0:
            ctx["jogo_a_fechar"] = "Empate ao min 80+ — ambas aceitam ponto"
        else:
            ctx["jogo_a_fechar"] = f"Resultado decidido ({estado.marcador_casa}-{estado.marcador_fora}), intensidade baixa"

    # Posse dominante
    if estado.possession_casa is not None:
        if estado.possession_casa >= 0.62:
            ctx["posse_dominante"] = f"{estado.home} com {estado.possession_casa*100:.0f}% posse"
        elif estado.possession_casa <= 0.38:
            ctx["posse_dominante"] = f"{estado.away} com {(1-estado.possession_casa)*100:.0f}% posse"

    # Arbitro severo
    try:
        from apostas.modelos.referencias import fator_arbitro, rate_arbitro, eh_derby
        arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
        if arb_sev > 1.1:
            ctx["arbitro_severo"] = f"Arbitro {estado.arbitro} ({rate_arbitro(estado.arbitro):.1f} cards/jogo)"
        if eh_derby(estado.home, estado.away):
            ctx["derby"] = "⚡DERBY — tensao extra"
    except Exception:  # noqa: BLE001
        pass

    # Faltas elevadas
    if estado.fouls_casa is not None and estado.minuto > 15:
        fpm = (estado.fouls_casa + estado.fouls_fora) / estado.minuto
        if fpm > 0.5:
            ctx["faltas_altas"] = f"{fpm*90:.0f} faltas/90min (jogo porreiro)"

    # Cartao vermelho
    reds = estado.red_cards_casa + estado.red_cards_fora
    if reds > 0:
        ctx["vermelho"] = f"{reds} vermelho(s) mostrado(s)"

    # ─── Formacao + ratings dos jogadores em campo ────────────────────
    if estado.rating_medio_casa is not None and estado.rating_medio_fora is not None:
        diff = estado.rating_medio_casa - estado.rating_medio_fora
        if abs(diff) >= 0.3:
            melhor = estado.home if diff > 0 else estado.away
            r_melhor = max(estado.rating_medio_casa, estado.rating_medio_fora)
            r_pior = min(estado.rating_medio_casa, estado.rating_medio_fora)
            ctx["rating_equipa"] = f"{melhor} a jogar melhor ({r_melhor:.1f} vs {r_pior:.1f} rating medio)"

    # Jogador chave em grande forma
    if estado.rating_top_casa is not None and estado.rating_top_casa >= 7.8:
        ctx["jogador_chave_casa"] = f"{estado.jogador_chave_casa} em grande ({estado.rating_top_casa:.1f})"
    if estado.rating_top_fora is not None and estado.rating_top_fora >= 7.8:
        ctx["jogador_chave_fora"] = f"{estado.jogador_chave_fora} em grande ({estado.rating_top_fora:.1f})"

    # GR em grande rating
    if estado.rating_gr_casa is not None and estado.rating_gr_casa >= 7.5:
        ctx["gr_casa_rating"] = f"GR {estado.home} em grande ({estado.rating_gr_casa:.1f})"
    if estado.rating_gr_fora is not None and estado.rating_gr_fora >= 7.5:
        ctx["gr_fora_rating"] = f"GR {estado.away} em grande ({estado.rating_gr_fora:.1f})"

    # Formacao: 5-4-1/5-3-2 defensivo vs 4-3-3/3-4-3 ofensivo
    def _tipo_formacao(f: str | None) -> str | None:
        if not f:
            return None
        if f.startswith("5-") or f == "5-3-2" or f == "5-4-1":
            return "defensiva"
        if f.startswith("3-") or f in ("4-3-3", "3-4-3"):
            return "ofensiva"
        return "equilibrada"
    tipo_c = _tipo_formacao(estado.formacao_casa)
    tipo_f = _tipo_formacao(estado.formacao_fora)
    if tipo_c and tipo_f and tipo_c != tipo_f:
        ctx["formacoes"] = f"{estado.home} {estado.formacao_casa} ({tipo_c}) vs {estado.away} {estado.formacao_fora} ({tipo_f})"

    # Importancia do jogo (classificacao da liga, cache 12h)
    try:
        imp, imp_desc = importancia_do_jogo(estado)
        if imp_desc:
            ctx["importancia_jogo"] = imp_desc
    except Exception:  # noqa: BLE001
        pass

    return ctx


# ─────────────── 1X2 (resultado final) ───────────────

# 1X2 raramente passa de 50% (ao contrario de over/under), por isso
# tem thresholds proprios mais baixos.
PROB_MIN_1X2 = 0.35


def _sinal_1x2(estado, prev, ctx, out, prob_min) -> None:
    """Prediz quem vence o jogo (resultado final 1/X/2)."""
    diff = abs(estado.marcador_casa - estado.marcador_fora)
    if diff >= 3 and estado.minuto >= 85:
        return
    if estado.minuto >= 88:
        return

    # ZONA DE INCERTEZA: se o momentum contradiz o lider do marcador,
    # NAO prever o lider (o jogo pode virar). Analista no estadio ve
    # claramente que a equipa a atacar pode empatar/virar.
    if diff == 1:  # 1 golo de diferenca
        casa_lider = estado.marcador_casa > estado.marcador_fora
        mom_contra = (casa_lider and estado.momentum_actual == "fora") or \
                     (not casa_lider and estado.momentum_actual == "casa")
        if mom_contra and estado.minuto >= 50:
            # Jogo turbulento com virada possivel — nao recomendamos lider
            return

    threshold = min(prob_min, PROB_MIN_1X2)

    # Aplicar fade favorito pos-intervalo (Caan Berry hotspot 2)
    p_c, p_x, p_f, racional_fade = _ajuste_fade_favorito(estado, prev)

    opcoes = [
        ("casa", estado.home, p_c),
        ("empate", "Empate", p_x),
        ("fora", estado.away, p_f),
    ]
    opcoes.sort(key=lambda x: -x[2])

    # Emite o favorito + possivel segundo com odd atractiva (ex: empate como
    # value quando equipas estao equilibradas)
    emitidos = 0
    for lado, nome, prob in opcoes:
        if prob < threshold:
            break
        if emitidos >= 2:
            break
        # So emite o segundo se estiver proximo do favorito (dentro de 10pp)
        if emitidos > 0 and prob < opcoes[0][2] - 0.10:
            break
        _emitir_sinal_1x2(estado, prev, ctx, out, lado, prob, racional_fade)
        emitidos += 1


def _emitir_sinal_1x2(estado, prev, ctx, out, lado, prob, racional_fade=None) -> None:

    racional = []
    titulo = (f"{estado.home} vence" if lado == "casa"
              else f"{estado.away} vence" if lado == "fora"
              else "Empate")

    # Fade sentimental (Caan Berry): aplica so a quem esta a ganhar e o underdog
    diff = estado.marcador_casa - estado.marcador_fora
    if racional_fade:
        underdog_ganha_casa = (lado == "casa" and diff > 0)
        underdog_ganha_fora = (lado == "fora" and diff < 0)
        if underdog_ganha_casa or underdog_ganha_fora:
            racional.append(racional_fade)

    # Racional baseado no marcador actual
    if lado == "casa" and diff > 0:
        racional.append(f"{estado.home} a vencer {estado.marcador_casa}-{estado.marcador_fora}")
    elif lado == "fora" and diff < 0:
        racional.append(f"{estado.away} a vencer {estado.marcador_fora}-{estado.marcador_casa}")
    elif lado == "empate" and diff == 0:
        racional.append(f"{estado.marcador_casa}-{estado.marcador_fora} ao min {estado.minuto}")
    elif lado in ("casa", "fora") and diff == 0:
        # Tied game, signal says someone will win from here
        racional.append(f"{estado.home if lado == 'casa' else estado.away} tem de marcar para ganhar ({estado.minuto}')")
    elif lado == "casa" and diff < 0:
        racional.append(f"{estado.home} tem de reverter {estado.marcador_casa}-{estado.marcador_fora}")
    elif lado == "fora" and diff > 0:
        racional.append(f"{estado.away} tem de reverter ({estado.marcador_casa}-{estado.marcador_fora})")

    # Rating medio da equipa
    if "rating_equipa" in ctx and lado != "empate":
        if ((lado == "casa" and estado.rating_medio_casa and estado.rating_medio_fora
             and estado.rating_medio_casa > estado.rating_medio_fora)
            or (lado == "fora" and estado.rating_medio_casa and estado.rating_medio_fora
                and estado.rating_medio_fora > estado.rating_medio_casa)):
            racional.append(ctx["rating_equipa"])

    # Jogador chave
    if lado == "casa" and "jogador_chave_casa" in ctx:
        racional.append(ctx["jogador_chave_casa"])
    if lado == "fora" and "jogador_chave_fora" in ctx:
        racional.append(ctx["jogador_chave_fora"])

    # Momentum
    if "momentum" in ctx:
        if (lado == "casa" and estado.momentum_actual == "casa") or \
           (lado == "fora" and estado.momentum_actual == "fora"):
            racional.append(ctx["momentum"])

    # Formacoes
    if "formacoes" in ctx and lado != "empate":
        racional.append(ctx["formacoes"])

    # Defesa fraca do adversario
    if lado == "casa" and "defesa_fraca_fora" in ctx:
        racional.append(ctx["defesa_fraca_fora"])
    if lado == "fora" and "defesa_fraca_casa" in ctx:
        racional.append(ctx["defesa_fraca_casa"])

    # Jogo a fechar favorece empate
    if lado == "empate" and "jogo_a_fechar" in ctx and estado.marcador_casa == estado.marcador_fora:
        racional.append(ctx["jogo_a_fechar"])

    # Contexto para empate: equilibrio
    if lado == "empate" and estado.marcador_casa == estado.marcador_fora:
        if estado.xg_recent_casa is not None and estado.xg_recent_fora is not None:
            xg_rec = estado.xg_recent_casa + estado.xg_recent_fora
            if xg_rec < 0.2 and estado.minuto >= 70:
                racional.append(f"xG ultimos 15min apenas {xg_rec:.1f} — jogo tranca")
        if (estado.rating_medio_casa is not None and estado.rating_medio_fora is not None
                and abs(estado.rating_medio_casa - estado.rating_medio_fora) < 0.3):
            racional.append(f"equipas equilibradas ({estado.rating_medio_casa:.1f} vs {estado.rating_medio_fora:.1f})")
        if estado.minuto >= 75 and prev.minutos_restantes <= 15:
            racional.append(f"{prev.minutos_restantes}min restam — equipas aceitam ponto")

    if not racional:
        racional.append(f"modelo {prob*100:.0f}% para resultado final")
    # Filtro contextual late game
    valido, _razao = _contexto_late_valido(estado, prev, "1x2", lado)
    if not valido:
        return
    # 1X2 passa por _add_1x2 que tem threshold proprio
    odd_be, odd_val = _odds_minimas(prob)
    if odd_val >= 1.15 and prob <= 0.92:
        forca = ("forte" if prob >= 0.60
                 else "normal" if prob >= 0.45
                 else "fraco")
        out.append(Sinal(
            tipo="1x2_live", mercado="1x2", lado=lado, linha=None,
            prob_modelo=prob, odd_minima=odd_be, odd_valor=odd_val,
            racional=racional, forca=forca, titulo=titulo,
        ))


# ─────────────── REGRESSAO xG (Rob Pizzola / Caan Berry) ───────────────
# "A estatistica que apostas quase nunca e a melhor para a projectar."
# Golos marcados = conversao (ruidosa). xG = processo (estavel).
# Equipa com xG 2.0 e 0 golos vai marcar com mais probabilidade que
# equipa com xG 0.3 e 1 golo — o processo diz golo iminente.

GAP_XG_MIN = 1.2           # xG - golos >= 1.2 para considerar unlucky
MINUTO_MIN_REGRESSAO = 55  # so no 2o tempo quando o xG ja e credivel


def _sinal_regressao_xg(estado, prev, ctx, out, prob_min) -> None:
    """Equipa com xG >> golos tem regressao positiva iminente.

    Em vez de "quantos golos marcou" (conversao ruidosa), olhamos para
    "quantas ocasioes de qualidade criou" (volume/processo). Se o gap
    e grande e ha minutos restantes credıveis, apostamos no proximo
    golo dessa equipa com boost sobre lam_rest base.
    """
    if estado.xg_casa is None or estado.xg_fora is None:
        return
    if prev.minutos_restantes < 10:
        return

    gap_casa = estado.xg_casa - estado.marcador_casa
    gap_fora = estado.xg_fora - estado.marcador_fora

    from math import exp

    for lado, nome, gap, xg, golos, lam_rest in (
        ("casa", estado.home, gap_casa, estado.xg_casa,
         estado.marcador_casa, prev.lam_golos_casa_rest),
        ("fora", estado.away, gap_fora, estado.xg_fora,
         estado.marcador_fora, prev.lam_golos_fora_rest),
    ):
        if gap < GAP_XG_MIN:
            continue
        if golos >= 3:
            continue  # ja marcou muito, nao e caso de "unlucky"

        # Boost: lam_rest base ja incorpora xG rate, mas aqui amplificamos
        # pelo gap acumulado nao-convertido (sinal de volume de ocasioes)
        boost = 1.0 + min(0.30, 0.15 + (gap - GAP_XG_MIN) * 0.12)
        lam_boosted = lam_rest * boost
        p_marca = 1.0 - exp(-lam_boosted)
        if p_marca < prob_min:
            continue

        racional = [
            f"{nome} xG {xg:.1f} vs {golos} golo(s) — gap {gap:+.1f} (unlucky)",
            "Processo (xG) diz: proximo golo e deles",
        ]
        bc_lado = (estado.big_chances_casa if lado == "casa"
                   else estado.big_chances_fora)
        if bc_lado and bc_lado >= 2:
            racional.append(f"{bc_lado} big chances criadas")
        if estado.momentum_actual == lado and "momentum" in ctx:
            racional.append(ctx["momentum"])
        if lado == "casa" and "defesa_fraca_fora" in ctx:
            racional.append(ctx["defesa_fraca_fora"])
        if lado == "fora" and "defesa_fraca_casa" in ctx:
            racional.append(ctx["defesa_fraca_casa"])
        if lado == "casa" and "jogador_chave_casa" in ctx:
            racional.append(ctx["jogador_chave_casa"])
        if lado == "fora" and "jogador_chave_fora" in ctx:
            racional.append(ctx["jogador_chave_fora"])

        titulo = f"{nome} marca (regressao xG)"
        _add(out, "regressao_xg", "golo_equipa", f"marca_{lado}",
             None, p_marca, racional, titulo, estado=estado, prev=prev)


# ─────────────── GOLOS ───────────────

def _sinal_golos(estado, prev, ctx, out, prob_min) -> None:
    """Itera as linhas standard do mercado e emite sinais."""
    golos = estado.marcador_casa + estado.marcador_fora

    for linha in LINHAS_MERCADO["golos"]:
        # Se jogo ja tem mais golos que a linha, mercado esta fechado
        if golos > linha:
            continue
        p_over = prev.p_over_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} golos"
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            if "big_chances_pendentes" in ctx:
                racional.append(ctx["big_chances_pendentes"])
            if "defesa_fraca_casa" in ctx:
                racional.append(ctx["defesa_fraca_casa"])
            if "defesa_fraca_fora" in ctx:
                racional.append(ctx["defesa_fraca_fora"])
            if "dominio_territorial" in ctx:
                racional.append(ctx["dominio_territorial"])
            if "chasing" in ctx:
                racional.append(ctx["chasing"])
            if estado.xg_casa is not None:
                xg_tot = estado.xg_casa + estado.xg_fora
                if xg_tot > golos + 0.3:
                    racional.append(f"xG total {xg_tot:.1f} > {golos} golos actuais")
            if not racional:
                racional.append(f"{prev.minutos_restantes}min restam, modelo espera +{int(linha - golos + 0.5)}")
            _add(out, "over_golos_live", "golos", "over", linha, p_over, racional, titulo, estado=estado, prev=prev)

        if p_under >= prob_min:
            racional = []
            titulo = f"Under {linha} golos"
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            if "keeper_casa_grande" in ctx:
                racional.append(ctx["keeper_casa_grande"])
            if "keeper_fora_grande" in ctx:
                racional.append(ctx["keeper_fora_grande"])
            if estado.xg_recent_casa is not None:
                xg_rec = estado.xg_recent_casa + estado.xg_recent_fora
                if xg_rec < 0.2 and estado.minuto >= 60:
                    racional.append(f"xG ultimos 15min apenas {xg_rec:.1f}")
            if not racional:
                racional.append(f"{golos} golos ao min {estado.minuto}, pouca intensidade")
            _add(out, "under_golos_live", "golos", "under", linha, p_under, racional, titulo, estado=estado, prev=prev)


# ─────────────── BTTS ───────────────

def _sinal_btts(estado, prev, ctx, out, prob_min) -> None:
    if estado.marcador_casa > 0 and estado.marcador_fora > 0:
        return
    p_sim = prev.p_btts_final
    p_nao = 1 - p_sim

    if p_sim >= prob_min:
        racional = []
        titulo = "BTTS SIM"
        quem_falta = estado.home if estado.marcador_casa == 0 else estado.away
        racional.append(f"{quem_falta} precisa marcar")
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem >= 0.6:
                racional.append(f"xG {xg_sem:.1f} do lado sem golo — chances feitas")
        if "defesa_fraca_casa" in ctx and estado.marcador_fora == 0:
            racional.append(ctx["defesa_fraca_casa"])
        if "defesa_fraca_fora" in ctx and estado.marcador_casa == 0:
            racional.append(ctx["defesa_fraca_fora"])
        if "chasing" in ctx:
            racional.append(ctx["chasing"])
        _add(out, "btts_sim", "btts", "sim", None, p_sim, racional, titulo, estado=estado, prev=prev)

    if p_nao >= prob_min and estado.minuto >= 65:
        racional = []
        titulo = "BTTS NAO"
        if estado.xg_casa is not None:
            xg_sem = estado.xg_casa if estado.marcador_casa == 0 else estado.xg_fora
            if xg_sem < 0.4:
                racional.append(f"xG {xg_sem:.1f} do lado sem golo — nao criam")
            else:
                return
        if "keeper_casa_grande" in ctx and estado.marcador_fora == 0:
            racional.append(ctx["keeper_casa_grande"])
        if "keeper_fora_grande" in ctx and estado.marcador_casa == 0:
            racional.append(ctx["keeper_fora_grande"])
        if "jogo_a_fechar" in ctx:
            racional.append(ctx["jogo_a_fechar"])
        _add(out, "btts_nao", "btts", "nao", None, p_nao, racional, titulo, estado=estado, prev=prev)


# ─────────────── CANTOS ───────────────

def _sinal_cantos(estado, prev, ctx, out, prob_min) -> None:
    cantos = (estado.corners_casa or 0) + (estado.corners_fora or 0)
    proj_c_c = prev.projeccao_cantos_casa
    proj_c_f = prev.projeccao_cantos_fora
    proj_c_total = proj_c_c + proj_c_f

    for linha in LINHAS_MERCADO["cantos"]:
        if cantos > linha:
            continue
        p_over = prev.p_over_cantos_total(linha)
        p_under = 1 - p_over
        rate = cantos / max(estado.minuto, 1)

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} cantos"
            racional.append(
                f"Projeccao: {estado.home} {proj_c_c:.0f}, "
                f"{estado.away} {proj_c_f:.0f} = {proj_c_total:.0f} total"
            )
            if rate > 0.11:
                racional.append(f"rate {rate*90:.1f} cantos/90min (acima media)")
            if "dominio_territorial" in ctx:
                racional.append(ctx["dominio_territorial"])
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            blocked = (estado.shots_blocked_casa or 0) + (estado.shots_blocked_fora or 0)
            if blocked >= 3:
                racional.append(f"{blocked} remates bloqueados — cantos a caminho")
            if (estado.crosses_total_casa or 0) + (estado.crosses_total_fora or 0) > 15:
                racional.append("Jogo pelos cruzamentos — cantos consequencia")
            _add(out, "cantos_over", "cantos", "over", linha, p_over, racional, titulo, estado=estado, prev=prev)

        if p_under >= prob_min:
            racional = []
            titulo = f"Under {linha} cantos"
            if rate < 0.08:
                racional.append(f"rate {rate*90:.1f} cantos/90min (abaixo media)")
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            diff = abs(estado.marcador_casa - estado.marcador_fora)
            if diff >= 2 and estado.minuto >= 75 and not racional:
                racional.append(f"Jogo decidido ({estado.marcador_casa}-{estado.marcador_fora}) ao min {estado.minuto}")
            if not racional:
                racional.append(f"{cantos} cantos ao min {estado.minuto}, pouca cadencia")
            _add(out, "cantos_under", "cantos", "under", linha, p_under, racional, titulo, estado=estado, prev=prev)


# ─────────────── CARTOES ───────────────

def _sinal_cartoes(estado, prev, ctx, out, prob_min) -> None:
    amarelos = estado.yellow_cards_casa + estado.yellow_cards_fora
    for linha in LINHAS_MERCADO["cartoes"]:
        if amarelos > linha:
            continue
        p_over = prev.p_over_cartoes_total(linha)
        p_under = 1 - p_over

        if p_over >= prob_min:
            racional = []
            titulo = f"Over {linha} cartoes amarelos"
            if "arbitro_severo" in ctx:
                racional.append(ctx["arbitro_severo"])
            if "derby" in ctx:
                racional.append(ctx["derby"])
            if "vermelho" in ctx:
                racional.append(ctx["vermelho"] + " — jogo mais tenso")
            if "faltas_altas" in ctx:
                racional.append(ctx["faltas_altas"])
            if estado.ground_duels_won_pct_casa is not None:
                dd = abs(estado.ground_duels_won_pct_casa - 0.5)
                if dd > 0.07:
                    perdedor = estado.home if estado.ground_duels_won_pct_casa < 0.5 else estado.away
                    racional.append(f"{perdedor} a perder duelos — faltas a vir")
            if not racional:
                rate = amarelos / max(estado.minuto, 1)
                racional.append(f"{amarelos} amarelos ao min {estado.minuto} ({rate*90:.1f}/90min)")
            _add(out, "cards_over", "cartoes", "over", linha, p_over, racional, titulo, estado=estado, prev=prev)

        # UNDER cartoes: so late no jogo e sem contexto de agressividade
        try:
            from apostas.modelos.referencias import fator_arbitro, eh_derby
            arb_sev = fator_arbitro(estado.arbitro) if estado.arbitro else 1.0
            is_derby = eh_derby(estado.home, estado.away)
        except Exception:  # noqa: BLE001
            arb_sev = 1.0
            is_derby = False
        if (p_under >= prob_min and prev.minutos_restantes <= 25
                and arb_sev < 1.05 and not is_derby):
            racional = []
            titulo = f"Under {linha} cartoes amarelos"
            if "jogo_a_fechar" in ctx:
                racional.append(ctx["jogo_a_fechar"])
            if estado.arbitro:
                racional.append(f"arbitro {estado.arbitro} brando")
            if not racional:
                racional.append(f"so {amarelos} amarelos ao min {estado.minuto}")
            _add(out, "cards_under", "cartoes", "under", linha, p_under, racional, titulo, estado=estado, prev=prev)


# ─────────────── REMATES ───────────────

def _sinal_remates(estado, prev, ctx, out, prob_min) -> None:
    shots = (estado.shots_total_casa or 0) + (estado.shots_total_fora or 0)
    sot = (estado.shots_on_target_casa or 0) + (estado.shots_on_target_fora or 0)
    rate_shots = shots / max(estado.minuto, 1)
    rate_sot = sot / max(estado.minuto, 1)

    proj_shots_c = prev.projeccao_shots_casa
    proj_shots_f = prev.projeccao_shots_fora
    proj_shots_total = proj_shots_c + proj_shots_f

    for linha in LINHAS_MERCADO["remates"]:
        if shots > linha:
            continue
        p = prev.p_over_remates_total(linha)
        if p >= prob_min:
            if proj_shots_total < linha * 0.75:
                continue
            racional = []
            titulo = f"Over {linha} remates"
            racional.append(
                f"Projeccao: {estado.home} {proj_shots_c:.0f}, "
                f"{estado.away} {proj_shots_f:.0f} = {proj_shots_total:.0f} total"
            )
            if rate_shots > 0.27:
                racional.append(f"rate {rate_shots*90:.1f} remates/90min")
            if "momentum" in ctx:
                racional.append(ctx["momentum"])
            if "big_chances_pendentes" in ctx:
                racional.append(ctx["big_chances_pendentes"])
            _add(out, "shots_over", "remates", "over", linha, p, racional, titulo, estado=estado, prev=prev)

    # Projeccao por equipa — mostramos sempre no racional para user
    # comparar com linhas do mercado (ex: casa oferece Over 11.5 que
    # modelo projecta 10, nao e aposta; se projeccao e 12-13, e aposta)
    proj_sot_c = prev.projeccao_sot_casa
    proj_sot_f = prev.projeccao_sot_fora
    proj_sot_total = proj_sot_c + proj_sot_f

    for linha in LINHAS_MERCADO["remates_a_baliza"]:
        if sot > linha:
            continue
        p = prev.p_over_remates_a_baliza_total(linha)
        if p >= prob_min:
            # Consistencia: projeccao do modelo tem de aproximar a linha
            if proj_sot_total < linha * 0.75:
                continue
            racional = []
            titulo = f"Over {linha} remates ao alvo"
            # Sempre: projeccao por equipa (dá contexto para comparar com casa)
            racional.append(
                f"Projeccao: {estado.home} {proj_sot_c:.0f}, "
                f"{estado.away} {proj_sot_f:.0f} = {proj_sot_total:.0f} total"
            )
            if estado.big_chances_casa is not None:
                bc = (estado.big_chances_casa or 0) + (estado.big_chances_fora or 0)
                if bc >= 3:
                    racional.append(f"{bc} big chances criadas")
            if "keeper_casa_grande" in ctx:
                racional.append(ctx["keeper_casa_grande"])
            if "keeper_fora_grande" in ctx:
                racional.append(ctx["keeper_fora_grande"])
            if rate_sot * 90 >= 7:
                racional.append(f"{sot} SoT ao min {estado.minuto} ({rate_sot*90:.1f}/90min)")
            _add(out, "sot_over", "remates_a_baliza", "over", linha, p, racional, titulo, estado=estado, prev=prev)
