"""Testes dos módulos de infraestrutura (config, logger, db, schema)."""

from __future__ import annotations

from sqlalchemy import inspect

from apostas.utils import config as config_mod
from apostas.utils import db as db_mod


def test_config_tem_chaves_obrigatorias():
    cfg = config_mod.load_config()
    for chave in (
        "ligas",
        "mercados",
        "modelo",
        "backtest",
        "value_betting",
        "alertas",
        "timezone",
        "dados",
        "log",
    ):
        assert chave in cfg, f"Chave '{chave}' em falta em config.yaml"


def test_config_tem_cinco_ligas():
    cfg = config_mod.load_config()
    assert len(cfg["ligas"]) == 5
    nomes = {liga["nome"] for liga in cfg["ligas"]}
    assert nomes == {
        "Premier League",
        "La Liga",
        "Serie A",
        "Bundesliga",
        "Ligue 1",
    }


def test_mercado_golos_tem_tres_linhas():
    cfg = config_mod.load_config()
    golos = cfg["mercados"]["golos"]
    assert golos["ativo"] is True
    assert set(golos["linhas"]) == {1.5, 2.5, 3.5}
    assert set(golos["lados"]) == {"over", "under"}


def test_project_root_contem_config():
    root = config_mod.project_root()
    assert (root / "config.yaml").exists()


def test_criar_schema_cria_todas_as_tabelas(tmp_path, monkeypatch):
    bd_temp = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd_temp)
    db_mod.reset_engine()

    db_mod.criar_schema()

    assert bd_temp.exists()
    inspector = inspect(db_mod.get_engine())
    tabelas = set(inspector.get_table_names())
    esperadas = {
        "ligas",
        "equipas",
        "jogos",
        "estatisticas_jogo",
        "odds_fecho",
        "odds_correntes",
        "alinhamentos",
        "lesoes",
        "sugestoes",
        "apostas_reais",
    }
    assert esperadas.issubset(tabelas), f"Tabelas em falta: {esperadas - tabelas}"


def test_sessao_commit(tmp_path, monkeypatch):
    bd_temp = tmp_path / "test.db"
    monkeypatch.setattr(config_mod, "db_path", lambda: bd_temp)
    db_mod.reset_engine()
    db_mod.criar_schema()

    from apostas.utils.schema import Liga

    with db_mod.abrir_sessao() as s:
        s.add(Liga(nome="Teste", pais="PT", api_football_id=999))

    with db_mod.abrir_sessao() as s:
        encontrado = s.query(Liga).filter_by(nome="Teste").one()
        assert encontrado.pais == "PT"
