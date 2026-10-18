"""Testes para Fase 4 — M17 Dual Signal, M20 Espeleologia, M21 Hipoteses."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from apostas.alertas import dual_sinal
from apostas.alertas.historico_alertas import PerfilAlerta
from apostas.modelos import espeleologia, hipoteses
from apostas.utils import config as config_mod, db as db_mod


# ═══════════════════════ M17 — Dual Signal ═══════════════════════════

@dataclass
class _PerfilFake(PerfilAlerta):
    pass


def _perfil(**kw):
    """Monta um PerfilAlerta com defaults sensiveis."""
    base = dict(
        liga="L", mercado="golos", lado="over",
        n_alertas=50, n_resolvidos=30,
        clv_medio=1.0, pct_clv_positivo=0.55,
        roi=2.0, categoria_forte=False, stake_mult=1.0,
    )
    base.update(kw)
    return PerfilAlerta(**base)


def test_dual_sinal_passa_quando_ev_e_historico_fortes():
    perfis = {("Serie A", "golos", "over"): _perfil(
        liga="Serie A", categoria_forte=True, n_alertas=150,
    )}
    sug = {"liga": "Serie A", "mercado": "golos", "lado": "over",
           "casa": "betano", "ev": 0.08, "prob_modelo": 0.60}
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert res.e_dual
    assert res.passa_edge
    assert res.passa_historico
    assert "edge+histórico" in res.razao


def test_dual_sinal_so_edge_nao_dual():
    perfis = {}  # Sem historico
    sug = {"liga": "L", "mercado": "golos", "lado": "over",
           "casa": "betano", "ev": 0.08, "prob_modelo": 0.60}
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert not res.e_dual
    assert res.passa_edge
    assert not res.passa_historico


def test_dual_sinal_so_historico_nao_dual():
    perfis = {("L", "golos", "over"): _perfil(categoria_forte=True)}
    sug = {"liga": "L", "mercado": "golos", "lado": "over",
           "casa": "betano", "ev": 0.02, "prob_modelo": 0.40}
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert not res.e_dual
    assert not res.passa_edge
    assert res.passa_historico


def test_dual_sinal_para_analista_usa_prob_minima():
    perfis = {("L", "golos", "over"): _perfil(categoria_forte=True)}
    # Analista com prob 0.75 passa edge (>=0.70)
    sug = {"liga": "L", "mercado": "golos", "lado": "over",
           "casa": "ANALISTA", "prob_modelo": 0.75}
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert res.e_dual

    # Prob 0.60 ja nao passa
    sug["prob_modelo"] = 0.60
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert not res.e_dual
    assert not res.passa_edge


def test_dual_sinal_historico_fraco_bloqueia_mesmo_com_volume():
    """n_alertas>=20 mas CLV negativo → nao conta como confirmacao."""
    perfis = {("L", "golos", "over"): _perfil(
        n_alertas=30, categoria_forte=False, stake_mult=1.0, clv_medio=-1.5,
    )}
    sug = {"liga": "L", "mercado": "golos", "lado": "over",
           "casa": "betano", "ev": 0.08, "prob_modelo": 0.6}
    res = dual_sinal.avaliar_dual_sinal(sug, perfis_historicos=perfis)
    assert not res.passa_historico


# ═══════════════════════ M20 — Espeleologia ══════════════════════════

@pytest.fixture
def bd_vazia(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "test.db")
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    yield tmp_path
    db_mod.reset_engine()


def _csv_falhas(tmp_path: Path, linhas: list[dict]) -> Path:
    """Escreve um CSV live minimalista com as linhas dadas."""
    campos = [
        "alert_id", "event_id", "liga", "mercado", "lado", "linha",
        "minuto", "marcador", "prob_modelo", "ev", "resultado",
    ]
    p = tmp_path / "sug.csv"
    with p.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    return p


def test_espeleologia_ingere_apenas_L(bd_vazia):
    csv_path = _csv_falhas(bd_vazia, [
        {"alert_id": 1, "event_id": 100, "liga": "Serie A",
         "mercado": "golos", "lado": "over", "linha": "2.5",
         "minuto": "55", "prob_modelo": "0.58", "ev": "0.08",
         "resultado": "L"},
        {"alert_id": 2, "event_id": 101, "liga": "L",
         "mercado": "golos", "lado": "over", "linha": "2.5",
         "minuto": "70", "prob_modelo": "0.6", "ev": "0.1",
         "resultado": "W"},  # Nao vai
        {"alert_id": 3, "event_id": 102, "liga": "L",
         "mercado": "cantos", "lado": "under", "linha": "11.5",
         "minuto": "40", "prob_modelo": "0.55", "ev": "0.06",
         "resultado": "L"},
    ])
    n = espeleologia.ingerir_falhas_do_csv(csv_path)
    assert n == 2
    # Idempotente
    assert espeleologia.ingerir_falhas_do_csv(csv_path) == 0


def test_espeleologia_agrega_padroes_por_mercado_lado_faixa(bd_vazia):
    csv_path = _csv_falhas(bd_vazia, [
        {"alert_id": i, "event_id": 100 + i, "liga": "L",
         "mercado": "golos", "lado": "over", "linha": "2.5",
         "minuto": "70", "prob_modelo": "0.6", "ev": "0.08",
         "resultado": "L"}
        for i in range(1, 5)  # 4 falhas na faixa 60-75
    ])
    espeleologia.ingerir_falhas_do_csv(csv_path)
    padroes = espeleologia.agregar_padroes(min_ocorrencias=3)
    assert len(padroes) == 1
    p = padroes[0]
    assert p.mercado == "golos"
    assert p.lado == "over"
    assert p.faixa_minuto == "60-75"
    assert p.n == 4


def test_espeleologia_filtra_abaixo_do_minimo(bd_vazia):
    csv_path = _csv_falhas(bd_vazia, [
        {"alert_id": 1, "event_id": 100, "liga": "L",
         "mercado": "golos", "lado": "over", "linha": "2.5",
         "minuto": "70", "prob_modelo": "0.6", "ev": "0.08",
         "resultado": "L"},
    ])
    espeleologia.ingerir_falhas_do_csv(csv_path)
    assert espeleologia.agregar_padroes(min_ocorrencias=3) == []


def test_espeleologia_classificar_falha_marca_categoria(bd_vazia):
    csv_path = _csv_falhas(bd_vazia, [
        {"alert_id": 42, "event_id": 100, "liga": "L",
         "mercado": "cartoes", "lado": "over", "linha": "4.5",
         "minuto": "85", "prob_modelo": "0.6", "ev": "0.08",
         "resultado": "L"},
    ])
    espeleologia.ingerir_falhas_do_csv(csv_path)
    assert espeleologia.classificar_falha(42, "arbitro_tolerante", "VAR nao marcou falta")
    from sqlalchemy import select
    from apostas.utils.schema import FalhaAlerta
    with db_mod.abrir_sessao() as s:
        row = s.scalar(select(FalhaAlerta).where(FalhaAlerta.alert_id == 42))
        assert row.categoria == "arbitro_tolerante"
        assert row.nota == "VAR nao marcou falta"


def test_espeleologia_formatar_sem_dados_devolve_texto():
    texto = espeleologia.formatar_relatorio_padroes([])
    assert "Sem padrões" in texto


# ═══════════════════════ M21 — Hipoteses ═════════════════════════════

def test_hipoteses_adicionar_e_listar(bd_vazia):
    h1 = hipoteses.adicionar("Teste 1", "Descricao 1")
    h2 = hipoteses.adicionar("Teste 2", "Descricao 2")
    assert h1 != h2

    todas = hipoteses.listar()
    assert len(todas) == 2
    assert todas[0].titulo in ("Teste 1", "Teste 2")
    for h in todas:
        assert h.estado == "ativa"


def test_hipoteses_filtra_por_estado(bd_vazia):
    h1 = hipoteses.adicionar("A", "...")
    h2 = hipoteses.adicionar("B", "...")
    hipoteses.resolver(h1, "confirmada", decisao_final="Integrar")

    ativas = hipoteses.listar(estado="ativa")
    confirmadas = hipoteses.listar(estado="confirmada")
    assert len(ativas) == 1
    assert ativas[0].id == h2
    assert len(confirmadas) == 1
    assert confirmadas[0].id == h1


def test_hipoteses_resolver_preenche_timestamps_e_evidencia(bd_vazia):
    hid = hipoteses.adicionar("T", "D")
    antes = datetime.utcnow()
    hipoteses.resolver(
        hid, "refutada",
        evidencia_contra="n=50, ROI -3%",
        decisao_final="Nao integrar",
    )
    h = hipoteses.obter(hid)
    assert h.estado == "refutada"
    assert h.resolvido_em is not None
    assert h.resolvido_em >= antes
    assert h.evidencia_contra == "n=50, ROI -3%"
    assert h.decisao_final == "Nao integrar"


def test_hipoteses_rejeita_estado_invalido(bd_vazia):
    hid = hipoteses.adicionar("T", "D")
    with pytest.raises(ValueError):
        hipoteses.resolver(hid, "ativa")  # nao pode re-abrir
    with pytest.raises(ValueError):
        hipoteses.resolver(hid, "xpto")


def test_hipoteses_resolver_devolve_false_se_nao_existe(bd_vazia):
    assert hipoteses.resolver(9999, "confirmada") is False


def test_hipoteses_adicionar_valida_input(bd_vazia):
    with pytest.raises(ValueError):
        hipoteses.adicionar("", "desc")
    with pytest.raises(ValueError):
        hipoteses.adicionar("titulo", "   ")


def test_hipoteses_formatar_detalhe_tem_estado_e_titulo(bd_vazia):
    hid = hipoteses.adicionar("Overs sabado 21h", "ligas Big-5")
    hipoteses.resolver(
        hid, "confirmada",
        evidencia_a_favor="n=42, ROI +9%",
        decisao_final="Integrar",
    )
    h = hipoteses.obter(hid)
    texto = hipoteses.formatar_detalhe(h)
    assert "confirmada" in texto
    assert "Overs sabado 21h" in texto
    assert "Integrar" in texto
