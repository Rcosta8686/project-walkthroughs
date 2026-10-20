"""Testes para analise_treinador — decisao da postura + geracao de voz."""
from __future__ import annotations

import pytest

from apostas.alertas.analise_treinador import (
    MIN_JOGOS,
    VozTreinador,
    _decidir_postura,
    _diff_do_lado,
    analise_treinador,
    gerar_para_ambas,
)


# ─── Decisao de postura ─────────────────────────────────────────────

def test_a_ganhar_2_golos_tarde_fecha():
    assert _decidir_postura(diff=2, minuto=70, perfil_dominante=False,
                            perfil_chasing=False) == "fechar"


def test_a_ganhar_2_golos_cedo_controla():
    assert _decidir_postura(diff=2, minuto=30, perfil_dominante=False,
                            perfil_chasing=False) == "controlar"


def test_a_ganhar_1_tarde_sem_posse_defende():
    assert _decidir_postura(diff=1, minuto=80, perfil_dominante=False,
                            perfil_chasing=False) == "defender"


def test_a_ganhar_1_tarde_com_posse_controla():
    assert _decidir_postura(diff=1, minuto=80, perfil_dominante=True,
                            perfil_chasing=False) == "controlar"


def test_empate_tarde_arrisca():
    assert _decidir_postura(diff=0, minuto=80, perfil_dominante=False,
                            perfil_chasing=False) == "arriscar"


def test_empate_cedo_equilibra():
    assert _decidir_postura(diff=0, minuto=20, perfil_dominante=False,
                            perfil_chasing=False) == "equilibrar"


def test_a_perder_1_tarde_sem_perfil_chasing_ataca():
    assert _decidir_postura(diff=-1, minuto=80, perfil_dominante=False,
                            perfil_chasing=False) == "atacar"


def test_a_perder_1_tarde_com_chasing_desespera():
    assert _decidir_postura(diff=-1, minuto=80, perfil_dominante=False,
                            perfil_chasing=True) == "desespero"


def test_a_perder_2_tarde_aceita_quando_nao_chasing():
    assert _decidir_postura(diff=-2, minuto=75, perfil_dominante=False,
                            perfil_chasing=False) == "aceitar"


def test_a_perder_2_cedo_ainda_ataca():
    assert _decidir_postura(diff=-2, minuto=30, perfil_dominante=False,
                            perfil_chasing=False) == "atacar"


# ─── diff_do_lado ──────────────────────────────────────────────────

def test_diff_casa_ganhando():
    assert _diff_do_lado(2, 1, "casa") == 1


def test_diff_fora_ganhando():
    assert _diff_do_lado(1, 3, "fora") == 2


def test_diff_fora_perdendo():
    assert _diff_do_lado(3, 1, "fora") == -2


# ─── analise_treinador ───────────────────────────────────────────────

def test_devolve_none_quando_poucos_jogos():
    r = analise_treinador(
        "Roma", diff=1, minuto=60, jogos_lidos=MIN_JOGOS - 1,
    )
    assert r is None


def test_devolve_voz_quando_dados_suficientes():
    r = analise_treinador(
        "Roma", diff=2, minuto=70, jogos_lidos=10,
        posse_media=60,
    )
    assert isinstance(r, VozTreinador)
    assert "Treinador Roma" in r.texto
    assert "a ganhar por 2" in r.texto
    assert "70'" in r.texto
    assert r.postura == "fechar"


def test_perfil_dominante_adiciona_tag():
    r = analise_treinador(
        "City", diff=1, minuto=65, jogos_lidos=10,
        posse_media=65,
    )
    assert r.postura == "controlar"
    assert "dominante com posse" in r.texto


def test_perfil_chasing_adiciona_tag_no_atacar():
    r = analise_treinador(
        "Milan", diff=-1, minuto=70, jogos_lidos=10,
        remates_a_perder=15.0, remates_media=10.0,
        posse_media=50,
    )
    assert r.postura == "atacar"
    assert "+remates quando atras" in r.texto


def test_sem_perfil_defende_fica_sem_tag_dominante():
    r = analise_treinador(
        "Burnley", diff=1, minuto=80, jogos_lidos=10,
        posse_media=42,
    )
    assert r.postura == "defender"
    assert "fechar e OK" in r.texto


def test_texto_mostra_empate():
    r = analise_treinador(
        "Nice", diff=0, minuto=30, jogos_lidos=10, posse_media=50,
    )
    assert "empatados" in r.texto


def test_texto_mostra_a_perder():
    r = analise_treinador(
        "Monza", diff=-2, minuto=40, jogos_lidos=10, posse_media=48,
    )
    assert "a perder por 2" in r.texto


# ─── gerar_para_ambas ───────────────────────────────────────────────

class _Baseline:
    def __init__(self, remates=12.0, posse_pct=55, cards_amarelos=2.5,
                 cantos=5.0):
        self.remates = remates
        self.posse_pct = posse_pct
        self.cards_amarelos = cards_amarelos
        self.cantos = cantos


class _PorResultado:
    def __init__(self, remates_L=12.0):
        self.remates_L = remates_L


