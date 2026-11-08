"""Grafico de acertos/falhas das dicas enviadas: barras por dia, semana e mes."""
from __future__ import annotations

from datetime import datetime

import pytest

from apostas.relatorios import grafico_acertos as ga
from apostas.relatorios.alertas_enviados import DicaEnviada

ATE = datetime(2026, 11, 8)    # a noite de 08/11: o ultimo dia e 07/11


def _dica(ts, res, lucro=None, mercado="🚩 cantos", lado="over", tipo="alerta"):
    return DicaEnviada(ts=ts, tipo=tipo, event_id=1, mercado=mercado, lado=lado, linha="9.5",
                       odd=1.9, resultado=res, lucro=lucro)


def test_baldes_por_dia_semana_e_mes():
    dias = ga.baldes_vazios("dia", ATE)
    assert len(dias) == 30 and dias[-1].inicio == datetime(2026, 11, 7)
    assert dias[-1].rotulo == "07/11" and dias[0].inicio == datetime(2026, 10, 9)
    semanas = ga.baldes_vazios("semana", ATE)        # 08/11/2026 e um domingo
    assert semanas[-1].inicio == datetime(2026, 11, 2)
    assert semanas[-1].fim == datetime(2026, 11, 9)
    assert all(b.inicio.weekday() == 0 for b in semanas)
    meses = ga.baldes_vazios("mes", datetime(2026, 11, 1))   # dia 1: o mes que acabou
    assert [b.rotulo for b in meses[-3:]] == ["ago 26", "set 26", "out 26"]
    assert meses[0].inicio == datetime(2025, 11, 1)
    jan = ga.baldes_vazios("mes", datetime(2026, 2, 1), n=2)
    assert [b.inicio for b in jan] == [datetime(2025, 12, 1), datetime(2026, 1, 1)]


def test_agrupa_acertos_falhas_lucro_e_mercados():
    dicas = [_dica(datetime(2026, 11, 7, 20), "W", 0.9),
             _dica(datetime(2026, 11, 7, 21), "L", -1.0, mercado="💥 remates"),
             _dica(datetime(2026, 11, 7, 22), "pendente"),
             _dica(datetime(2026, 11, 7, 22), "SD"),
             _dica(datetime(2026, 11, 7, 23), "push", 0.0),
             _dica(datetime(2026, 11, 6, 20), "W", 0.8, tipo="projeccao"),
             _dica(datetime(2026, 11, 8, 1), "W", 0.8),       # depois da noite: fica fora
             _dica(datetime(2026, 9, 1), "W", 0.8)]            # antes dos 30 dias
    baldes, mercados = ga.agrupar(dicas, "dia", ATE)
    ontem, anteontem = baldes[-1], baldes[-2]
    assert (ontem.acertos, ontem.falhas, ontem.pendentes, ontem.nulas) == (1, 1, 1, 1)
    assert ontem.lucro == pytest.approx(-0.1) and ontem.taxa == 0.5
    assert anteontem.acertos == 1
    nomes = {m.nome: (m.acertos, m.falhas) for m in mercados}
    assert nomes == {"Cantos over": (1, 0), "Remates over": (0, 1),
                     "Cantos over (projecção)": (1, 0)}
    leg = ga.legenda(baldes, mercados, "dia")
    assert "*Dia 07/11:* 1✅ 1❌ · acerto 50% · lucro -0.10 un · 1 por resolver" in leg
    assert "*Ultimos 30 dias:* 2✅ 1❌" in leg


def test_melhor_e_pior_mercado_com_volume():
    dicas = ([_dica(datetime(2026, 11, d, 20), "W", 0.9) for d in range(1, 7)]
             + [_dica(datetime(2026, 11, d, 21), "L", -1.0, mercado="🟨 cartoes")
                for d in range(1, 7)])
    baldes, mercados = ga.agrupar(dicas, "dia", ATE)
    leg = ga.legenda(baldes, mercados, "dia")
    assert "🟢 Melhor: Cantos over 6/6 · +5.40 un" in leg
    assert "🔴 Pior: Cartoes over 0/6 · -6.00 un" in leg
    txt = ga.texto(baldes, mercados, "dia")
    assert "` 01/11` 🟩🟩🟥🟥🟥 1/2 · -0.10" in txt    # 50%: 2.5 arredonda para 2


