# Arquitetura — Ricardo Tips

Documento técnico que explica cada peça do sistema e **porque** foi escolhida assim. Escrito para alguém que não é programador — se algo não for claro, abre um issue.

---

## 1. Objetivo

Encontrar apostas de futebol com **vantagem real** face ao mercado, apoiando-se em dados, não em palpites. A ferramenta vigia, não prevê — a decisão de apostar é sempre humana.

---

## 2. Princípios (não negociáveis)

1. **Vigiar, não prever.** O modelo não tenta adivinhar resultados; identifica discrepâncias entre a sua estimativa e o mercado.
2. **Remates e cantos como sinal, não golos.** Golos são ruidosos (uma equipa pode ter 20 remates e não marcar). Remates e cantos são mais estáveis e preditivos.
3. **Comparar sempre com o mercado.** Só há aposta quando o modelo e as odds discordam o suficiente para criar valor (EV positivo).
4. **Backtest antes de dinheiro real.** Nenhuma fase avança sem resultados históricos consistentes.
5. **Humano decide, código executa.** Nunca auto-apostar.

Base académica: Edward Wheatcroft, *"A profitable model for predicting the over/under market in football"*, *International Journal of Forecasting*, 2020 (10 ligas europeias, 12 anos, ROI médio +0,8%).

---

