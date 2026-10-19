"""Testes Fase 7 (casa/fora split) + Fase 9 (carga titulares)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from apostas.relatorios.ficha_equipa import (
    SeccaoCasaFora, _gerar_notas,
)


def _bg_dict():
    """Minimum ficha_base dict for _gerar_notas."""
    class _Baseline:
        golos_marcados = 2.0
        remates = 12.0
        cards_amarelos = 2.0
        posse_pct = 50.0
    class _ParteObj:
        diferenca_remates = None
        diferenca_cantos = None
        cards_am_1t = 0.5
        cards_am_2t = 0.7
        fouls_1t = 7.0
        fouls_2t = 7.5
    class _PR:
        n_W = 5; n_D = 2; n_L = 3
        remates_W = 14.0; remates_D = 11.0; remates_L = 10.0
        sot_W = 5.0; sot_D = 4.0; sot_L = 3.5
        virar_jogos_pct = 0.1
    class _Fadiga:
        jogos_ultimos_7d = 1
        jogos_ultimos_14d = 2
        dias_desde_ultimo_jogo = 3
        minutos_top_titulares_7d = None
        minutos_top_titulares_14d = None
    return {
        "baseline": _Baseline(),
        "por_parte": _ParteObj(),
        "por_resultado": _PR(),
        "fadiga": _Fadiga(),
    }


def test_nota_fortaleza_em_casa():
    """Fase 7: 5 casas, 4 vitorias → emite 🏠 Fortaleza."""
    base = _bg_dict()
    base["casa_fora"] = SeccaoCasaFora(
        n_casa=5, n_fora=5,
        golos_marcados_casa=2.5, golos_marcados_fora=1.3,
        golos_sofridos_casa=0.6, golos_sofridos_fora=1.8,
        remates_casa=14.0, remates_fora=10.0,
        cantos_casa=6.5, cantos_fora=4.5,
        cards_am_casa=2.0, cards_am_fora=2.4,
        vitorias_casa=4, empates_casa=1, derrotas_casa=0,
        vitorias_fora=1, empates_fora=1, derrotas_fora=3,
    )
    notas = _gerar_notas(base, [])
    assert any("Muito melhor em casa" in n for n in notas)
    assert any("Fortaleza em casa" in n for n in notas)


def test_nota_fraca_fora():
    base = _bg_dict()
    base["casa_fora"] = SeccaoCasaFora(
        n_casa=5, n_fora=5,
        golos_marcados_casa=2.0, golos_marcados_fora=2.0,
        golos_sofridos_casa=1.0, golos_sofridos_fora=1.0,
        remates_casa=12.0, remates_fora=12.0,
        cantos_casa=5.0, cantos_fora=5.0,
        cards_am_casa=2.0, cards_am_fora=2.0,
        vitorias_casa=3, empates_casa=1, derrotas_casa=1,
        vitorias_fora=1, empates_fora=1, derrotas_fora=3,
    )
    notas = _gerar_notas(base, [])
    assert any("Fraca fora" in n for n in notas)


def test_nota_sem_split_quando_poucos_jogos():
    base = _bg_dict()
    base["casa_fora"] = SeccaoCasaFora(
        n_casa=2, n_fora=2,
        golos_marcados_casa=None, golos_marcados_fora=None,
        golos_sofridos_casa=None, golos_sofridos_fora=None,
        remates_casa=None, remates_fora=None,
        cantos_casa=None, cantos_fora=None,
        cards_am_casa=None, cards_am_fora=None,
        vitorias_casa=2, empates_casa=0, derrotas_casa=0,
        vitorias_fora=1, empates_fora=1, derrotas_fora=0,
    )
    notas = _gerar_notas(base, [])
    # Sem splits suficientes, nao deve emitir nota casa/fora de golos
    assert not any("Muito melhor" in n or "Melhor fora" in n for n in notas)


def test_nota_titulares_desgastados():
    """Fase 9: minutos top5 >=180 em 7d → nota 🥵."""
    base = _bg_dict()
    base["fadiga"].minutos_top_titulares_7d = 240
    base["casa_fora"] = None
    notas = _gerar_notas(base, [])
    assert any("🥵" in n and "240" in n for n in notas)


def test_nota_titulares_frescos_nao_emite():
    base = _bg_dict()
    base["fadiga"].minutos_top_titulares_7d = 90
    base["casa_fora"] = None
    notas = _gerar_notas(base, [])
    assert not any("🥵" in n for n in notas)


# ─── Confronto 🏠/✈ cruzado ────────────────────────────────────────

def test_confronto_padrao_casa_adv_sofre_fora():
    """Fase 7 cruzado: casa marca 2.5 em casa + adv sofre 2.0 fora → 🏠."""
    from apostas.alertas.confronto import _padroes_cruzados

    class _CF:
        pass
    cfc = _CF(); cfc.golos_marcados_casa = 2.5; cfc.golos_sofridos_casa = 0.8
    cfc.golos_marcados_fora = 1.2; cfc.golos_sofridos_fora = 1.8
    cff = _CF(); cff.golos_marcados_casa = 1.5; cff.golos_sofridos_casa = 1.0
    cff.golos_marcados_fora = 1.0; cff.golos_sofridos_fora = 2.0

    class _Ficha:
        pass
    cas = _Ficha(); cas.nome = "Roma"
    cas.por_parte = _CF(); cas.por_parte.diferenca_remates = None
    cas.por_parte.diferenca_cantos = None
    cas.por_resultado = _CF(); cas.por_resultado.virar_jogos_pct = None
    cas.por_resultado.n_L = 0; cas.por_resultado.remates_L = None
    cas.baseline = _CF(); cas.baseline.remates = 12.0
    cas.baseline.cards_amarelos = 2.0; cas.baseline.posse_pct = 55.0
    cas.baseline.golos_marcados = 2.0
    cas.casa_fora = cfc

    fora = _Ficha(); fora.nome = "Milan"
    fora.por_parte = _CF(); fora.por_parte.diferenca_remates = None
    fora.por_parte.diferenca_cantos = None
    fora.por_resultado = _CF(); fora.por_resultado.virar_jogos_pct = None
    fora.por_resultado.n_L = 0; fora.por_resultado.remates_L = None
    fora.baseline = _CF(); fora.baseline.remates = 11.0
    fora.baseline.cards_amarelos = 2.0; fora.baseline.posse_pct = 50.0
    fora.baseline.golos_marcados = 1.3
    fora.casa_fora = cff

    padroes = _padroes_cruzados(cas, fora, None, None, None)
    # Casa marca 2.5 em casa + Milan sofre 2.0 fora → esperado=(2.5+2.0)/2=2.25 vs base 2.0 → >1.20× ? 2.25/2.0=1.125 → nao
    # Vamos fazer o case mais forte: casa 3.0 em casa + adv sofre 2.5 fora
    cfc.golos_marcados_casa = 3.0
    cff.golos_sofridos_fora = 2.5
    padroes = _padroes_cruzados(cas, fora, None, None, None)
    assert any("🏠" in p for p in padroes)


# ─── Cerebro aplica ajustes dos novos padroes ──────────────────────

def test_cerebro_aplica_mult_para_casa_adv_sofre_fora():
    from apostas.alertas import cerebro_live
    cerebro_live.limpar_cache()

    class _Res:
        nome_casa = "A"; nome_fora = "B"
        padroes_cruzados = ["🏠 Em casa + adversario sofre fora"]
        alertas = []

    with patch("apostas.alertas.confronto.analisar_confronto",
               return_value=_Res()):
        aj = cerebro_live.calcular_ajustes(1, 2)
    assert aj.lam_golos_mult == pytest.approx(1.08)
    assert aj.lam_shots_casa_mult == pytest.approx(1.05)


def test_cerebro_aplica_mult_titulares_desgastados():
    from apostas.alertas import cerebro_live
    cerebro_live.limpar_cache()

    class _Res:
        nome_casa = "A"; nome_fora = "B"
        padroes_cruzados = []
        alertas = []
    # Nota: 🥵 vem dos padroes do confronto, nao de alertas de fadiga —
    # vamos testar que se padroes contem 🥵, aplica mult
    r = _Res()
    r.padroes_cruzados = ["🥵 Titulares TOP5 cansados"]
    with patch("apostas.alertas.confronto.analisar_confronto", return_value=r):
        aj = cerebro_live.calcular_ajustes(1, 2)
    assert aj.lam_shots_casa_mult == pytest.approx(0.95)
    assert aj.lam_shots_fora_mult == pytest.approx(0.95)
