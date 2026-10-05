"""Ingestão do football-data.co.uk.

Fonte gratuita de resultados históricos + odds de fecho de várias casas.
Documentação das colunas: https://www.football-data.co.uk/notes.txt

Em modo `desenvolvimento` lê um CSV sintético reprodutível (ver `_mocks_fd.py`).
Em modo `producao` descarrega o CSV real e põe em cache local.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import select

from apostas.ingestao import _mocks_fd
from apostas.ingestao import aliases as _aliases
from apostas.utils.config import get_env, project_root
from apostas.utils.db import abrir_sessao
from apostas.utils.logger import get_logger
from apostas.utils.schema import Equipa, EquipaAlias, Jogo, Liga, OddsFecho

log = get_logger(__name__)

# Mapeamento de ligas → códigos usados pelo football-data.co.uk
CODIGOS_LIGA = {
    "Premier League": "E0",
    "La Liga": "SP1",
    "Serie A": "I1",
    "Bundesliga": "D1",
    "Ligue 1": "F1",
    "Liga Portugal": "P1",
}

_BASE_URL = "https://www.football-data.co.uk/mmz4281"


@dataclass
class ResultadoIngestaoFD:
    jogos_criados: int = 0
    jogos_existentes: int = 0
    odds_criadas: int = 0
    odds_existentes: int = 0
    equipas_criadas: int = 0
    erros: list[str] = field(default_factory=list)


def _codigo_epoca(ano: int) -> str:
    """2023 → '2324' (época 2023/24)."""
    return f"{ano % 100:02d}{(ano + 1) % 100:02d}"


def _caminho_cache(codigo_liga: str, epoca: int) -> Path:
    return project_root() / "dados" / "raw" / "football_data" / f"{_codigo_epoca(epoca)}_{codigo_liga}.csv"


def _obter_csv(codigo_liga: str, epoca: int, modo: str) -> str:
    """Devolve o conteúdo do CSV (mock em dev, download em producao)."""
    if modo == "desenvolvimento":
        log.debug("[mock] CSV football-data %s %d", codigo_liga, epoca)
        return _mocks_fd.gerar_csv(codigo_liga, epoca)

    cache = _caminho_cache(codigo_liga, epoca)
    if cache.exists():
        log.debug("A ler cache: %s", cache)
        return cache.read_text(encoding="utf-8")

    url = f"{_BASE_URL}/{_codigo_epoca(epoca)}/{codigo_liga}.csv"
    log.info("A descarregar %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(resp.text, encoding="utf-8")
    return resp.text


def _ler_dataframe(csv_texto: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(csv_texto))
    # Garante colunas obrigatórias
    for col in ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"):
        if col not in df.columns:
            raise ValueError(f"CSV sem coluna obrigatória: {col}")
    # Remove linhas vazias (football-data costuma ter rodapé em branco)
    df = df.dropna(subset=["HomeTeam", "AwayTeam"]).copy()
    df["Date"] = df["Date"].apply(_parse_data)
    return df


def _parse_data(valor: str) -> datetime:
    """Aceita DD/MM/YYYY e DD/MM/YY."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(valor, fmt)
        except ValueError:
            continue
    raise ValueError(f"Formato de data desconhecido: {valor!r}")


