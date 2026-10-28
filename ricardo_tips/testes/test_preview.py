"""Testes para apostas/relatorios/preview.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from apostas.relatorios.preview import (
    formatar_preview_telegram, gerar_preview,
)


def _mock_evento(event_id: int, home: str, away: str,
                 home_id: int, away_id: int,
                 start_ts: int, liga: str = "Serie A") -> dict:
    return {
        "id": event_id,
        "homeTeam": {"id": home_id, "name": home},
        "awayTeam": {"id": away_id, "name": away},
        "tournament": {"name": liga, "uniqueTournament": {"name": liga}},
        "startTimestamp": start_ts,
    }


def _mock_confronto(padroes=None, alertas=None):
    @dataclass
    class _Res:
        nome_casa: str = "Roma"
        nome_fora: str = "Milan"
        ficha_casa: object = None
        ficha_fora: object = None
        remates_esperados: float = 24.0
        sot_esperados: float = 8.5
        cantos_esperados: float = 10.2
        golos_esperados: float = 2.6
        cards_am_esperados: float = 4.5
        padroes_cruzados: list = field(default_factory=list)
        alertas: list = field(default_factory=list)
        minuto: int | None = None
        score_casa: int | None = None
        score_fora: int | None = None
    r = _Res()
    r.padroes_cruzados = padroes or []
    r.alertas = alertas or []
    return r


def test_gerar_preview_filtra_pela_janela():
    agora = datetime(2026, 10, 19, 12, 0)
    dentro = int(datetime(2026, 10, 19, 20, 0).timestamp())
    fora_antes = int(datetime(2026, 10, 18, 20, 0).timestamp())
    fora_depois = int(datetime(2026, 10, 21, 20, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "Roma", "Milan", 100, 200, dentro),
        _mock_evento(2, "Barca", "Real", 300, 400, fora_antes),
        _mock_evento(3, "Benfica", "Porto", 500, 600, fora_depois),
    ])
    confronto = MagicMock(return_value=_mock_confronto())

    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    assert len(preview.jogos) == 1
    assert preview.jogos[0].home == "Roma"


def test_gerar_preview_passa_padroes_do_confronto():
    agora = datetime(2026, 10, 19, 12, 0)
    ts = int(datetime(2026, 10, 19, 20, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "Roma", "Milan", 100, 200, ts),
    ])
    confronto = MagicMock(return_value=_mock_confronto(
        padroes=["⚡ Ambas carregam 2T", "🟡 Discrepancia disciplinar"],
        alertas=["⏱ Roma: 3 jogos 7d"],
    ))

    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    j = preview.jogos[0]
    assert len(j.padroes) == 2
    assert "⚡" in j.padroes[0]
    assert len(j.alertas) == 1


def test_gerar_preview_sem_confronto_mantem_jogo():
    """Jogo sem fichas suficientes continua a aparecer no preview."""
    agora = datetime(2026, 10, 19, 12, 0)
    ts = int(datetime(2026, 10, 19, 20, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "X", "Y", 999, 998, ts),
    ])
    confronto = MagicMock(return_value=None)

    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    assert len(preview.jogos) == 1
    assert preview.jogos[0].padroes == []
    assert preview.jogos[0].cards_esperados is None


def test_gerar_preview_dedup_por_event_id():
    agora = datetime(2026, 10, 19, 12, 0)
    ts = int(datetime(2026, 10, 19, 20, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "A", "B", 1, 2, ts),
        _mock_evento(1, "A", "B", 1, 2, ts),  # duplicado
    ])
    confronto = MagicMock(return_value=_mock_confronto())
    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    assert len(preview.jogos) == 1


def test_formatar_preview_vazio_mostra_mensagem():
    from apostas.relatorios.preview import PreviewDia
    p = PreviewDia(
        data_inicio=datetime(2026, 10, 19),
        data_fim=datetime(2026, 10, 20),
    )
    texto = formatar_preview_telegram(p)
    assert "Sem jogos" in texto


def test_formatar_preview_tem_icones_para_padroes():
    agora = datetime(2026, 10, 19, 12, 0)
    ts = int(datetime(2026, 10, 19, 20, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "Roma", "Milan", 100, 200, ts),
    ])
    confronto = MagicMock(return_value=_mock_confronto(
        padroes=["⚡ Ambas carregam 2T"],
    ))
    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    texto = formatar_preview_telegram(preview)
    # Icone ⚡ aparece na linha-resumo
    assert "⚡" in texto
    # E o padrao detalhado tambem aparece
    assert "Ambas carregam" in texto
    # Projeccoes aparecem
    assert "🟨" in texto
    assert "4.5" in texto


def test_escolher_aposta_golos_altos_sugere_over():
    """Projeccao 4.7 golos → recomenda OVER 3.5 com estrelas altas."""
    from apostas.relatorios.preview import LinhaJogoPreview, _escolher_aposta
    j = LinhaJogoPreview(
        hora="19:00", liga="L", home="A", away="B",
        event_id=1, home_id=1, away_id=2,
        golos_esperados=4.7,
    )
    aposta = _escolher_aposta(j)
    assert aposta is not None
    assert aposta.mercado == "golos"
    assert aposta.direccao == "over"
    assert aposta.linha == 3.5
    assert aposta.estrelas == 3  # edge > 0.18


def test_escolher_aposta_projecao_perto_da_linha_sem_recomendacao():
    """Projeccao 2.5 (= linha) nao sugere nada — edge insignificante."""
    from apostas.relatorios.preview import LinhaJogoPreview, _escolher_aposta
    j = LinhaJogoPreview(
        hora="19:00", liga="L", home="A", away="B",
        event_id=1, home_id=1, away_id=2,
        golos_esperados=2.5,  # exactamente na linha
    )
    assert _escolher_aposta(j) is None


def test_escolher_aposta_escolhe_mercado_com_maior_edge():
    """Dado varios mercados, escolhe aquele com maior |prob-0.5|."""
    from apostas.relatorios.preview import LinhaJogoPreview, _escolher_aposta
    j = LinhaJogoPreview(
        hora="19:00", liga="L", home="A", away="B",
        event_id=1, home_id=1, away_id=2,
        golos_esperados=2.5,     # neutro (linha 2.5)
        cards_esperados=4.5,      # neutro (linha 4.5)
        cantos_esperados=14.0,    # muito acima — linha 12.5 fica edge >~0.13
        sot_esperados=8.5,        # neutro
    )
    aposta = _escolher_aposta(j)
    assert aposta is not None
    assert aposta.mercado == "cantos"
    assert aposta.direccao == "over"


def test_formatar_linha_inclui_aposta_quando_edge_forte():
    """Preview com projecao forte mostra linha '⭐⭐⭐ ... OVER X.X ...'."""
    from apostas.relatorios.preview import LinhaJogoPreview, _formatar_linha
    j = LinhaJogoPreview(
        hora="19:00", liga="Serie A", home="Napoli", away="Pisa",
        event_id=1, home_id=1, away_id=2,
        golos_esperados=4.5,
        cantos_esperados=11.0,
    )
    linhas = _formatar_linha(j)
    txt = "\n".join(linhas)
    assert "⭐" in txt
    assert "OVER" in txt
    assert "@ " in txt  # odd justa


def test_formatar_preview_ordena_por_hora():
    agora = datetime(2026, 10, 19, 12, 0)
    ts_19h = int(datetime(2026, 10, 19, 19, 0).timestamp())
    ts_17h = int(datetime(2026, 10, 19, 17, 0).timestamp())
    listar = MagicMock(return_value=[
        _mock_evento(1, "Tarde", "Tarde2", 1, 2, ts_19h),
        _mock_evento(2, "Cedo", "Cedo2", 3, 4, ts_17h),
    ])
    confronto = MagicMock(return_value=_mock_confronto())
    preview = gerar_preview(
        agora, agora + timedelta(days=1),
        listar_fn=listar, confronto_fn=confronto,
    )
    # Cedo (17h) vem primeiro
    assert preview.jogos[0].home == "Cedo"
    assert preview.jogos[1].home == "Tarde"
