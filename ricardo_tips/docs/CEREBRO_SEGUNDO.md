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

### Fase 6 — Micro-cérebros por JOGADOR (ideia 2026-10-18)

Objectivo: cada jogador relevante tem o seu próprio micro-cérebro.
Permite confrontos tácticos: "defesa agressivo A vs driblador B →
amarelo provável para A".

**Novas tabelas:**
- `EstatisticaJogadorJogo` — append-only, 1 linha por (jogador, jogo):
  - IDs: `sofascore_player_id`, `sofascore_event_id`, `sofascore_team_id`
  - Jogou: `minutos`, `foi_titular`, `posicao` (ex: `LB`, `RW`, `AM`)
  - Produção: `remates`, `sot`, `golos`, `assistencias`, `grandes_oportunidades`
  - Passes: `passes`, `passes_pct`, `passes_chave`
  - Defesa: `tackles`, `interceptacoes`, `desarmes`, `duels_won`, `duels_lost`
  - Disciplina: `fouls_committed`, `fouls_drawn`, `cards_amarelos`,
    `cards_vermelhos`, `minuto_amarelo` (quando foi apontado)
  - Rating Sofascore: `rating` (float 0-10)
- `FormacaoEquipaJogo` — 1 linha por (equipa, jogo):
  - `formacao` (`"4-3-3"`), `11_inicial_json`, `substituicoes_json`,
    `estadio`, `arbitro`

**Analise derivada (perfil jogador):**
- `agressividade` = fouls_committed/90 nos últimos 10 jogos
- `tendencia_amarelo` = cards_am/90
- `papel_tactico` = posição moda + rating médio
- `forma_actual` = média de ratings últimos 5 jogos
- `remates_90`, `golos_90`, `tackles_90`

**Confronto táctico (match-up):**
Para cada par (atacante A, defesa B) que se vão enfrentar:
- Se A tem alto `drible_sucesso%` e B tem alta `agressividade`:
  - probabilidade de B tirar amarelo sobe (sinal: "amarelo B ≥ linha")
- Se jogador C já tem amarelo neste jogo (minuto X) e é agressivo:
  - probabilidade de 2º amarelo sobe no fim

**Alertas automáticos:**
- "⚠ Casemiro já amarelado minuto 23, probabilidade vermelho 2T +180%"
- "📍 Vinicius Jr vs Trippier: Trippier tem 2.3 faltas/90, cartão 0.4/90"

### Fase 7 — Casa vs Fora split (ideia 2026-10-18)

- [ ] 7.1 Perfil equipa separa stats quando `era_casa=True` vs `False`.
  Já temos a flag na tabela, só falta separar no agregado.
- [ ] 7.2 Novo campo `_casa` e `_fora` para cada métrica
  (ex: `remates_casa_90`, `remates_fora_90`)

### Fase 8 — Estádios (ideia 2026-10-18)

Alguns estádios são "fortaleza" (visitantes jogam pior). Precisamos de:
- [ ] 8.1 Tabela `Estadio` (nome, cidade, lotação, altitude?)
- [ ] 8.2 Ligar `EstatisticaEquipaJogo.estadio_id`
- [ ] 8.3 Para cada estádio: performance média dos visitantes vs
  baseline mundial. Flag "fortaleza" se visitantes marcam ≤ média - 0.5

### Fase 9 — Carga semanal rica (ideia 2026-10-18)

Já temos `dias_desde_jogo_anterior`. Precisamos de:
- [ ] 9.1 `jogos_ultimos_7d` (quantos)
- [ ] 9.2 `jogos_ultimos_14d`
- [ ] 9.3 `viagens_ultimos_14d` (deslocações para fora)
- [ ] 9.4 `jogo_europeu_entre_meio` (bool)

**Factor fadiga composto:**
```
fator = 1.0
  - 0.08 se 2+ jogos nos últimos 7d
  - 0.05 se viagem longa (voo >2h) na última semana
  - 0.03 adicional por cada dia de descanso <4
```
Aplicado em projecções 2ª parte.

### Fase 10 — Jogadores lesionados / suspensos

Já temos `Lesao` no schema (não usamos). Expandir:
- [ ] 10.1 Ingerir lista oficial de lesões/suspensos da Sofascore
- [ ] 10.2 Confronto considera: "Roma sem Dybala + Pellegrini → ataque −30%"
- [ ] 10.3 Alerta "ONZE SURPRESA" quando onze inicial muda substancialmente
  vs 5 jogos anteriores

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
