"""Reparacao do sugestoes_live_v2.csv com linhas escritas em formatos antigos."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from apostas.relatorios import csv_live as cl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

V1, V2, V3, V4, V5 = cl.FORMATOS.values()


def _sinal(**kw) -> dict:
    base = {
        "alert_id": "4", "ts_scan": "2026-11-04T17:17:31", "event_id": "16391603",
        "liga": "UEFA Europa League", "home": "Stade Rennais", "away": "Olympiacos FC",
        "minuto": "45", "marcador": "0-0", "mercado": "remates", "lado": "over",
        "linha": "16.5", "casa": "ANALISTA", "odd": "1.45", "prob_modelo": "0.72",
        "p_mercado": "", "p_final": "", "verificar": "False", "ev": "", "estrelas": "4",
        "xg_casa": "0.61", "xg_fora": "0.0", "shots_on_target_c": "2",
        "shots_on_target_f": "1", "corners_c": "3", "corners_f": "2", "yellows_c": "1",
        "yellows_f": "2", "resultado": "", "lucro": "", "resolvido_em": "",
    }
    base.update(kw)
    return base


def _valores(formato: list[str], d: dict) -> list[str]:
    return [d.get(c, "") for c in formato]


def _escrever(path: Path, cabecalho: list[str], linhas: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(cabecalho)
        w.writerows(linhas)


def _ler(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def test_formatos_sao_os_do_historico_git():
    import scanner_live_v2 as sc
    assert V5 == cl.COLUNAS_ACTUAIS == sc.CSV_COLUNAS
    assert len(V1) == 24 and V1[:6] == ["ts_scan", "liga", "home", "away", "minuto", "marcador"]
    assert V2[1] == "event_id" and "estrelas" not in V2
    assert V3[14] == "estrelas" and "p_mercado" not in V3
    assert V4 == V5[1:]


def test_ficheiro_antigo_com_linhas_novas_fica_alinhado(tmp_path):
    """O caso do PC: cabecalho v4 (14/10), linhas novas escritas em v5."""
    f = tmp_path / "sug.csv"
    antiga = _sinal(ts_scan="2026-10-15T20:00:00", minuto="60", resultado="W", lucro="0.45",
                    resolvido_em="2026-10-15T22:00:00")
    hoje1 = _sinal(alert_id="4")
    hoje2 = _sinal(alert_id="4", ts_scan="2026-11-04T22:15:31", event_id="16938938",
                   home="Borussia Dortmund", away="Real Betis", minuto="51", marcador="1-0",
                   linha="20.5")
    # o resolver reescreve pelo cabecalho: a linha v5 perde o ultimo campo (vazio)
    _escrever(f, V4, [_valores(V4, antiga), _valores(V5, hoje1)[:29], _valores(V5, hoje2)[:29]])

    diag = cl.realinhar(f)
    assert diag.reparado and not diag.cabecalho_actual
    assert diag.desalinhadas == 2 and diag.ids_repetidos == 1
    rows = _ler(f)
    assert list(rows[0].keys()) == cl.COLUNAS_ACTUAIS
    assert [r["alert_id"] for r in rows] == ["1", "2", "3"]
    assert rows[0]["resultado"] == "W" and rows[0]["ts_scan"] == "2026-10-15T20:00:00"
    # as linhas de hoje voltam a estar por resolver, com tudo no sitio
    assert rows[1]["resultado"] == "" and rows[1]["event_id"] == "16391603"
    assert rows[2]["home"] == "Borussia Dortmund" and rows[2]["mercado"] == "remates"
    assert rows[2]["yellows_f"] == "2" and rows[2]["estrelas"] == "4"
    assert diag.backup is not None and diag.backup.exists()


def test_todos_os_formatos_misturados(tmp_path):
    f = tmp_path / "sug.csv"
    d1 = _sinal(ts_scan="2026-10-11T15:00:00", minuto="30", estrelas="", verificar="")
    d2 = _sinal(ts_scan="2026-10-11T19:00:00", minuto="31", resultado="L", lucro="-1")
    d3 = _sinal(ts_scan="2026-10-13T19:00:00", minuto="32", estrelas="5")
    d4 = _sinal(ts_scan="2026-10-15T19:00:00", minuto="33", p_mercado="0.66", p_final="0.7")
    d5 = _sinal(ts_scan="2026-11-01T19:00:00", minuto="34", alert_id="7")
    v1_cru = _valores(V1, d1)[:21]  # o scanner v1 escrevia 21 campos
    _escrever(f, V2, [v1_cru, _valores(V2, d2), _valores(V3, d3), _valores(V4, d4),
                      _valores(V5, d5)])
    diag = cl.realinhar(f)
    assert diag.irreconheciveis == 0
    assert sorted(diag.por_formato.values()) == [1, 1, 1, 1, 1]
    rows = {r["minuto"]: r for r in _ler(f)}
    assert rows["30"]["event_id"] == "" and rows["30"]["yellows_f"] == "2"
    assert rows["31"]["resultado"] == "L" and rows["31"]["lucro"] == "-1"
    assert rows["32"]["estrelas"] == "5" and rows["32"]["xg_casa"] == "0.61"
    assert rows["33"]["p_mercado"] == "0.66" and rows["33"]["verificar"] == "False"
    assert rows["34"]["corners_f"] == "2"


def test_ficheiro_em_ordem_nao_e_tocado(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V5, [_valores(V5, _sinal(alert_id="1")),
                      _valores(V5, _sinal(alert_id="2", minuto="50"))])
    antes = f.read_bytes()
    diag = cl.realinhar(f)
    assert not diag.precisa_reparar and not diag.reparado
    assert f.read_bytes() == antes
    assert not list(tmp_path.glob("*.backup-*"))


def test_so_diagnostico_nao_altera(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal())[:29]])
    antes = f.read_bytes()
    diag = cl.realinhar(f, aplicar=False)
    assert diag.precisa_reparar and not diag.reparado
    assert f.read_bytes() == antes


def test_lixo_demais_recusa_e_nao_mexe(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal())[:29], ["isto", "nao", "e", "uma", "linha"]])
    antes = f.read_bytes()
    diag = cl.realinhar(f)
    assert diag.recusado and not diag.reparado
    assert f.read_bytes() == antes


def test_duplicados_ficam_um_preferindo_o_resolvido(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V5, [_valores(V5, _sinal(alert_id="1")),
                      _valores(V5, _sinal(alert_id="2", resultado="W", lucro="0.45"))])
    diag = cl.realinhar(f)
    rows = _ler(f)
    assert diag.duplicadas == 1 and len(rows) == 1 and rows[0]["resultado"] == "W"


def test_registo_de_enviados_perde_os_alertas_e_guarda_as_projeccoes(tmp_path):
    from datetime import datetime

    from apostas.relatorios import alertas_enviados as ae
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal())[:29]])
    reg = tmp_path / "env.csv"
    ae.registar(4, datetime(2026, 11, 4, 23), reg)
    ae.registar(tipo="projeccao", ts=datetime(2026, 11, 4, 23), path=reg,
                event_id=9, mercado="cantos", lado="over", linha=8.5, odd=1.5)
    cl.realinhar(f, registo_enviados=reg)
    assert [r["tipo"] for r in ae.carregar(reg)] == ["projeccao"]


def test_resolver_volta_a_ver_as_linhas_reparadas(tmp_path, monkeypatch):
    """Antes da reparacao o resolver achava 0 pendentes (lia lixo em 'resultado')."""
    import resolver_sugestoes_live as rsl
    from datetime import datetime
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal())[:29]])
    monkeypatch.setattr(rsl, "CSV_PATH", f)
    monkeypatch.setattr(rsl, "_stats_seguro", lambda *a: None)
    vistos: list[int] = []
    monkeypatch.setattr(rsl, "_puxar_resultado_final", lambda eid: vistos.append(eid) or None)
    rsl.resolver(desde=datetime(2026, 11, 4))
    assert vistos == []                      # desalinhado: invisivel
    cl.realinhar(f)
    rsl.resolver(desde=datetime(2026, 11, 4))
    assert vistos == [16391603]


def _linha_csv(valores: list[str]) -> str:
    import io
    buf = io.StringIO()
    csv.writer(buf).writerow(valores)
    return buf.getvalue()


def test_escritas_misturadas_de_dois_scanners_ficam_de_parte(tmp_path):
    """Dois scanners a escrever ao mesmo tempo: linhas coladas e cortadas a meio."""
    f = tmp_path / "sug.csv"
    boas = [_valores(V5, _sinal(alert_id=str(i), minuto=str(i)))[:29] for i in range(1, 41)]
    texto = _linha_csv(V4) + "".join(_linha_csv(v) for v in boas)
    a = _linha_csv(_valores(V5, _sinal(minuto="88")))
    b = _linha_csv(_valores(V5, _sinal(minuto="89")))
    corte = len(a) // 2
    texto += a[:corte] + b + a[corte:]       # linha colada + resto cortado
    f.write_text(texto, encoding="utf-8")

    diag = cl.realinhar(f)
    assert diag.irreconheciveis == 2 and diag.reparado
    assert diag.ficheiro_irreconheciveis is not None
    assert len(_ler(f)) == 40
    with diag.ficheiro_irreconheciveis.open(encoding="utf-8") as fp:
        assert len(list(csv.reader(fp))) == 3   # cabecalho + 2 linhas postas de parte
    txt = diag.texto()
    assert "irreconheciveis: 2 (4.8%)" in txt and "ex: " in txt and "campos" in txt


def test_um_campo_estranho_e_tolerado(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal(estrelas="7"))[:29]])
    diag = cl.realinhar(f)
    assert diag.irreconheciveis == 0
    assert _ler(f)[0]["estrelas"] == "7" and _ler(f)[0]["mercado"] == "remates"


def test_mercado_desconhecido_em_minusculas_e_aceite(tmp_path):
    f = tmp_path / "sug.csv"
    _escrever(f, V4, [_valores(V5, _sinal(mercado="golos_casa", lado="over"))[:29]])
    diag = cl.realinhar(f)
    assert diag.irreconheciveis == 0 and _ler(f)[0]["mercado"] == "golos_casa"
