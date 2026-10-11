"""Diagnostico: mostra a resposta CRUA da Sofascore via flaresolverr.

Faz warmup na homepage primeiro, para apanhar cookies de sessao.
Testa 2 endpoints com o path diferente, para ver qual aceita pedidos:
  - api.sofascore.com/api/v1/...  (domain da API)
  - www.sofascore.com/api/v1/...  (via dominio principal, pode ter auth mais branda)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import _flaresolverr  # noqa: E402

_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _preview(txt: str, chars: int = 500) -> str:
    txt = (txt or "").strip()
    if len(txt) <= chars:
        return txt
    return txt[:chars] + f"\n... [cortado, total {len(txt)} caracteres]"


def _testar(url: str, warmup: str | None) -> None:
    print(f"\n{'=' * 80}")
    print(f"GET {url}")
    if warmup:
        print(f"  (com warmup previo em {warmup})")
    print("=" * 80)
    try:
        resp = _flaresolverr.get(url, headers=_HEADERS, warmup_url=warmup)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: {exc}")
        return
    print(f"HTTP status: {resp.status}")
    print(f"URL final:   {resp.url}")
    print(f"\n--- body (500 chars) ---")
    print(_preview(resp.body_text))
    try:
        data = resp.json()
    except Exception:
        return
    if isinstance(data, dict):
        if "error" in data:
            print(f"\n⚠ ERRO Sofascore: {data['error']}")
        elif "events" in data:
            print(f"\n✓ {len(data['events'])} events devolvidos")
            if data["events"]:
                e0 = data["events"][0]
                print(f"  primeiro: {e0.get('homeTeam',{}).get('name')} vs "
                      f"{e0.get('awayTeam',{}).get('name')} "
                      f"({e0.get('tournament',{}).get('name')})")


def main() -> None:
    hoje = datetime.utcnow().strftime("%Y-%m-%d")
    warmup = "https://www.sofascore.com/"

    print(">>> Teste 1: endpoint api.sofascore.com COM warmup")
    _testar("https://api.sofascore.com/api/v1/sport/football/events/live", warmup=warmup)

    print("\n>>> Teste 2: mesmo endpoint via www.sofascore.com (sem warmup — mesma sessao)")
    _testar("https://www.sofascore.com/api/v1/sport/football/events/live", warmup=None)

    print("\n>>> Teste 3: scheduled-events hoje via api.sofascore.com")
    _testar(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{hoje}", warmup=None)


if __name__ == "__main__":
    main()
