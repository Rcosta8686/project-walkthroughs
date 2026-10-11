"""Diagnostico: mostra a resposta CRUA da Sofascore via flaresolverr.

Util quando o scanner diz 0 jogos live e queremos ver se:
  - A ligacao HTTP funcionou (status)
  - O body e JSON ou HTML (Cloudflare interstitial)
  - O JSON tem events ou foi devolvido vazio
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apostas.ingestao import _flaresolverr  # noqa: E402


def _preview(txt: str, chars: int = 600) -> str:
    txt = (txt or "").strip()
    if len(txt) <= chars:
        return txt
    return txt[:chars] + f"\n... [cortado, total {len(txt)} caracteres]"


def _testar(url: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"GET {url}")
    print("=" * 80)
    try:
        resp = _flaresolverr.get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao chamar flaresolverr: {exc}")
        return
    print(f"HTTP status: {resp.status}")
    print(f"URL final:   {resp.url}")
    print(f"Content-Type: {resp.headers.get('Content-Type') or resp.headers.get('content-type')}")
    print(f"\n--- primeiros 600 chars do body ---")
    print(_preview(resp.body_text))
    print(f"\n--- tentativa de parse JSON ---")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"NAO E JSON valido: {exc}")
        return
    if isinstance(data, dict):
        print(f"Dict com keys: {list(data.keys())[:20]}")
        events = data.get("events")
        if events is not None:
            print(f"events: {len(events)} items")
            if events:
                e0 = events[0]
                print(f"primeiro evento: id={e0.get('id')}, "
                      f"home={e0.get('homeTeam',{}).get('name')}, "
                      f"away={e0.get('awayTeam',{}).get('name')}, "
                      f"liga={e0.get('tournament',{}).get('name')}")
    elif isinstance(data, list):
        print(f"Lista com {len(data)} items")


def main() -> None:
    _testar("https://api.sofascore.com/api/v1/sport/football/events/live")
    # Fallback: eventos agendados para hoje — serve para confirmar que a API
    # responde em geral, caso o /live esteja a devolver vazio sempre.
    from datetime import datetime
    hoje = datetime.utcnow().strftime("%Y-%m-%d")
    _testar(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{hoje}")


if __name__ == "__main__":
    main()
