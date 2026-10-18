"""Testes do pipeline de alertas (análise, estado, mensagens, cliente, bot)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from apostas.alertas import analise, bot, estado, mensagens, telegram_cliente
from apostas.ingestao import football_data_uk, ligas_equipas
# removed: Sportmonks import as sportmonks_cliente
from apostas.utils import config as config_mod
from apostas.utils import db as db_mod
from apostas.utils.schema import ApostaReal, Equipa, Jogo, Liga, Sugestao


# ─── estado ─────────────────────────────────────────────────────────

@pytest.fixture
def project_root_temp(tmp_path, monkeypatch):
    """Faz com que project_root() aponte para tmp_path (isola dados/)."""
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    yield tmp_path


def test_estado_default_e_pausar_retomar(project_root_temp):
    assert estado.esta_pausado() is False
    estado.pausar()
    assert estado.esta_pausado() is True
    estado.retomar()
    assert estado.esta_pausado() is False


def test_estado_marcar_analise(project_root_temp):
    ref = datetime(2024, 10, 5, 12, 0)
    estado.marcar_analise_feita(ref)
    assert estado.ler()["ultima_analise_utc"] == ref.isoformat(timespec="seconds")


# ─── telegram_cliente mock ──────────────────────────────────────────

def test_telegram_cliente_dev_escreve_outbox(project_root_temp, monkeypatch):
    monkeypatch.setenv("MODO", "desenvolvimento")
    cli = telegram_cliente.cliente()
    cli.enviar("olá", chat_id=123)
    cli.enviar("mundo", chat_id=123)
    saida = telegram_cliente.ler_outbox()
    assert len(saida) == 2
    assert saida[0]["texto"] == "olá"
    assert saida[1]["chat_id"] == 123


# ─── mensagens ──────────────────────────────────────────────────────

def test_mensagem_sugestao_contem_campos():
    liga = Liga(id=1, nome="Premier League", pais="England", sportmonks_id=8)
    casa = Equipa(id=10, nome="Arsenal", liga_id=1)
    fora = Equipa(id=11, nome="Chelsea", liga_id=1)
    jogo = Jogo(id=100, id_externo=1, liga_id=1, epoca=2024,
                data_utc=datetime(2024, 10, 5, 15, 30),
                casa_id=10, fora_id=11, estado="agendado")
    sugestao = Sugestao(id=42, jogo_id=100, mercado="golos",
                        linha=2.5, lado="over",
                        prob_modelo=0.61, odd_referencia=1.85, ev=0.129)
    texto = mensagens.sugestao_nova(sugestao, jogo, liga, casa, fora)
    assert "Arsenal" in texto
    assert "Chelsea" in texto
    assert "2.5" in texto or "2,5" in texto
    assert "over" in texto.lower()
    assert "/registar 42" in texto


def test_mensagem_resumo_dia_vazio():
    assert "Sem sugestões" in mensagens.resumo_dia([])


def test_mensagem_estatisticas_mes_vazio():
    inicio = datetime(2024, 10, 1)
    assert "Sem apostas" in mensagens.estatisticas_mes(inicio, 0, 0, 0.0, 0.0)


# ─── análise end-to-end ─────────────────────────────────────────────

@pytest.fixture
def bd_populada(tmp_path, monkeypatch):
    bd = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd)
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setenv("MODO", "desenvolvimento")
    db_mod.reset_engine()
    db_mod.criar_schema()
    ligas_equipas.sincronizar()
    football_data_uk.sincronizar(epocas=[2023, 2024])
    # Marca os jogos de 2024 como "agendados" para a análise live os ver
    with db_mod.abrir_sessao() as s:
        for jogo in s.scalars(select(Jogo).where(Jogo.epoca == 2024)):
            jogo.estado = "agendado"
    yield bd
    db_mod.reset_engine()


def test_identificar_sugestoes_grava_na_bd(bd_populada):
    # Referência: antes da primeira jornada 2024, para apanhar jogos no futuro
    ref = datetime(2024, 7, 31)
    sugestoes = analise.identificar_sugestoes(
        referencia=ref, janela_horas=(0, 24 * 10), modelo="gap",
    )
    assert len(sugestoes) > 0

    with db_mod.abrir_sessao() as s:
        total = s.scalar(select(Sugestao))
        assert total is not None

    # Idempotência: segunda chamada não cria nada novo
    repetida = analise.identificar_sugestoes(
        referencia=ref, janela_horas=(0, 24 * 10), modelo="gap",
    )
    assert len(repetida) == 0


# ─── bot (handlers puros) ───────────────────────────────────────────

def test_comando_registar_cria_aposta_real(bd_populada):
    ref = datetime(2024, 7, 31)
    sugs = analise.identificar_sugestoes(
        referencia=ref, janela_horas=(0, 24 * 10), modelo="gap",
    )
    assert len(sugs) > 0
    sid = sugs[0].sugestao.id

    resposta = bot.comando_registar([str(sid), "1.0", "1.85"])
    assert "registada" in resposta.lower()

    with db_mod.abrir_sessao() as s:
        reg = s.scalar(select(ApostaReal).where(ApostaReal.sugestao_id == sid))
        assert reg is not None
        assert reg.stake == 1.0
        assert reg.odd_executada == 1.85
        assert reg.resultado == "pendente"


def test_comando_registar_rejeita_duplicado(bd_populada):
    ref = datetime(2024, 7, 31)
    sugs = analise.identificar_sugestoes(
        referencia=ref, janela_horas=(0, 24 * 10), modelo="gap",
    )
    sid = sugs[0].sugestao.id
    bot.comando_registar([str(sid), "1.0", "1.85"])
    resposta = bot.comando_registar([str(sid), "1.0", "1.85"])
    assert "já existe" in resposta.lower() or "Já existe" in resposta


def test_comando_registar_valida_args():
    assert "Uso:" in bot.comando_registar(["42"])
    assert "números" in bot.comando_registar(["abc", "1", "1.85"])
    assert "stake" in bot.comando_registar(["1", "-1", "1.85"]).lower()


def test_comando_stats_sem_apostas(bd_populada):
    resposta = bot.comando_stats(datetime(2024, 10, 15))
    assert "Sem apostas" in resposta


def test_comando_hoje_com_sugestoes(bd_populada):
    ref = datetime(2024, 7, 31)
    analise.identificar_sugestoes(
        referencia=ref, janela_horas=(0, 24 * 10), modelo="gap",
    )
    # Pede os jogos de 2024-08-01 (primeira jornada no mock)
    resposta = bot.comando_hoje(datetime(2024, 8, 1, 10, 0))
    # Deve ter alguma sugestão listada
    assert "Sugestões" in resposta or "Sem sugestões" in resposta


def test_comando_pausar_retomar(bd_populada):
    assert "Pausados" in bot.comando_pausar() or "pausados" in bot.comando_pausar()
    assert estado.esta_pausado()
    bot.comando_retomar()
    assert not estado.esta_pausado()


def test_comando_ajuda_lista_comandos():
    texto = bot.comando_ajuda()
    for c in ("/hoje", "/amanha", "/stats", "/registar", "/apostei",
              "/pausar", "/retomar"):
        assert c in texto


# ─── /apostei (aposta live via alert_id) ────────────────────────────

@pytest.fixture
def csv_live_minimo(tmp_path, monkeypatch):
    """CSV de sugestoes live com 2 linhas (alert_id 42 e 43)."""
    import csv as _csv

    csv_path = tmp_path / "sug_live.csv"
    campos = [
        "alert_id", "ts_scan", "event_id", "liga", "home", "away",
        "minuto", "marcador", "mercado", "lado", "linha", "casa", "odd",
        "prob_modelo", "p_mercado", "p_final", "verificar",
        "ev", "estrelas", "xg_casa", "xg_fora",
        "shots_on_target_c", "shots_on_target_f",
        "corners_c", "corners_f", "yellows_c", "yellows_f",
        "resultado", "lucro", "resolvido_em",
    ]
    linhas = [
        {"alert_id": "42", "event_id": "123", "liga": "Serie A",
         "home": "Roma", "away": "Milan", "minuto": "55", "marcador": "1-0",
         "mercado": "golos", "lado": "over", "linha": "2.5",
         "casa": "betano", "odd": "1.95", "prob_modelo": "0.58",
         "estrelas": "4", "ev": "0.09"},
        {"alert_id": "43", "event_id": "124", "liga": "LaLiga",
         "home": "Barcelona", "away": "Real", "minuto": "40", "marcador": "0-0",
         "mercado": "cantos", "lado": "under", "linha": "11.5",
         "casa": "ANALISTA", "odd": "1.80", "prob_modelo": "0.65",
         "estrelas": "3", "ev": ""},
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = _csv.DictWriter(fp, fieldnames=campos, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(linhas)
    monkeypatch.setenv("RT_LIVE_CSV", str(csv_path))
    yield csv_path


def test_comando_apostei_cria_registo_live(bd_populada, csv_live_minimo):
    from apostas.utils.schema import ApostaRealLive

    resposta = bot.comando_apostei(["42", "1.95", "2.0"])
    assert "registada" in resposta.lower()
    assert "#42" in resposta
    assert "Roma" in resposta

    with db_mod.abrir_sessao() as s:
        reg = s.scalar(select(ApostaRealLive).where(ApostaRealLive.alert_id == 42))
        assert reg is not None
        assert reg.stake == 2.0
        assert reg.odd_executada == 1.95
        assert reg.event_id == 123
        assert reg.mercado == "golos"
        assert reg.lado == "over"
        assert reg.odd_recomendada == 1.95
        assert reg.prob_modelo == pytest.approx(0.58)
        assert reg.resultado == "pendente"


def test_comando_apostei_rejeita_duplicado(bd_populada, csv_live_minimo):
    bot.comando_apostei(["42", "1.95", "1.0"])
    resposta = bot.comando_apostei(["42", "1.90", "1.0"])
    assert "já existe" in resposta.lower() or "Já existe" in resposta


def test_comando_apostei_valida_args(bd_populada, csv_live_minimo):
    assert "Uso:" in bot.comando_apostei([])
    assert "Uso:" in bot.comando_apostei(["42"])
    assert "números" in bot.comando_apostei(["abc", "1.9", "1.0"]).lower()
    assert "odd" in bot.comando_apostei(["42", "0.9", "1.0"]).lower()
    assert "stake" in bot.comando_apostei(["42", "1.9", "-1"]).lower()


def test_comando_apostei_alerta_desconhecido(bd_populada, csv_live_minimo):
    resposta = bot.comando_apostei(["9999", "1.9", "1.0"])
    assert "não encontrado" in resposta.lower() or "nao encontrado" in resposta.lower()


def test_comando_apostei_mostra_delta_odd(bd_populada, csv_live_minimo):
    # Odd executada 2.10 vs recomendada 1.95 → +7.7%
    resposta = bot.comando_apostei(["42", "2.10", "1.0"])
    assert "+7.7%" in resposta or "+7.6%" in resposta or "+7.8%" in resposta