def _get_or_create_equipa(s, nome: str, liga: Liga, resultado: ResultadoIngestaoFD) -> Equipa:
    """Procura por nome exato, por alias registado, por mapa estático; cria se não existir."""
    # 1) Nome exato (= equipa Sportmonks com este nome, ou equipa football-data já criada)
    equipa = s.scalar(
        select(Equipa).where(Equipa.nome == nome, Equipa.liga_id == liga.id)
    )
    if equipa is not None:
        return equipa

    # 2) Alias registado na BD (fonte football_data)
    alias = s.scalar(
        select(EquipaAlias).where(
            EquipaAlias.alias == nome,
            EquipaAlias.fonte == _aliases.FONTE_FOOTBALL_DATA,
        )
    )
    if alias is not None:
        return s.get(Equipa, alias.equipa_id)

    # 3) Alias no mapa estático (equipa Sportmonks sem alias ainda registado)
    canonico = _aliases.nome_canonico_de(nome, _aliases.FONTE_FOOTBALL_DATA)
    if canonico is not None:
        equipa = s.scalar(
            select(Equipa).where(Equipa.nome == canonico, Equipa.liga_id == liga.id)
        )
        if equipa is not None:
            s.add(EquipaAlias(equipa_id=equipa.id, alias=nome, fonte=_aliases.FONTE_FOOTBALL_DATA))
            return equipa

    # 4) Nova equipa (sem sportmonks_id — reconciliação futura via alias)
    equipa = Equipa(nome=nome, liga_id=liga.id, sportmonks_id=None)
    s.add(equipa)
    s.flush()
    resultado.equipas_criadas += 1
    log.debug("Criada equipa %s na liga %s (sem match Sportmonks)", nome, liga.nome)
    return equipa


_COLUNAS_1X2 = {
    "B365H": "B365", "B365D": "B365", "B365A": "B365",
    "BWH": "BW", "BWD": "BW", "BWA": "BW",
    "PSH": "PS", "PSD": "PS", "PSA": "PS",
    "AvgCH": "Avg_Closing", "AvgCD": "Avg_Closing", "AvgCA": "Avg_Closing",
}
_LADO_1X2 = {"H": "casa", "D": "empate", "A": "fora"}

_COLUNAS_OU25 = {
    "B365>2.5": ("B365", "over", 2.5),
    "B365<2.5": ("B365", "under", 2.5),
    "AvgC>2.5": ("Avg_Closing", "over", 2.5),
    "AvgC<2.5": ("Avg_Closing", "under", 2.5),
}


def _inserir_odds(s, jogo: Jogo, linha_df, resultado: ResultadoIngestaoFD) -> None:
    """Insere odds de fecho disponíveis para 1X2 e Over/Under 2.5."""
    # 1X2
    for col, casa in _COLUNAS_1X2.items():
        if col not in linha_df.index or pd.isna(linha_df[col]):
            continue
        sufixo = col[-1]
        if sufixo not in _LADO_1X2:
            continue
        _upsert_odd(s, jogo.id, "1x2", None, _LADO_1X2[sufixo], float(linha_df[col]), casa, resultado)

    # Over/Under 2.5
    for col, (casa, lado, linha) in _COLUNAS_OU25.items():
        if col not in linha_df.index or pd.isna(linha_df[col]):
            continue
        _upsert_odd(s, jogo.id, "golos", linha, lado, float(linha_df[col]), casa, resultado)


def _upsert_odd(
    s,
    jogo_id: int,
    mercado: str,
    linha: float | None,
    lado: str,
    odd: float,
    casa: str,
    resultado: ResultadoIngestaoFD,
) -> None:
    existente = s.scalar(
        select(OddsFecho).where(
            OddsFecho.jogo_id == jogo_id,
            OddsFecho.mercado == mercado,
            OddsFecho.linha == linha,
            OddsFecho.lado == lado,
            OddsFecho.casa_de_apostas == casa,
        )
    )
    if existente is not None:
        resultado.odds_existentes += 1
        return
    s.add(
        OddsFecho(
            jogo_id=jogo_id, mercado=mercado, linha=linha, lado=lado,
            odd=odd, casa_de_apostas=casa,
        )
    )
    resultado.odds_criadas += 1


