# Ricardo Tips

Ferramenta pessoal de apoio a apostas de futebol baseada em dados — não em palpites nem previsão genérica de resultados. Vigia condições específicas em várias ligas em simultâneo e avisa quando aparece uma aposta com vantagem real face ao mercado.

**Importante:** a decisão final de apostar é sempre tua. A ferramenta sugere, tu decides.

---

## O que é

- Lê dados de jogos passados das 5 principais ligas europeias (Premier League, La Liga, Serie A, Bundesliga, Ligue 1).
- Constrói classificações de "pressão ofensiva/defensiva" a partir de **remates e cantos** (não dos golos marcados).
- Compara as probabilidades do modelo com as odds do mercado (referência: 22bet).
- Quando o valor esperado (EV) de uma aposta é positivo o suficiente, envia-te um alerta no Telegram.
- Mantém um registo das sugestões vs. apostas reais que fizeste, para medires o ROI mês a mês.

A fundamentação está no documento `ARQUITETURA.md`. Lê-o quando quiseres perceber *porquê* cada peça está onde está.

---

## Fases do projeto (ordem obrigatória)

1. **Backtest** com dados históricos — sem apostar dinheiro nenhum.
2. **Teto de perda** definido antes de arrancar com dinheiro real.
3. **Apostas de valor mínimo**, só para confirmar o sistema fora da amostra.
4. **Meses de resultados consistentes** antes de pensar em escalar.

Estamos atualmente na **Fase 1 (backtest)**.

---

## Instalação (Windows)

### 1. Instalar Python

1. Vai a <https://www.python.org/downloads/windows/> e descarrega o Python 3.11 ou mais recente.
2. No instalador, **marca a caixa "Add Python to PATH"** antes de clicar em Install.
3. Depois de instalar, abre o **Prompt de Comando** (tecla Windows + escreve `cmd`) e corre:
   ```
   python --version
   ```
   Deve aparecer `Python 3.11.x` ou superior.

### 2. Clonar / abrir o projeto

Se já tens o repositório no PC, abre o Prompt de Comando dentro da pasta `project-walkthroughs\ricardo_tips`.

### 3. Criar um ambiente virtual (recomendado)

Dentro da pasta `ricardo_tips`:
```
python -m venv venv
venv\Scripts\activate
```

Vais ver `(venv)` no início da linha — significa que estás no ambiente isolado.

### 4. Instalar as dependências

```
pip install -r requirements.txt
```

Isto demora 2–5 minutos.

### 5. Configurar as chaves

Copia o ficheiro `.env.example` para `.env`:
```
copy .env.example .env
```

Abre `.env` no Bloco de Notas e preenche:
- **API_FOOTBALL_KEY** — regista-te em <https://rapidapi.com/api-sports/api/api-football> e subscreve o plano Pro (~€29/mês). Copia a tua chave para aqui.
- **TELEGRAM_BOT_TOKEN** — fala com <https://t.me/BotFather> no Telegram, escreve `/newbot`, segue as instruções, copia o token.
- **TELEGRAM_CHAT_ID** — fala com <https://t.me/userinfobot>, copia o número que ele te manda.

Guarda e fecha. **Nunca partilhes este ficheiro.**

---

## Como usar no dia-a-dia

Todos os comandos correm na pasta `ricardo_tips` com o ambiente ativo (`venv\Scripts\activate`).

### Atualizar os dados (uma vez por dia)
```
python scripts\atualizar_dados.py
```
Puxa jogos recentes, alinhamentos, lesões e odds. Demora 1–3 minutos.

### Correr o backtest
```
python scripts\correr_backtest.py --mercado golos --linha 2.5
```
Produz um relatório HTML em `apostas\dashboard\output\backtest.html` com ROI, drawdown e número de apostas.

### Analisar jogos de hoje
```
python scripts\analisar_jogos_hoje.py
```
Analisa os jogos das próximas 24h, calcula EV, envia alertas Telegram para os que superam o limiar definido em `config.yaml`.

**Automatizar no Windows:** usa o **Task Scheduler** para correr `atualizar_dados.py` todos os dias às 7h e `analisar_jogos_hoje.py` de hora a hora. Instruções detalhadas em `ARQUITETURA.md`.

---

## Comandos do bot Telegram

Depois de arrancar o bot (`python scripts\bot_telegram.py` em segundo plano), estes comandos funcionam na conversa com ele:

| Comando | O que faz |
|---------|-----------|
| `/hoje` | Jogos de hoje com EV positivo |
| `/amanha` | Jogos de amanhã com EV positivo |
| `/stats` | ROI do mês, nº de sugestões, hit rate |
| `/registar` | Registar que apostaste efetivamente num jogo |
| `/pausar` | Suspender alertas automáticos |
| `/retomar` | Voltar a ativar alertas |
| `/jogo <ID>` | Análise detalhada de um jogo específico |

---

## Dashboard

Abre `apostas\dashboard\output\index.html` no browser. Mostra:
- Resumo dos últimos 30 dias
- Lista de sugestões por jogo (ao vivo)
- Histórico de apostas registadas
- Gráfico de ROI acumulado

---

## Avisos

- Isto envolve apostar dinheiro a sério. A ferramenta nunca envia apostas automaticamente.
- A 22bet é offshore do ponto de vista suíço; as contas podem ser limitadas se detetarem padrão de ganhador consistente. Lê os termos deles.
- A margem de lucro realista deste tipo de sistema é de **0,8–2% por aposta**, demonstrada em artigos académicos (Wheatcroft, 2020). Qualquer promessa de "87% de acerto" ou similar é falsa.
- Resultados fora da amostra (dinheiro real) são sempre piores que os do backtest. Começa com stakes mínimos.

---

## Como pedir ajuda

Se algo falhar, abre um issue neste repositório descrevendo:
1. Que comando correste
2. Que erro apareceu (copia o texto)
3. O que esperavas que acontecesse

---

## Licença

Uso pessoal. Não distribuir.
