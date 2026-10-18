# Segundo Cérebro — arquitectura de equipas

> Ideia do Ricardo (2026-10-18): construir um "segundo cérebro" tipo Obsidian,
> com um cérebro principal (estratégia) e cérebros pequenos por equipa
> (histórico + comportamento). Para cada jogo, o cérebro principal consulta
> os dois cérebros de equipa envolvidos e decide.

## Visão

```
                    CÉREBRO PRINCIPAL
          (regras: Pizzola, Berry, Rabelo, Ricardo)
                         |
          ┌──────────────┴──────────────┐
          │                             │
   CÉREBRO EQUIPA A                CÉREBRO EQUIPA B
```

### Cérebro principal (estratégia)
Regras que não dependem da equipa específica:
- Blend p_modelo × p_mercado (Pinnacle como fair)
- Kelly ¼, stake flat 5 CHF
- Fade mediáticas (Caan Berry: ligas pouco monitoradas têm edge)
- Hard floor ⭐⭐⭐ no Telegram
- Stop-loss diário 5%, semanal 10%
- Blacklists conhecidas (Brasileirão, LaLiga 2…)
- 1X2 live NÃO envia (ROI −27%)
- Unders remates/cantos NÃO envia (falhavam 49-64%)

### Cérebros de equipa (micro-brains)
Para cada equipa das ligas prioritárias, uma "ficha" rica com:
- **Baseline**: remates/SOT/cantos/cards/fouls médios por 90'
- **Por parte**: 1T vs 2T (ex: Roma 2T +40% remates)
- **Comportamento por resultado**:
  - a ganhar: redução típica (ex: −15% SOT)
  - a perder 1: aumento típico
  - a perder 2+: aumento típico
- **Virar jogos**: % em que perdia 1T e ganhou/empatou final
- **Padrões específicos**: notas livres ("explode nos ultimos 15 min",
  "muitos cantos 2ª parte", "nunca sofre cartões cedo")
- **Fadiga/carga**:
  - jogos nos últimos 7d/14d
  - jogo europeu a meio da semana → 2ª parte −8%
  - viagem longa 14d

### Confronto (combinar 2 cérebros)
Dado (equipa_A, equipa_B, contexto do jogo):
1. Lê ficha de A e ficha de B
2. Aplica regras do cérebro principal
3. Emite:
   - Projecções minuto a minuto (remates, SOT, cantos)
   - Sinais com estrelas (⭐⭐⭐+)
   - Alertas específicos ("A joga Champions amanhã — fadiga")
   - Recomendação clara: aposta sim/não, mercado, linha, stake

## Ligas prioritárias

| País | Ligas |
|------|-------|
| Inglaterra | Premier League, Championship |
| Espanha | La Liga, La Liga 2 (Hypermotion) |
| Itália | Serie A, Serie B |
| Alemanha | Bundesliga, Bundesliga 2 |
| França | Ligue 1, Ligue 2 |
| Portugal | Liga Portugal, Liga Portugal 2 |
| EUA | MLS |
| Holanda (talvez) | Eredivisie, Eerste Divisie |

**Total**: ~15 ligas × ~20 equipas = ~300 equipas, 15-20 jogos cada =
**~5000 jogos** de histórico detalhado.

## Mapa: visão ↔ código

| Conceito | O que já existe | O que falta |
|----------|----------------|-------------|
| Cérebro principal | `apostas/alertas/analista.py` + `melhorias.py` | Rever regras com Pizzola/Berry explícitos |
| Cérebro equipa — storage | `EstatisticaEquipaJogo` ✓ | — |
| Cérebro equipa — ficha | `perfil_equipa.calcular_perfil_do_historico` ✓ | Report visual (markdown/HTML) por equipa |
| Confronto | `scanner_live_v2.py` combina parcialmente | Módulo `apostas/alertas/confronto.py` dedicado |
| Backfill histórico | `scripts/backfill_historico_equipas.py` ✓ | Lista oficial de ligas/equipas prioritárias |
| Visualização | Dashboard HTML existe para value bets | Dashboard por equipa (ficha visual) |

