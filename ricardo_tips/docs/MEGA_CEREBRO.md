# Mega-cérebro — leitura de jogo e síntese final

O mega-cérebro é a camada acima dos mini-cérebros (fichas por equipa).
Lê a ficha de cada equipa, o estado live, as vozes dos treinadores e
devolve **1 linha** com o mercado que está a ganhar peso.

Objetivo final (o que o Ricardo descreveu na noite da UCL de 20/Out/2026):

> "ler o treinador nos mini-cérebros, atitude em campo se está a ser
> refletida, necessidade da equipa, poder de bons jogadores para
> decidir e atitude. Tudo com sentido crítico, até ao ponto em que
> entra o mega-cérebro e diz 'a jogar assim, esta equipa vai criar
> mais e vamos apostar em cantos, remates ou o que seja'."

## O que já está (v1)

`apostas/alertas/mega_cerebro.py` — 4 heurísticas simples que correm
contra o estado live + ficha das 2 equipas:

| # | Dispara quando                                 | Devolve         |
|---|------------------------------------------------|-----------------|
| 1 | amarelos ≥ 3 totais ao 55'+ e 1 lado ≥ 1.4× | over amarelos   |
| 2 | ambas as equipas ≥ 1.25× SoT normal ao 35'+ | over remates    |
| 3 | equipa a perder com cantos ≥ 1.4× e SoT 0.3 abaixo | over cantos (pressão lateral) |
| 4 | equipa dominante a ganhar, cantos < 0.8× ao 65'+ | under cantos (controlar) |

Silencia quando nenhuma heurística bate — melhor silêncio que ruído.

A linha sai na mensagem de PROJECAO como:

```
🧠 Mega: 🚩 over cantos — equipa a perder com 1.6x cantos vs normal — pressao pelas pontas
```

## Fase A — Contexto de competição (próxima)

**Problema:** uma noite de UCL em fase de liguilha tem jogos onde cada
golo vale milhões. PSV e Estugarda jogaram para segurar porque já
estavam confortáveis; outros atacaram porque precisavam. O mega-cérebro
hoje não sabe nada disso.

**O que falta:**

1. `apostas/segundo_cerebro/contexto_competicao.py`
   - Dado um `event_id`/`liga`, puxa:
     - **Fase da competição** (grupo, liguilha, playoff, final)
     - **Standings** (classificação atual, pontos para o próximo lugar que importa)
     - **Peso do jogo**:
       - UCL grupo com 2 jogos a jogar: alto
       - UCL playoff: muito alto
       - Liga doméstica meio-tabela sem descida/europa: baixo
       - Último jogo da época com europa/descida: muito alto
   - Fonte: Sofascore `/tournament/{id}/standings` (gratis via flaresolverr) + regras hardcoded por competição

2. Heurística nova no mega-cérebro:
   - "ambas as equipas precisam de pontos" + "nenhuma dominou a 1ª parte"
     → sinal pro over golos / SoT (jogo abre)
   - "equipa a ganhar e já qualificada" + "a perder precisa de 3 pts"
     → bias pro chasing da que perde + under cantos da que ganha
   - Linha extra: `⚖ Peso: UCL grupo · casa precisa 3pts, fora qualificada`

## Fase B — Validação de atitude (próxima +1)

**Problema:** a ficha diz X (equipa X joga a 60% posse, 15 remates/90).
Hoje às 55' está com 48% posse e 7 remates. Isto é um **sinal** — pode
ser lesão de peça chave, pode ser respeito ao adversário, pode ser
estratégia nova. O mega-cérebro deve dizê-lo.

**O que falta:**

1. Função `atitude_vs_ficha(ficha, live_now, minuto)` que compara:
   - posse actual vs baseline
   - sot/90' projectado vs baseline
   - cantos/90' projectado vs baseline
   - cards/90' projectado vs baseline
2. Classifica em 3 estados:
   - **"como habitual"** (todas as métricas ±15% do normal)
   - **"a jogar diferente — mais passivo"** (ritmo ≤ 70% em ≥ 2 métricas)
   - **"a jogar diferente — mais ativo"** (ritmo ≥ 130% em ≥ 2 métricas)
3. Linha: `⚖ VfB: a jogar como habitual · Atleti: mais passivo que normal`

## Fase C — Player power (próxima +2)

**Problema:** o mega-cérebro ignora o impacto individual. Equipa pode
estar a jogar bem, mas se o Vinícius sai lesionado ao 30', muda tudo.

**O que falta:**

1. Já temos `EstatisticaJogadorJogo` por jogo (minutos, ratings).
2. Função `estrelas_em_campo(home_id, away_id, lineup_live)` que:
   - Puxa os top 3 jogadores por rating médio de cada equipa
   - Cross-reference com lineup actual (Sofascore)
   - Devolve: "Estrelas em campo: 2/3 casa, 1/3 fora (Vinícius lesionado)"
3. Alerta de substituição: se uma estrela sai, flag.

## Fase D — Loop de aprendizagem (próxima +3)

**Problema:** o mega-cérebro é hoje estático. Ricardo quer "aprender uma
coisa nova a cada dia".

**O que falta:**

1. Já temos `hypothesis_ledger` (M21) para registar hipóteses.
2. Cada vez que o mega-cérebro emite uma leitura ("over cantos"), grava:
   - estado completo no momento (minuto, score, stats, perfis)
   - mercado+direcção emitido
3. Depois do jogo, cruza com o resultado real e marca a hipótese
   confirmada/falhada.
4. **Backtest da heurística**: cada semana, correr o ledger e dizer:
   "Heurística 3 (pressão lateral) acertou 68% nos últimos 30 dias →
   manter. Heurística 1 (amarelos) acertou 42% → rever."
5. Relatório semanal expande com "mega-cérebro: taxa de acerto por
   heurística".

## Decisões no caminho

- **Heurísticas vs ML:** começar com heurísticas transparentes e
  revisíveis. ML só depois de ter dataset grande (>1k observações por
  heurística confirmada).
- **Nunca autosubmeter aposta.** Mega-cérebro **sinaliza**, Ricardo
  decide. Mesmo que a confiança seja 3/3.
- **Silêncio é a opção segura.** Melhor não dizer nada do que ruído.
- **Explicabilidade sempre.** Cada linha do mega tem de dizer *porquê*
  — nunca "aposta cantos" sem a razão em texto plano.
