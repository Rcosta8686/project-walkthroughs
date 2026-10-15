# MELHORIAS SCANNER — plano, estado, decisoes

Documento de trabalho para implementar as 14 melhorias de
`melhorias-scanner.md` ao scanner Ricardo Tips.

**Decisoes do utilizador (2026-10-15):**
- Banca: **50 CHF**
- Stake: **5 CHF flat** por sugestao (nao-Kelly ate banca crescer)
- Casas: Suiça (nome a definir — ignorar M14 contas por agora)
- Documenta tudo em cada commit (este ficheiro + commit message detalhado)
- Base de dados **por equipa** para acumular comportamento (ver secçao Perfil de Equipa)

---

## Estado M1-M14

Audit feito em 2026-10-15:

| # | Melhoria | Antes | Depois (planeado) |
|---|---|---|---|
| M1 | Prob. mercado como base | 🟡 em parte (no_vig + blend) | ✅ completar com odds pré-match no pre_live |
| M2 | CLV (abertura/fecho) | ❌ | ✅ **COMMIT 1** — funcoes + snapshot odds |
| M3 | Blend p_modelo × p_mercado | ✅ feito | — |
| M4 | 1 modelo golos → tudo | 🟡 em parte | ✅ grelha Dixon-Coles com AH, 1ªP |
| M5 | Ordenar por lucro CHF | ❌ | ✅ `lucro_esperado_chf` + ranking |
| M6 | Filtro final 8 pontos | 🟡 2/8 | ✅ `passa_filtros_m6()` |
| M7 | Info estrutural | ❌ | fase 2 (castigos, rotação, incentivos) |
| M8 | Notícias → P(joga) | ❌ | fase 2 (precisa agentes de leitura web) |
| M9 | Janela onzes | ❌ | fase 2 |
| M10 | Mercados jogadores | 🟡 ratings equipa | ✅ ajustes λ_remates/cards por jogador |
| M11 | Teses em simulação | ❌ | fase 2 |
| M12 | Ao vivo com segurança | 🟡 | ✅ latência medida + derivados λ_restante |
| M13 | Banca + stop-loss | ❌ | ✅ **COMMIT 1** — stake_flat 5 CHF + stop-loss |
| M14 | CLV móvel + relatório | ❌ | fase 2 |

Prioridade do doc:
1. M2 + M1 (vantagem real)
2. M6 + M13 (protege a banca)
3. M3 + M4
4. M5 + M14
5. M7 + M8
6. M9 + M10
7. M11
8. M12

---

## Perfil de equipa (ask especifico do utilizador)

Objectivo: para **cada equipa** acumular padroes de comportamento a partir
dos ultimos 10-20 jogos, actualizados a cada 24h:

**Padroes a guardar:**
- `caudal_ofensivo_atras`: remates/90 quando a equipa esta a perder
- `remates_1a_parte_media`, `remates_2a_parte_media`
- `cantos_1a_parte`, `cantos_2a_parte`
- `cruzamentos_por_90` (quando disponivel no Sofascore)
- `cards_media`, `fouls_media`
- `golos_em_atraso_media` (golos marcados quando a perder)
- `virar_jogos_pct`: % de jogos onde virou (viagou perdendo → acabou ganhando)
- `carga_jogos_7d`, `carga_jogos_14d`
- `viagens_longas_7d` (quando possivel detectar via UCL/UEL)
- `lesoes_ativas` (via lineups + alinhamentos)
- `rating_jogadores_media` (do Sofascore)

**Como usar:**
Quando o pre_live diario encontrar Juventus vs equipa fraca:
- Puxa perfil Juventus → sabe que `caudal_ofensivo_atras = 18 remates/90`
- Se Juventus esperado perder cedo → ajusta `λ_remates` em +X%
- Se perfil mostra `virar_jogos_pct > 40%` → não apostar contra Juventus em 1X2 live
- Se `cantos_2a_parte` do Juventus >> media liga → sinal Over cantos 2ªP