def test_png_e_vazio():
    pytest.importorskip("matplotlib")
    g = ga.gerar("semana", ATE, dicas=[_dica(datetime(2026, 11, 3, 20), "W", 0.9)])
    assert g.png is not None and g.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert not g.vazio
    vazio = ga.gerar("mes", ATE, dicas=[])
    assert vazio.vazio and vazio.png is not None   # desenha mesmo sem dados


def test_sem_matplotlib_vai_texto(monkeypatch):
    monkeypatch.setattr(ga, "tem_matplotlib", lambda: False)
    g = ga.gerar("dia", ATE, dicas=[_dica(datetime(2026, 11, 7, 20), "W", 0.9)])
    assert g.png is None and "🟩🟩🟩🟩🟩 1/1" in g.texto


def test_periodos_da_noite():
    assert ga.periodos_da_noite(datetime(2026, 11, 8)) == ["dia"]            # domingo
    assert ga.periodos_da_noite(datetime(2026, 11, 9)) == ["dia", "semana"]  # segunda
    assert ga.periodos_da_noite(datetime(2026, 6, 1)) == ["dia", "semana", "mes"]


def test_nome_limpo_tira_emojis():
    assert ga.nome_limpo("🎯 remates a baliza under (projecção)") == \
        "Remates a baliza under (projecção)"
    assert ga.nome_limpo("⚽ golos over") == "Golos over"


def test_envia_uma_vez_por_noite(tmp_path, monkeypatch):
    from apostas.utils import config as config_mod
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setattr(ga, "project_root", lambda: tmp_path)
    assert not ga.ja_enviado("dia", ATE)
    ga.marcar_enviado("dia", ATE)
    assert ga.ja_enviado("dia", ATE) and not ga.ja_enviado("semana", ATE)


def test_foto_no_modo_desenvolvimento(tmp_path, monkeypatch):
    from apostas.alertas import telegram_cliente as tc
    from apostas.utils import config as config_mod
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    tc.ClienteTelegram(modo="desenvolvimento").enviar_foto(b"\x89PNG", "*legenda*")
    fotos = list((tmp_path / "dados" / "telegram_fotos").glob("*.png"))
    assert len(fotos) == 1 and fotos[0].read_bytes() == b"\x89PNG"
    assert "*legenda*" in tc.ler_outbox()[-1]["texto"]


def test_noite_envia_graficos_e_multiplas(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import incremento_diario as script
    from apostas.alertas import telegram_cliente as tc
    from apostas.relatorios import multiplas
    from apostas.utils import config as config_mod, db as db_mod
    monkeypatch.setattr(config_mod, "project_root", lambda: tmp_path)
    monkeypatch.setattr(ga, "project_root", lambda: tmp_path)
    monkeypatch.setattr(multiplas, "project_root", lambda: tmp_path)
    monkeypatch.setattr(config_mod, "db_path", lambda: tmp_path / "t.db")
    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.criar_schema()
    enviados = []
    monkeypatch.setattr(ga, "dicas_do_periodo", lambda inicio, fim, **kw: [
        _dica(datetime(2026, 11, 8, 20), "W", 0.9)])
    monkeypatch.setattr(ga, "enviar", lambda g: enviados.append(g.periodo))

    class _Cliente:
        def enviar(self, texto):
            enviados.append(texto)
    monkeypatch.setattr(tc, "cliente", lambda modo=None: _Cliente())
    sel = type("S", (), dict(jogo="A–B", event_id=7, jogador="X", player_id=1,
                             descricao="1+ remates", p=0.7, mercado="remates", linha=1,
                             sujeito_id=1))
    multiplas.registar("segura", [sel], datetime(2026, 11, 8, 15))
    try:
        script._graficos(datetime(2026, 11, 9, 0, 15))      # segunda: dia e semana
        script._graficos(datetime(2026, 11, 9, 0, 40))      # outra vez: nada repetido
    finally:
        db_mod._engine = None
        db_mod._SessionLocal = None
    assert enviados[:2] == ["dia", "semana"] and len(enviados) == 3
    assert "Multiplas de 08/11" in enviados[2] and "⏳" in enviados[2]