def sincronizar(
    epocas: list[int] | None = None,
    modo: str | None = None,
) -> ResultadoIngestaoFD:
    """Puxa CSVs para cada liga configurada em cada época e persiste."""
    modo = modo or (get_env("MODO", "desenvolvimento") or "desenvolvimento").lower()
    epocas = epocas or [2023, 2024]
    resultado = ResultadoIngestaoFD()

    with abrir_sessao() as s:
        for nome_liga, codigo in CODIGOS_LIGA.items():
            liga = s.scalar(select(Liga).where(Liga.nome == nome_liga))
            if liga is None:
                resultado.erros.append(f"Liga '{nome_liga}' não existe na BD — correr ingestão de ligas primeiro.")
                continue
            for epoca in epocas:
                try:
                    csv_texto = _obter_csv(codigo, epoca, modo)
                    df = _ler_dataframe(csv_texto)
                except Exception as exc:  # noqa: BLE001
                    resultado.erros.append(f"{nome_liga} {epoca}: {exc}")
                    continue
                _processar_dataframe(s, df, liga, epoca, resultado)

    log.info(
        "football-data: %d jogos (%d novos), %d odds (%d novas), %d equipas novas, %d erros",
        resultado.jogos_criados + resultado.jogos_existentes,
        resultado.jogos_criados,
        resultado.odds_criadas + resultado.odds_existentes,
        resultado.odds_criadas,
        resultado.equipas_criadas,
        len(resultado.erros),
    )
    return resultado


def _processar_dataframe(
    s, df: pd.DataFrame, liga: Liga, epoca: int, resultado: ResultadoIngestaoFD
) -> None:
    for _, row in df.iterrows():
        casa = _get_or_create_equipa(s, row["HomeTeam"], liga, resultado)
        fora = _get_or_create_equipa(s, row["AwayTeam"], liga, resultado)

        jogo = _get_or_create_jogo(s, liga, epoca, row, casa, fora, resultado)
        _inserir_odds(s, jogo, row, resultado)


def _get_or_create_jogo(
    s, liga: Liga, epoca: int, row, casa: Equipa, fora: Equipa,
    resultado: ResultadoIngestaoFD,
) -> Jogo:
    """Prefere ligar odds a um Jogo já existente (vindo da Sportmonks) para
    o mesmo (liga, equipas, mesmo dia). Caso contrário cria novo."""
    data_jogo = row["Date"]

    # 1) Procurar jogo já existente para a mesma dupla no mesmo dia (±36h de folga)
    inicio = datetime(data_jogo.year, data_jogo.month, data_jogo.day)
    fim = inicio + timedelta(days=1, hours=12)
    existente = s.scalar(
        select(Jogo).where(
            Jogo.liga_id == liga.id,
            Jogo.casa_id == casa.id,
            Jogo.fora_id == fora.id,
            Jogo.data_utc >= inicio - timedelta(hours=12),
            Jogo.data_utc <= fim,
        )
    )
    if existente is not None:
        resultado.jogos_existentes += 1
        return existente

    # 2) Chave determinística (fallback para jogos só do football-data)
    chave = _chave_jogo_fd(liga.id, data_jogo, casa.id, fora.id)
    pelo_externo = s.scalar(select(Jogo).where(Jogo.id_externo == chave))
    if pelo_externo is not None:
        resultado.jogos_existentes += 1
        return pelo_externo

    jogo = Jogo(
        id_externo=chave,
        liga_id=liga.id,
        epoca=epoca,
        data_utc=data_jogo,
        casa_id=casa.id,
        fora_id=fora.id,
        golos_casa=int(row["FTHG"]) if pd.notna(row.get("FTHG")) else None,
        golos_fora=int(row["FTAG"]) if pd.notna(row.get("FTAG")) else None,
        estado="terminado" if pd.notna(row.get("FTHG")) else "agendado",
    )
    s.add(jogo)
    s.flush()
    resultado.jogos_criados += 1
    return jogo


def _chave_jogo_fd(liga_id: int, data: datetime, casa_id: int, fora_id: int) -> int:
    """Chave inteira determinística para identificar jogos vindos do football-data.

    Formato: LLDDMMYYCCCFFFF onde LL=liga_id (2 digitos), DDMMYY=data, CCC=casa_id, FFFF=fora_id.
    """
    return int(
        f"{liga_id:02d}{data.strftime('%d%m%y')}{casa_id:04d}{fora_id:04d}"
    )