## 3. Visão geral do fluxo

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Sportmonks    │     │football-data.co │     │  22bet (manual) │
│  (stats, line- │     │ (odds de fecho  │     │ (odds correntes │
│   ups, lesões) │     │  históricas)    │     │ introduzidas    │
└────────┬────────┘     └────────┬────────┘     │  por ti)        │
         │                       │              └────────┬────────┘
         └───────────┬───────────┘                       │
                     ▼                                   │
            ┌─────────────────┐                          │
            │  Ingestão       │                          │
            │ (apostas.inges  │                          │
            │  tao)           │                          │
            └────────┬────────┘                          │
                     ▼                                   │
            ┌─────────────────┐                          │
            │  SQLite local   │                          │
            │  (dados/*.db)   │                          │
            └────────┬────────┘                          │
                     ▼                                   │
         ┌───────────────────────┐                       │
         │  Modelo GAP ratings   │                       │
         │ (apostas.modelos)     │                       │
         │ → P(golos ≥ N)        │                       │
         └───────────┬───────────┘                       │
                     ▼                                   │
         ┌───────────────────────┐                       │
         │  Motor de value       │◀──────────────────────┘
         │  betting              │
         │ EV = p·odds − 1       │
         └───────────┬───────────┘
                     ▼
         ┌───────────────────────┐
         │ Backtest (histórico)  │   Alertas (hoje/amanhã)
         │ ou                    │   via Telegram
         └───────────────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │  Dashboard HTML       │
         └───────────────────────┘
```

---

## 4. Componentes

### 4.1. Ingestão (`apostas/ingestao/`)

**Responsabilidades**
- Buscar à **Sportmonks Football API v3**: fixtures, line-ups, lesões, remates, cantos, cartões, substituições. Plano com as 6 ligas (Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal).
- Buscar ao **football-data.co.uk** (gratuito): resultados e odds de fecho históricas de várias casas (B365, PS, PH, VC, …) — usamos para o backtest.
- Gravar tudo normalizado em SQLite (`dados/ricardo_tips.db`).

**Esquema de BD (resumo)**
- `ligas` (id, nome, pais, slug)
- `equipas` (id, nome, liga_id)
- `jogos` (id, liga_id, data_utc, casa_id, fora_id, golos_casa, golos_fora, estado)
- `estatisticas_jogo` (jogo_id, equipa_id, remates, remates_a_baliza, cantos, posse, cartoes_amarelos, cartoes_vermelhos)
- `odds_fecho` (jogo_id, mercado, linha, lado, odd, casa_de_apostas)
- `odds_correntes` (jogo_id, mercado, linha, lado, odd, casa_de_apostas, timestamp)
- `alinhamentos` (jogo_id, equipa_id, jogadores_json, formacao)
- `lesoes` (equipa_id, jogador, tipo, data_estimada_regresso)
- `sugestoes` (id, jogo_id, mercado, linha, lado, prob_modelo, odd, ev, enviado_em)
- `apostas_reais` (id, sugestao_id, stake, odd_executada, resultado, lucro)

**Idempotência**
Cada rotina de ingestão pode ser corrida várias vezes no mesmo dia sem duplicar registos (chave única `(jogo_id, …)` em cada tabela).

### 4.2. Modelo GAP ratings (`apostas/modelos/`)

**Ideia (linguagem simples)**
- Cada equipa tem dois números: **GAP ofensivo** (quanto pressiona) e **GAP defensivo** (quanto se deixa pressionar).
- Esses números não são calculados a partir dos golos marcados, mas sim a partir de **remates + cantos** — métricas que são mais fiáveis como "intenção" ofensiva.
- Para cada jogo, combinamos as 4 ratings das duas equipas e estimamos a **probabilidade de haver ≥ N golos**, para N ∈ {0,5; 1,5; 2,5; 3,5}.
- A partir dessa probabilidade, derivamos a probabilidade dos mercados Over N e Under N.

**Treino**
- Dados: últimas **5 épocas** das 5 ligas (~18.000 jogos).
- Peso: **decaimento exponencial** com meia-vida de 1 ano — jogos de há 5 épocas pesam ~1/32 do que pesam jogos recentes. Isto respeita a tua intuição (mudanças de treinador, táticas) sem perder volume estatístico.
- Validação: **walk-forward** — para cada semana testada, o modelo só vê jogos anteriores a essa semana. Zero fugas de informação do futuro.

**Contexto por jogo (não entra no treino)**
Antes de cada jogo analisado, o motor olha para os **últimos 10 jogos** de cada equipa e ajusta as GAP ratings com:
- Lesões de jogadores-chave (API-Football dá a lista).
- Suspensões por acumulação de cartões.
- Forma recente (média móvel das últimas 10 performances).
- Panel de contexto humano: links para notícias recentes (Google News) — tu lês e decides se o modelo está a ignorar algo subjetivo (balneário, treinador em final de ciclo).

### 4.3. Motor de value betting (`apostas/modelos/value.py`)

Para cada jogo e cada linha de mercado:

```
EV = (prob_modelo × odd_mercado) − 1
```

- Se `EV > ev_minimo` (configurável, default 3%): candidato a sugestão.
- Antes de sugerir, remove-se a margem (overround) das odds da casa para medir a prob implícita "justa" — se a prob do modelo for muito próxima da justa, o EV aparente da casa pode ser ilusão.
- Referência: odds da 22bet. Como vais introduzir manualmente (opção B que escolheste), o motor pede-te as odds no Telegram quando houver um jogo candidato.

### 4.4. Backtest (`apostas/backtest/`)

**Walk-forward semanal**
1. Define o conjunto de jogos de uma semana específica (ex.: 2024-W12).
2. Treina o modelo apenas com jogos anteriores a essa semana.
3. Para cada jogo da semana, calcula P(over/under N) e compara com odds de fecho históricas.
4. Regista as apostas que teriam sido feitas (EV > limiar) e o seu resultado.
5. Avança uma semana e repete.

**Métricas reportadas**
- ROI (Return on Investment) por época e acumulado
- Nº de apostas, hit rate, odds média
- Max drawdown (maior queda consecutiva)
- CLV (closing line value) — média de quanto a nossa odd batia a odd de fecho
- Kelly fracionado teórico (0,25 Kelly) para dimensionar stakes no futuro
- Gráfico de bankroll ao longo do tempo

**Stake**: 1 unidade por aposta (flat). Decidido contigo — resultados comparáveis entre mercados e com o paper.

### 4.5. Alertas Telegram (`apostas/alertas/`)

**Modo:** interativo (python-telegram-bot em polling).

**Arranque automático no Windows:** script registado no Agendador de Tarefas com o gatilho "Ao iniciar sessão", garantindo que o bot está sempre ligado quando o teu PC está ligado.

**Fluxo quando aparece um jogo candidato**:
1. Script `analisar_jogos_hoje.py` encontra um jogo com EV positivo.
2. Envia-te mensagem no Telegram:
   > ⚡ **Candidato**: Arsenal vs Chelsea (hoje, 20h)  
   > Mercado: Over 2,5 golos  
   > Prob modelo: 61%  
   > Linha de referência (Pinnacle): 1,85  
   > **Envia a odd da 22bet para confirmar EV.**
3. Respondes com a odd (ex.: `1.80`).
4. Bot recalcula EV com a odd real:
   > EV: +4,2%. **Sugestão: Over 2,5 @ 1.80.**  
   > Para registar a aposta: `/registar <id> <stake>`
5. Se registares, o resultado é tracked automaticamente após o jogo e entra no ROI mensal.

**Timing** (decidido contigo): **~2h antes do kickoff** (opção B). Scheduler verifica de 15 em 15 minutos se há jogos a começar em 2–2,25h.

### 4.6. Dashboard (`apostas/dashboard/`)

HTML estático gerado por Jinja2. Abres com duplo-clique no `index.html`.

Secções:
- **Hoje**: jogos candidatos, com link para a página detalhada
- **Backtest**: resultados do último backtest corrido (ROI por época, curva de bankroll)
- **Minhas apostas**: histórico de apostas reais registadas vs sugestões não aceites
- **Diagnóstico**: distribuição de EV, hit rate por faixa de EV, CLV ao longo do tempo

---

## 5. Fases de desenvolvimento

### Fase 1 — Backtest Over/Under golos (atual)
Objetivo: ter um ROI positivo em walk-forward nas últimas 2 épocas, com ≥ 300 apostas.

Entregas:
- [ ] Ingestão a funcionar (API-Football + football-data.co.uk)
- [ ] Modelo GAP implementado e treinado
- [ ] Backtest walk-forward com relatório HTML
- [ ] Dashboard mínimo (secção backtest)

### Fase 2 — Alertas live para golos
Objetivo: receber sugestões reais e começar a registar com stake simulada (1€).

Entregas:
- [ ] Script `analisar_jogos_hoje.py`
- [ ] Bot Telegram com comandos `/hoje`, `/stats`, `/registar`, `/pausar`, `/retomar`, `/jogo <ID>`, `/amanha`
- [ ] Registo manual de odds da 22bet via Telegram

### Fase 3 — Mercado de cantos
Replicar a mesma estrutura para over/under cantos. Mesma GAP ratings, outra linha.

### Fase 4 — Mercado de cartões
Modelo distinto: cartões dependem muito do árbitro. Adicionar feature "árbitro" e re-validar o backtest.

### Fase 5 — Mercado 1X2
Mercado mais eficiente. Só avançar se as fases anteriores derem resultados positivos consistentes durante ≥ 3 meses em dinheiro real.

### Fase 6 — Dinheiro real
Teto de perda definido antes. Stakes mínimos. Comparação sistemática real vs simulado.

### Fase 7 (futura, condicional) — Expansão para outros desportos

**Pré-condição obrigatória:** ≥ 3 meses de ROI positivo em dinheiro real no futebol, com ≥ 200 apostas registadas. Sem isto, não começar.

**Ordem recomendada:**

1. **Ténis** — mais simples. 1v1, sem empates, Elo com rating por superfície (terra, duro, relva). Dados grátis: Jeff Sackmann (GitHub) + tennis-data.co.uk. Mercados principais: vencedor, vencedor de set, total de games, handicap. Edge real existe em circuito Challenger e primeiras rondas de Grand Slam. Esforço estimado: ~2 semanas.

2. **NBA** — mais complexo e mercado mais eficiente. Dados grátis: `nba_api` (Python). Modelo tipo SRS/Elo com ajuste de ritmo e descanso. O edge real está nos **player props** (pontos/ressaltos/assistências por jogador), não nos mercados principais. Verificar primeiro se a 22bet os oferece com liquidez. Esforço estimado: ~3 semanas.

**O que se reaproveita** (quase toda a infraestrutura): config, logger, BD, bot Telegram, motor de EV, backtest walk-forward, dashboard.

**O que é por desporto** (reescrito): fonte de dados, modelo probabilístico, lista de mercados, features relevantes.

A ingestão e os modelos ficam em subpacotes separados: `apostas/ingestao/futebol/`, `apostas/ingestao/tenis/`, `apostas/ingestao/nba/` — e análogo em `apostas/modelos/`. A tabela `jogos` ganha a coluna `desporto` quando chegarmos a esse ponto.

---

## 6. Como adicionar um mercado novo

1. Adicionar entrada em `config.yaml` com `ativo: true` e `linhas: [...]`.
2. Em `apostas/modelos/`, criar um módulo `<mercado>.py` que exporta uma função `prob(jogo, estado_bd) -> dict`.
3. Em `apostas/backtest/`, adicionar o mercado ao switch do runner.
4. Em `apostas/alertas/mensagens.py`, adicionar o template de mensagem.
5. Correr `python scripts/correr_backtest.py --mercado <novo>` e verificar ROI antes de ativar alertas.

---

## 7. Riscos e limitações conhecidos

- **22bet pode limitar a conta** se detetar padrão de ganhador consistente. Alternativas: Pinnacle (sharp book), Betfair exchange.
- **Suíça bloqueia bookmakers sem licença suíça.** Já discutido — ficámos pela 22bet mesmo com risco operacional.
- **Odds introduzidas manualmente** (opção B) significam que se não estiveres disponível 2h antes do jogo, perdes essa oportunidade. Aceitável para uso pessoal.
- **Edge de 0,8%** exige **volume** para se materializar. Em 50 apostas, qualquer ROI entre -5% e +5% é compatível com um modelo que tem edge. É preciso disciplina e paciência.
- **Mudanças de regra** (ex.: VAR em 2019, mudanças de regras de cantos) podem fazer o modelo descalibrar. Monitorizar CLV é a melhor defesa.

---

## 8. Dependências externas

| Serviço | Para quê | Custo |
|---------|----------|-------|
| Sportmonks Football API v3 | Stats em tempo real, line-ups, lesões, 6 ligas | depende do plano (~€15–€50/mês) |
| football-data.co.uk | Odds de fecho históricas | Grátis |
| Telegram Bot API | Alertas | Grátis |
| 22bet | Casa de apostas efetiva | Comissão implícita no spread |

Total mensal: dentro do orçamento de €50/mês acordado.

---

## 9. Estrutura de código

```
ricardo_tips/
├── apostas/                   # pacote Python
│   ├── ingestao/              # buscar e normalizar dados
│   ├── modelos/               # GAP ratings, value betting
│   ├── backtest/              # walk-forward
│   ├── alertas/               # bot Telegram
│   ├── dashboard/             # Jinja2 → HTML
│   └── utils/                 # funções partilhadas
├── scripts/                   # comandos para o utilizador correr
├── dados/                     # SQLite (ignorado pelo git)
├── testes/                    # pytest
└── config.yaml                # parâmetros
```

---

## 10. Decisões técnicas registadas

| Decisão | Alternativa considerada | Porque esta |
|---------|------------------------|-------------|
| Python 3.11 | R, Julia | Ecossistema de scraping/ML/Telegram maduro; mais fácil para iniciante |
| SQLite | PostgreSQL | Zero config, chega para a escala; pode trocar-se depois |
| Walk-forward | k-fold | k-fold permite look-ahead no tempo; walk-forward não |
| Flat staking | Kelly | Kelly é melhor em teoria mas sensível a overestimação de edge; começamos simples |
| 2h antes do jogo | De manhã | Odds mais estáveis perto do kickoff; movimentação final já visível |
| Decaimento exponencial 1 ano | Janela fixa 2 épocas | Respeita a tua intuição sem perder dados |
| Jinja2 para dashboard | Streamlit/Dash | Zero servidor, abre com duplo-clique |
| python-telegram-bot v20+ | Telethon | API mais simples; só precisamos de bot, não de user account |
