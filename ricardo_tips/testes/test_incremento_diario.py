"""Incremento diario: grava so os jogos terminados recentes que faltam na BD."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apostas.modelos import incremento_diario as inc

AGORA = datetime(2026, 11, 7, 0, 15)


def _ts(horas_atras: float) -> int:
    return int((AGORA - timedelta(hours=horas_atras)).replace(tzinfo=timezone.utc).timestamp())


def _ev(eid, horas_atras, code=100):
    return {"id": eid, "startTimestamp": _ts(horas_atras), "status": {"code": code}}


@pytest.fixture
def bd(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    yield
    db_mod._engine = None
    db_mod._SessionLocal = None


@pytest.fixture
def sofascore(monkeypatch):
    """Liga 17 (Premier) com 4 jogos: 2 recentes terminados, 1 a decorrer, 1 antigo."""
    respostas = {
        "/unique-tournament/17/seasons": {"seasons": [{"id": 900}]},
        "/unique-tournament/17/season/900/events/last/0": {"events": [
            _ev(1, 5), _ev(2, 30), _ev(3, 2, code=6), _ev(4, 24 * 6)]},
    }
    pedidos: list[str] = []
    monkeypatch.setattr(inc, "_get_json", lambda p: pedidos.append(p) or respostas.get(p))
    monkeypatch.setattr(inc, "ligas_alvo", lambda: {17: "Premier League (ENG)"})
    monkeypatch.setattr(inc, "_PAUSA_S", 0)
    return pedidos


def test_so_jogos_terminados_dentro_da_janela(sofascore):
    assert inc.eventos_terminados(17, _ts(72)) == [1, 2]


def test_grava_so_o_que_falta(bd, sofascore, monkeypatch):
    from apostas.modelos import historico_equipas, historico_jogadores
    from apostas.utils.db import abrir_sessao
    from apostas.utils.schema import EstatisticaEquipaJogo, EstatisticaJogadorJogo

    with abrir_sessao() as s:  # jogo 1 ja completo; jogo 2 so tem as equipas
        for ev, team in ((1, 10), (1, 11), (2, 12), (2, 13)):
            s.add(EstatisticaEquipaJogo(sofascore_event_id=ev, sofascore_team_id=team,
                                        nome_equipa="X", era_casa=team % 2 == 0,
                                        data_jogo=AGORA - timedelta(days=1), resultado="W"))
        s.add(EstatisticaJogadorJogo(sofascore_player_id=5, sofascore_event_id=1,
                                     sofascore_team_id=10, nome_jogador="Y",
                                     data_jogo=AGORA - timedelta(days=1), era_casa=True))
    equipas, jogadores = [], []
    monkeypatch.setattr(historico_equipas, "gravar_jogo_terminado",
                        lambda eid: equipas.append(eid) or 2)
    monkeypatch.setattr(historico_jogadores, "gravar_jogadores_terminado",
                        lambda eid: jogadores.append(eid) or 30)
    r = inc.correr(dias=3, agora=AGORA)
    assert equipas == [] and jogadores == [2]
    assert (r.jogos_vistos, r.jogos_novos, r.linhas_jogadores) == (2, 1, 30)
    assert r.equipas_total == 4 and r.equipas_fortes == 0
    assert "1 novos" in r.texto() and "Fichas fortes (5+ jogos): 0 de 4" in r.texto()


def test_erro_num_jogo_nao_para_o_resto(bd, sofascore, monkeypatch):
    from apostas.modelos import historico_equipas, historico_jogadores

    def falha_no_1(eid):
        if eid == 1:
            raise RuntimeError("timeout")
        return 2
    monkeypatch.setattr(historico_equipas, "gravar_jogo_terminado", falha_no_1)
    monkeypatch.setattr(historico_jogadores, "gravar_jogadores_terminado", lambda eid: 20)
    r = inc.correr(dias=3, agora=AGORA)
    assert r.erros == 1 and r.linhas_equipas == 2 and "ficam para a proxima noite" in r.texto()


def test_ligas_alvo_inclui_scanner_e_prioritarias():
    alvo = inc.ligas_alvo()
    assert 17 in alvo and 238 in alvo and len(alvo) >= 24


def test_instalador_tem_a_tarefa_e_scanner_sem_limite():
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent / "scripts"
    geral = (raiz / "instalar_tasks_windows.ps1").read_text(encoding="utf-8")
    assert "RicardoTips_IncrementoDiario" in geral and 'Time = "00:15"' in geral
    assert "SemLimiteTempo = $true" in geral and "--rapido" in geral
    so_este = (raiz / "instalar_incremento_diario.ps1").read_text(encoding="utf-8")
    assert '"PT0S"' in so_este and "incremento_diario.py" in so_este


def test_script_corre_incremento_e_resolver(monkeypatch, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import incremento_diario as script
    import resolver_sugestoes_live
    from apostas.utils import db as db_mod
    monkeypatch.setattr(db_mod, "criar_schema", lambda: None)
    monkeypatch.setattr(script.incremento_diario, "correr",
                        lambda dias, agora: inc.ResumoIncremento(ligas=24, jogos_vistos=60,
                                                                 jogos_novos=12))
    desde = []
    monkeypatch.setattr(resolver_sugestoes_live, "resolver", lambda desde=None: desde_append(desde))

    def desde_append(d):
        desde.append(d)
    from apostas.modelos import cartoes_finais, correcao_ritmo
    monkeypatch.setattr(cartoes_finais, "recalcular_e_gravar", lambda s: cartoes_finais.Historia())
    from apostas.relatorios import prime
    from apostas.utils import backup
    monkeypatch.setattr(prime, "recalcular_e_gravar", lambda: prime.Prime())
    from apostas.relatorios import vs_casa
    monkeypatch.setattr(vs_casa, "recalcular_e_gravar", lambda: vs_casa.VsCasa())
    from apostas.utils import limpeza_competicoes
    limpezas = []
    monkeypatch.setattr(limpeza_competicoes, "limpar", lambda: limpezas.append(1) or
                        limpeza_competicoes.ResultadoLimpeza(jogos=2, linhas_csv=5,
                                                             competicoes={"Club Friendly Games": 2}))
    copias = []
    monkeypatch.setattr(backup, "fazer_backup", lambda: copias.append(1) or backup.ResultadoBackup(
        pasta=Path("dados/backups/hoje"), ficheiros=5, tamanho_mb=12.0, apagados=0))
    recalculos = []
    monkeypatch.setattr(correcao_ritmo, "recalcular_e_gravar",
                        lambda s: recalculos.append(1) or correcao_ritmo.CorrecaoRitmo())
    monkeypatch.setattr(sys, "argv", ["incremento_diario.py", "--dias", "2"])
    script.main()
    out = capsys.readouterr().out
    assert recalculos == [1] and "Ritmo do resto do jogo" in out
    assert "Nos vs Casas" in out
    assert limpezas == [1] and "Fora: 2 jogos" in out
    assert copias == [1] and "Copia de seguranca: 5 ficheiros" in out
    assert "24 ligas · 60 jogos terminados · 12 novos" in out
    assert len(desde) == 1 and desde[0].hour == 0