**Implementação:**
- Tabela `EquipaPerfil` (team_id, data_atualizacao, campos acima)
- Script `scripts/atualizar_perfis_equipa.py` corre à meia-noite
- Função `obter_perfil(team_id) -> EquipaPerfil`
- Integração no `pre_live_diario._analisar_jogo()` e `scanner_live_v2`

**COMMIT 3** — depois do CLV e banca estarem.

---

## COMMITS PLANEADOS

### Commit 1 — M2 (CLV) + M13 (banca simples) + funcoes base

**Ficheiros:**
- `apostas/modelos/melhorias.py` (NOVO) — todas as funcoes core:
  - `tirar_margem(odds) -> (probs, margem)` [M1]
  - `ev(prob, odd)` [M1]
  - `kelly_quarto(prob, odd, max_frac)` [M5/M13]
  - `odd_americana_para_decimal(am)` [util]
  - `clv(odd_apostada, odd_fecho_justa)` [M2]
  - `prob_over(linha, lam)` [M4]
  - `grelha_dixon_coles(lam_c, lam_f, rho)` [M4]
  - `probs_1x2_da_grelha`, `prob_over_da_grelha`, `prob_btts_da_grelha` [M4]
  - `ev_ah(grelha, favorito, linha, odd)` [M4]
  - `inverter_linha_total(p_over, golos_restantes)` [M12]
  - `stake_flat(5.0)` [M13 user-specific]
  - `stake_kelly_cap(prob, odd, banca, round=5)` [M13 futuro]
  - `lucro_esperado_chf(prob, odd, stake)` [M5]
  - `passa_stop_loss(perda_dia, perda_semana, banca)` [M13]
- `testes/test_melhorias.py` (NOVO) — 30 testes com valores de referencia do doc
- `docs/MELHORIAS_SCANNER.md` (ESTE)

**Testes:** 30/30 passam, todos com valores exactos do doc.

### Commit 2 — snapshot de odds pré-match (M2 operacional)

**Nova tabela** `odds_snapshots`:
- `id, jogo_id, mercado, lado, linha, odd, casa, timestamp, momento`
- `momento ∈ {"abertura", "+1h", "pre_onzes", "pos_onzes", "fecho"}`

**Script** `scripts/snapshot_odds_prelive.py`:
- Corre varias vezes por dia via Task Scheduler
- Abertura: 24h antes do kickoff
- +1h, pré-onzes (90min antes), pós-onzes (45min antes), fecho (5min antes)

**Scanner live** passa a guardar snapshot a cada ciclo.

**Script** `scripts/mostrar_clv.py`:
- Para cada sugestão passada: `clv = odd_apostada / odd_fecho_justa - 1`
- Agrupa por mercado/liga/momento
- Relatório mostra CLV **antes** do lucro

### Commit 3 — perfil de equipa

Ver secçao "Perfil de equipa" acima.

### Commit 4 — M6 filtro final 8 pontos

`apostas/alertas/filtros.py` com `passa_filtros_m6(sinal, contexto)`.

### Commit 5 — integrar tudo no pre_live_diario e scanner_live_v2

Usar `grelha_dixon_coles` nos sinais de golos; aplicar `blend()` com p_mercado;
`stake_flat()` no Telegram; `passa_filtros_m6()` como gate final.

---

## Teses (M11) — tracker para o futuro

Quando chegarmos à fase 2, criar `docs/TESES.md` com tabela:

| id | descricao | porque mercado erra | difícil copiar? | estado | CLV sim | ROI real |
|---|---|---|---|---|---|---|
| T1 | Viés público Benfica/Porto | adeptos sobrevalorizam | sim | ideia | - | - |
| T2 | Caudal ofensivo Juventus-tipo quando perde | mercado nao reage | parcial | ideia | - | - |
| T3 | Correcao exagerada pos-xG alto | mercado over-reage | sim | ideia | - | - |

---

## Historico de decisoes

- **2026-10-15**: audit M1-M14 completo. User escolheu "faz tudo" com banca 50 CHF e stake 5 CHF flat.
- **2026-10-15 (commit 1)**: funcoes base + 30 testes com valores do doc.
