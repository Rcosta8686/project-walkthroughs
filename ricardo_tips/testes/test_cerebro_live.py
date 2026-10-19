"""Testes Fase 5 — cerebro_live liga mini-cerebros ao predictor."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from apostas.alertas import cerebro_live


# ─── Helper: mock ResultadoConfronto ───────────────────────────────

def _mock_confronto(padroes=None, alertas=None,
                    nome_casa="Roma", nome_fora="Milan"):
    """Produz um objecto parecido com ResultadoConfronto para o teste."""
    class _Res:
        pass
    r = _Res()
    r.padroes_cruzados = padroes or []
    r.alertas = alertas or []
    r.nome_casa = nome_casa
    r.nome_fora = nome_fora
    return r


# ─── Cache ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _limpar_cache():
    cerebro_live.limpar_cache()
    yield
    cerebro_live.limpar_cache()


def test_devolve_none_sem_confronto_disponivel():
    with patch("apostas.alertas.confronto.analisar_confronto",
               return_value=None):
        res = cerebro_live.calcular_ajustes(100, 200, minuto=55,
                                             score_casa=1, score_fora=0)
    assert res is None


def test_ajustes_vazios_quando_sem_padroes():
    """Confronto sem padroes activos — multiplicadores todos a 1.0."""
    with patch("apostas.alertas.confronto.analisar_confronto",
               return_value=_mock_confronto()):
        aj = cerebro_live.calcular_ajustes(100, 200, minuto=55,
                                            score_casa=1, score_fora=0)
    assert aj is not None
    assert aj.lam_golos_mult == 1.0
    assert aj.lam_cantos_mult == 1.0
    assert aj.lam_cards_mult == 1.0
    assert aj.lam_shots_casa_mult == 1.0
    assert aj.lam_shots_fora_mult == 1.0
    assert aj.notas == []


def test_padrao_ambas_carregam_2t_aumenta_cards_e_shots():
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            padroes=["⚡ **Ambas carregam na 2ª parte** (teste)"],
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    assert aj.lam_cards_mult == pytest.approx(1.15)
    assert aj.lam_shots_casa_mult == pytest.approx(1.05)
    assert aj.lam_shots_fora_mult == pytest.approx(1.05)
    assert any("Ambas carregam" in n for n in aj.notas)


def test_padrao_disciplinar_aumenta_cards():
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            padroes=["🟡 Discrepancia disciplinar: Roma (3.5) vs Milan (1.2)"],
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    assert aj.lam_cards_mult == pytest.approx(1.10)


def test_padrao_posse_clara_aumenta_cantos():
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            padroes=["🎯 Barcelona tem posse clara (65% vs 35%)"],
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    assert aj.lam_cantos_mult == pytest.approx(1.10)


def test_fadiga_casa_reduz_shots_casa_so():
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            alertas=["⏱ Roma: 3 jogos nos ultimos 7d (fadiga)"],
            nome_casa="Roma", nome_fora="Milan",
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    assert aj.lam_shots_casa_mult == pytest.approx(0.95)
    assert aj.lam_shots_fora_mult == pytest.approx(1.0)


def test_fadiga_fora_reduz_shots_fora_so():
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            alertas=["🛌 Milan jogou ha 2 dias (impacto na 2a parte)"],
            nome_casa="Roma", nome_fora="Milan",
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    assert aj.lam_shots_fora_mult == pytest.approx(0.95)
    assert aj.lam_shots_casa_mult == pytest.approx(1.0)


def test_multiplos_padroes_acumulam_com_cap():
    """Varios padroes somam, mas cap a 1.30."""
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(
            padroes=[
                "⚡ ambas carregam 2T",            # cards×1.15
                "🟡 disciplinar",                    # cards×1.10
            ],
        ),
    ):
        aj = cerebro_live.calcular_ajustes(100, 200)
    # 1.15 × 1.10 = 1.265 ≤ 1.30 → fica 1.265
    assert aj.lam_cards_mult == pytest.approx(1.265)


def test_cache_evita_chamada_duplicada():
    agora = datetime.utcnow()
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(),
    ) as spy:
        cerebro_live.calcular_ajustes(100, 200, agora=agora)
        cerebro_live.calcular_ajustes(100, 200, agora=agora)
        cerebro_live.calcular_ajustes(100, 200, agora=agora)
    assert spy.call_count == 1


def test_cache_expira_apos_ttl():
    agora = datetime.utcnow()
    with patch(
        "apostas.alertas.confronto.analisar_confronto",
        return_value=_mock_confronto(),
    ) as spy:
        cerebro_live.calcular_ajustes(100, 200, agora=agora)
        # Avanca o relogio alem do TTL
        cerebro_live.calcular_ajustes(
            100, 200, agora=agora + timedelta(minutes=11),
        )
    assert spy.call_count == 2


# ─── Integracao com predictor ──────────────────────────────────────

def test_predictor_aplica_mult_cards_quando_ajustes_dados():
    from dataclasses import dataclass
    from apostas.alertas.cerebro_live import AjustesCerebro
    from apostas.modelos import live_predictor

    @dataclass
    class _Estado:
        event_id: int = 1
        home: str = "A"
        away: str = "B"
        liga: str = "L"
        minuto: int = 60
        estado: str = "2H"
        marcador_casa: int = 1
        marcador_fora: int = 1
        xg_casa: float | None = 1.0
        xg_fora: float | None = 1.0
        shots_total_casa: int | None = 10
        shots_total_fora: int | None = 10
        shots_on_target_casa: int | None = 3
        shots_on_target_fora: int | None = 3
        big_chances_casa: int | None = 1
        big_chances_fora: int | None = 1
        possession_casa: float | None = 0.5
        possession_fora: float | None = 0.5
        corners_casa: int | None = 3
        corners_fora: int | None = 3
        shots_blocked_casa: int | None = 1
        shots_blocked_fora: int | None = 1
        yellow_cards_casa: int = 2
        yellow_cards_fora: int = 2
        red_cards_casa: int = 0
        red_cards_fora: int = 0
        fouls_casa: int | None = 8
        fouls_fora: int | None = 8
        arbitro: str | None = None
        xg_recent_casa: float | None = None
        xg_recent_fora: float | None = None
        momentum_actual: str | None = None
        home_id: int | None = 100
        away_id: int | None = 200

    estado = _Estado()
    prev_sem = live_predictor.prever(estado)
    aj = AjustesCerebro(lam_cards_mult=1.20)
    prev_com = live_predictor.prever(estado, ajustes_cerebro=aj)
    # Multiplicador de 1.20 deve bater o lambda de cards por ~1.20x
    assert prev_com.lam_cartoes_amarelos_restantes == pytest.approx(
        prev_sem.lam_cartoes_amarelos_restantes * 1.20, rel=0.01,
    )