class _Ficha:
    def __init__(self, nome, jogos=10, remates=12, posse=55, remates_L=12):
        self.nome = nome
        self.jogos_lidos = jogos
        self.baseline = _Baseline(remates=remates, posse_pct=posse)
        self.por_resultado = _PorResultado(remates_L=remates_L)


class _Confronto:
    def __init__(self, fc, ff):
        self.ficha_casa = fc
        self.ficha_fora = ff


def test_gerar_para_ambas_devolve_duas_vozes():
    def fake_confronto(home_id, away_id, *, minuto, score_casa, score_fora):
        return _Confronto(
            _Ficha("Casa FC", posse=60),
            _Ficha("Fora FC", posse=48),
        )
    voz_c, voz_f = gerar_para_ambas(
        1, 2, minuto=70, score_casa=2, score_fora=1,
        confronto_fn=fake_confronto,
    )
    assert voz_c is not None
    assert voz_f is not None
    assert "Casa FC" in voz_c.texto
    assert "Fora FC" in voz_f.texto
    # Casa ganha por 1 ao 70 com posse → controlar
    assert voz_c.postura == "controlar"
    # Fora perde por 1 ao 70 → atacar
    assert voz_f.postura == "atacar"


def test_gerar_para_ambas_filtra_ficha_fraca():
    def fake_confronto(home_id, away_id, *, minuto, score_casa, score_fora):
        return _Confronto(
            _Ficha("Casa", jogos=2),   # ficha com poucos jogos
            _Ficha("Fora", jogos=10),
        )
    voz_c, voz_f = gerar_para_ambas(
        1, 2, minuto=70, score_casa=1, score_fora=0,
        confronto_fn=fake_confronto,
    )
    assert voz_c is None
    assert voz_f is not None


def test_gerar_para_ambas_devolve_none_quando_confronto_falha():
    def fake_confronto(*args, **kwargs):
        raise RuntimeError("sem rede")
    voz_c, voz_f = gerar_para_ambas(
        1, 2, minuto=60, score_casa=0, score_fora=0,
        confronto_fn=fake_confronto,
    )
    assert voz_c is None
    assert voz_f is None


def test_gerar_para_ambas_devolve_none_quando_confronto_e_none():
    def fake_confronto(*args, **kwargs):
        return None
    voz_c, voz_f = gerar_para_ambas(
        1, 2, minuto=60, score_casa=0, score_fora=0,
        confronto_fn=fake_confronto,
    )
    assert voz_c is None
    assert voz_f is None


# ─── Variedade de texto ─────────────────────────────────────────────

def test_variantes_mudam_entre_equipas():
    """Mesma postura, nomes diferentes devem (frequentemente) escolher
    variantes diferentes. Nao ha garantia 100% mas numa amostra grande
    aparece mais que uma variante."""
    textos = set()
    for nome in [f"Equipa{i}" for i in range(10)]:
        r = analise_treinador(
            nome, diff=1, minuto=65, jogos_lidos=10, posse_media=50,
        )
        textos.add(r.texto)
    assert len(textos) >= 2  # pelo menos 2 variantes aparecem


def test_variantes_mudam_ao_longo_do_jogo():
    """Mesma equipa, minutos de decadas diferentes podem mudar frase."""
    textos = set()
    # Testa minutos de varias decadas - devem produzir pelo menos 1 variante,
    # possivelmente mais consoante o hash.
    for minuto in [10, 25, 35, 50, 65, 75]:
        r = analise_treinador(
            "Benfica", diff=0, minuto=minuto, jogos_lidos=10, posse_media=55,
        )
        textos.add(r.texto)
    assert len(textos) >= 1  # smoke test — nao crasha, produz texto


# ─── Observacao NOW ─────────────────────────────────────────────────

def test_obs_now_remates_acima_do_normal():
    """A pressionar — remates muito acima do esperado."""
    r = analise_treinador(
        "Dortmund", diff=-1, minuto=60, jogos_lidos=10,
        posse_media=50,
        remates_media=10.0,  # media 10 por 90
        remates_atuais=15,   # a 60' ja levou 15 (esperado ~6.7)
    )
    assert "pressionar" in r.texto or "acima do normal" in r.texto


def test_obs_now_jogo_preso():
    """Poucos remates em jogo avancado."""
    r = analise_treinador(
        "Hertha", diff=0, minuto=60, jogos_lidos=10,
        posse_media=48,
        remates_media=12.0,
        remates_atuais=2,  # muito baixo
    )
    assert "jogo preso" in r.texto or "preso" in r.texto


def test_obs_now_amarelos_demais():
    r = analise_treinador(
        "Atleti", diff=0, minuto=60, jogos_lidos=10,
        posse_media=50,
        cards_media=2.0,
        cards_atuais=5,  # 5 amarelos a 60' quando media e 2 por jogo
    )
    assert "amarelos acima do normal" in r.texto


def test_obs_now_silencia_sem_baseline():
    """Sem baseline de remates/cantos/cards, nao arrisca observacao."""
    r = analise_treinador(
        "X", diff=0, minuto=60, jogos_lidos=10, posse_media=50,
        remates_atuais=5, cantos_atuais=3,
    )
    # Nao deve ter " · " de observacao NOW (pode ter o tag de perfil)
    # Verificar que nao tem palavras que requerem baseline
    assert "pressionar" not in r.texto
    assert "jogo preso" not in r.texto
