"""Script: atualizar_dados.py

Puxa dados mais recentes das ligas configuradas:
  1. Bootstrap das ligas a partir de config.yaml (sem API externa)
  2. Jogos + odds + remates/cantos/cartões via football-data.co.uk (grátis)
  3. xG via Understat (grátis, 5 top ligas europeias)

Grava tudo em dados/ricardo_tips.db (SQLite).

Uso:
    python scripts/atualizar_dados.py

Correr diariamente. No Windows: Task Scheduler com trigger "Diariamente 07:00".

O modo de operação (desenvolvimento com mocks vs. producao real) é lido da
variável de ambiente MODO no .env — ver .env.example.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import football_data_uk, ligas_equipas, odds_api, understat  # noqa: E402
from apostas.utils.config import get_env, load_config  # noqa: E402
from apostas.utils.db import criar_schema  # noqa: E402
from apostas.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def _epocas_a_puxar(n_anteriores: int = 2) -> list[int]:
    """Devolve [época_atual - n_anteriores, ..., época_atual]."""
    hoje = datetime.utcnow()
    epoca_atual = hoje.year if hoje.month >= 7 else hoje.year - 1
    return list(range(epoca_atual - n_anteriores, epoca_atual + 1))


def main() -> None:
    modo = get_env("MODO", "desenvolvimento")
    cfg = load_config()
    n_prev = int(cfg.get("modelo", {}).get("epocas_treino", 3)) - 1
    epocas = _epocas_a_puxar(n_anteriores=n_prev)
    log.info("A correr em modo: %s · épocas a puxar: %s", modo, epocas)

    criar_schema()

    # 1) Bootstrap de ligas a partir do config
    res_lf = ligas_equipas.sincronizar()
    print(
        f"✓ Ligas: {res_lf.ligas_criadas} novas, "
        f"{res_lf.ligas_existentes} já existentes."
    )

    # 2) Jogos + odds + remates/cantos/cartões via football-data.co.uk
    res_fd = football_data_uk.sincronizar(epocas=epocas)
    print(
        f"✓ Jogos (football-data): {res_fd.jogos_criados} novos, "
        f"{res_fd.jogos_existentes} já existentes."
    )
    print(
        f"✓ Odds (football-data): {res_fd.odds_criadas} novas, "
        f"{res_fd.odds_existentes} já existentes."
    )
    print(
        f"✓ Estatísticas (football-data): {res_fd.stats_criados} criadas, "
        f"{res_fd.stats_atualizados} atualizadas."
    )
    if res_fd.equipas_criadas:
        print(
            f"  (criadas {res_fd.equipas_criadas} equipas novas pelo football-data)"
        )
    for erro in res_fd.erros:
        print(f"⚠ football-data: {erro}")

    # 3) xG via Understat (grátis, Top-5 ligas) — anota EstatisticasJogo
    res_us = understat.sincronizar(epocas=epocas)
    print(
        f"✓ xG (Understat): {res_us.jogos_atualizados} jogos atualizados, "
        f"{res_us.jogos_sem_match} sem match, "
        f"{len(res_us.equipas_sem_match)} equipas sem match."
    )
    if res_us.equipas_sem_match:
        print(f"  Equipas Understat sem match: {sorted(res_us.equipas_sem_match)[:10]}")
    for erro in res_us.erros:
        print(f"⚠ Understat: {erro}")

    # 4) Odds pré-jogo em tempo real via The Odds API.
    # Em dev mode (MODO=desenvolvimento) usa mocks e não precisa de chave.
    # Em produção precisa de ODDS_API_KEY no .env.
    if modo == "desenvolvimento" or get_env("ODDS_API_KEY"):
        res_oa = odds_api.sincronizar()
        print(
            f"✓ Odds correntes (The Odds API): {res_oa.odds_criadas} novas, "
            f"{res_oa.odds_atualizadas} atualizadas, "
            f"{res_oa.jogos_criados} jogos novos criados, "
            f"{res_oa.jogos_sem_match} sem match."
        )
        if res_oa.credits_restantes is not None:
            print(f"  Credits Odds API restantes este mês: {res_oa.credits_restantes}")
        if res_oa.equipas_sem_match:
            print(f"  Equipas Odds API sem match: {sorted(res_oa.equipas_sem_match)[:10]}")
        for erro in res_oa.erros:
            print(f"⚠ Odds API: {erro}")
    else:
        print("  (The Odds API: desactivado — define ODDS_API_KEY no .env para activar)")


if __name__ == "__main__":
    main()