## Plano de execução (quando o Ricardo disser)

### Fase 1 — Preencher o histórico (crítico, mas lento)
Objectivo: cada equipa das ligas prioritárias tem ≥15 jogos em
`EstatisticaEquipaJogo`.

- [ ] 1.1 Criar lista oficial de `(liga, equipas)` em código
  (`apostas/dados_referencia/ligas_prioritarias.py`)
- [ ] 1.2 Script `scripts/popular_cerebros.py` que itera ligas e para
  cada equipa:
  - a. Resolve `sofascore_team_id` via `/search/team/{nome}`
  - b. Chama `historico_equipas.gravar_jogo_terminado()` para
    últimos 20 jogos
  - c. Progresso guardado — se cair, retoma onde parou
- [ ] 1.3 Rate-limit cuidadoso (Sofascore via flaresolverr aguenta)

### Fase 2 — Ficha rica por equipa
- [ ] 2.1 Função `apostas/relatorios/ficha_equipa.py:
  `gerar_ficha(team_id) -> dict` com secções:
  - baseline, por parte, por resultado, padrões, fadiga, notas
- [ ] 2.2 Renderer markdown: `ficha_markdown(team_id)` escreve
  `docs/equipas/{nome}.md` — Obsidian-friendly
- [ ] 2.3 Comando CLI: `python scripts/ver_ficha.py "AS Roma"`
- [ ] 2.4 (opcional) Renderer HTML interactivo

### Fase 3 — Módulo Confronto
- [ ] 3.1 `apostas/alertas/confronto.py:`
  `analisar_confronto(team_a_id, team_b_id, contexto) -> ConfrontoResultado`
  junta 2 fichas + regras principal + estado do jogo
- [ ] 3.2 Devolve mensagem rica tipo:
  ```
  🧠 ANÁLISE: AS Roma vs Genoa
  
  🏠 Roma:
    - 2T +42% remates (padrão forte)
    - A ganhar: −20% SOT (acalma)
    - Fadiga: jogou há 3 dias (Champions)
    - Virar jogos: 55% (muito alto)
  
  ✈ Genoa:
    - Perde 2+: +50% remates (desespero)
    - 2T +10% cantos
    - Sem fadiga
  
  📊 Projecções (modelo):
    SOT total: 10.5 (over 9.5 justo @1.85)
    Cantos:    8.5 (over 8.5 justo @1.95)
  
  🎯 Sinal sugerido:
    SOT over 10.5 @ 2.00+ (3 estrelas)
    Razão: Roma 2T + Genoa a perder = pressão + chasing
  ```
- [ ] 3.3 Scanner live chama `confronto.analisar_confronto()` em vez de
  `analista.gerar_sinais()` directo

### Fase 4 — UX: ler as fichas
- [ ] 4.1 Comando `/equipa Roma` no bot Telegram → envia ficha resumida
- [ ] 4.2 (opcional) Static site com ficha por equipa (GitHub Pages)

### Fase 5 — Auto-aprendizagem
- [ ] 5.1 Após cada jogo visto, cérebro de equipa auto-actualiza (já
  acontece via `historico_equipas.gravar_jogo_terminado` no resolver) ✓
- [ ] 5.2 Padrões não-numéricos: analista narrativo detecta anomalias
  ("Roma marcou 3 cantos nos últimos 15 min dos últimos 5 jogos") e
  escreve nas `notas` da `EquipaPerfil`

## Estado em 2026-10-18

**Já pronto:**
- Append-only `EstatisticaEquipaJogo` ✓
- `historico_equipas.gravar_jogo_terminado` ✓
- `perfil_equipa.calcular_perfil_do_historico` ✓
- Projecções 40'/55'/70' no Telegram ✓
- Resolver grava histórico ao resolver jogos ✓

**Falta (Fase 1-5 acima):**
- Lista oficial de ligas prioritárias
- Popular histórico em bulk
- Ficha visual por equipa
- Módulo Confronto dedicado
- Comando `/equipa` no bot

Quando o Ricardo disser "arranca", começo pela Fase 1.
