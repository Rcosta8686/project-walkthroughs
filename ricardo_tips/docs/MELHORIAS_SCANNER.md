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

### Commit 2 — schema + CLV tracker + banca (FEITO 2026-10-15)

**Schema (3 tabelas novas, nao mexem nas existentes):**
- `OddsSnapshotPrelive` — snapshot de odds pre-match
  - `sofascore_event_id, mercado, linha, lado, odd, casa, momento, timestamp`
  - `momento ∈ {"abertura", "+1h", "pre_onzes", "pos_onzes", "fecho"}`
  - Usa sofascore_event_id directamente (evita matching com jogos.id)
- `EquipaPerfil` — perfil comportamental acumulado (ver secçao Perfil Equipa)
- `BancaRegisto` — snapshot diario de banca + stop-loss state

**Migracao SEM perder dados:**
- `scripts/migrar_m2_m13.py` corre `Base.metadata.create_all()` que so cria
  o que falta. Nao mexe nas 1927 sugestoes existentes.

**Modulo `apostas/modelos/clv_tracker.py`:**
- `gravar_snapshot(...)` — insere linha em OddsSnapshotPrelive
- `calcular_clv_sinal(evento, mercado, linha, lado, odd_apostada)` →
  `CLVResultado` com clv_vs_casa, clv_vs_justa, momento usado, casa.
- Logica: procura por ordem decrescente de proximidade ao kickoff
  (fecho > pos_onzes > pre_onzes > +1h > abertura).
- Tira margem automaticamente quando os dois lados do mercado estao
  disponiveis no mesmo momento/casa.

**Modulo `apostas/modelos/banca.py`:**
- `saldo_actual()` — ultimo BancaRegisto ou default 50 CHF
- `estado_banca_actual()` → `EstadoBanca` com perda_dia, perda_semana,
  apostas abertas, pode_apostar + motivo (stop-loss)
- `gravar_snapshot_hoje()` — upsert do registo diario
- Constantes: `BANCA_INICIAL_CHF_DEFAULT=50`, `STAKE_FLAT_CHF_DEFAULT=5`,
  `STOP_LOSS_DIA_PCT=0.05`, `STOP_LOSS_SEMANA_PCT=0.10`

**Script `scripts/banca_status.py`:**
- Mostra snapshot do estado banca + decide se podemos apostar
- Exit code 0 (ok) ou 2 (stop-loss) para encadear em .bat

**Testes:** +7 testes clv_tracker (totais 37/37).

### Commit 3 — perfil equipa + filtro M6 (FEITO 2026-10-15)

**Modulo `apostas/modelos/perfil_equipa.py`:**
- `calcular_perfil(team_id, nome, liga, n_jogos=10)` → `EquipaPerfil`:
  - Chama `/team/{id}/events/last/0` (sabemos funciona)
  - Para cada jogo chama `/event/{id}/statistics`
  - Agrega: baseline (remates/sot/cantos/cards/fouls/golos por 90)
  - Agrega: comportamento atras (remates_atras_90, cantos_atras_90,
    golos_em_atraso_media, virar_jogos_pct)
  - Agrega: carga_jogos_7d/14d
- `obter_perfil(team_id)` lê da BD
- `atualizar_perfil(team_id, nome, liga, ttl_horas=24)` upsert com cache

**Script `scripts/atualizar_perfis_equipa.py`:**
- Lista jogos agendados hoje → extrai team_ids → chama atualizar_perfil()
- Cache 24h: perfis frescos saltados
- Logs progresso + resumo final

**Modulo `apostas/alertas/filtros.py`:**
- `ContextoFiltro(ev, odd, mercado, liga, dados_recentes, momento, ...)`
- `passa_filtros_m6(ctx) → ResultadoFiltro(passou, falhados, avisos)`
- Avalia os 8 pontos do M6:
  - P1: EV >= min (3% default, 5% para odd < 1.50)
  - P2: nao em liga_fraca (PL, Ligue1, Brasileirao) nem mercado fraco (1x2)
  - P3: dados recentes + onze (aviso se nao-confirmado)
  - P4: momento certo (bloqueia "tarde")
  - P5: limite casa >= stake pretendida
  - P6: nao stop-loss + carteira pior perda <= 4%
  - P7: live latencia <= 10s + sem suspensao
  - P8: casa limitada so com EV >= 8%

**Testes:** +12 perfil_equipa + +17 filtros_m6 = 66 totais a passar.

### Commit 4 — integrar tudo no pre_live_diario (FEITO 2026-10-15)

**pre_live_diario.py agora:**
- **Banca gate (M13):** no inicio chama `banca.estado_banca_actual()`,
  mostra saldo + perda_dia, se `pode_apostar=False` marca
  `telegram_bloqueado_stop=True` e Telegram nao e enviado.
- **Perfil equipa integrado (M10/ask user):** por jogo chama
  `perfil_equipa.obter_perfil(home_id)` e `(away_id)`.
  - Se perfil `virar_jogos_pct >= 0.35` → `lam_golos += 0.08` (jogos
    mais abertos quando equipa com cauda ofensiva anda em desvantagem).
- **Filtro M6 por sinal:** cada sinal passa por `ContextoFiltro` +
  `passa_filtros_m6()`. Nao-passa fica so em "SINAIS REJEITADOS PELO M6
  (apenas contexto)" com motivo mostrado.
- **CLV snapshot (M2):** cada sinal aprovado chama
  `clv_tracker.gravar_snapshot(..., momento="pre_live_12h")` para marcar
  o momento de emissao. Odds reais entram no commit 5.
- **Stake flat (M13):** cada sinal mostra `stake 5 CHF`. Cabecalho
  Telegram mostra "Stake total: CHF X".
- **Formatacao:** linha extra "🔎 Equipa: vira 45%, 18 rem/90 atras,
  carga 3×7d" quando perfil tiver dados fortes.

**Caveats:**
- EV/odd no M6 sao placeholders (0.05/2.0) porque pre_live_diario ainda
  nao le odds pre-match. Isto bypassa P1/P5/P8 por agora. O commit 5
  vai ler odds reais do Sofascore/Odds API e calcular EV real.

**Testes:** 66/66 continuam a passar (nenhum teste velho partido).

### Commit 5 — odds pré-match reais no pre_live_diario (fase 2)

Integrar: `obter_odds_prematch_sofascore(event_id)` ou leitura
odds_correntes do The Odds API → calcular p_mercado via tirar_margem →
`blend(p_modelo, p_mercado, w=0.3)` → EV real → sinal.

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
