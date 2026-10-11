# Notificacoes Telegram (receber sugestoes no telemovel)

Com isto, em vez de olhar o PowerShell, recebes mensagens no telemovel
quando o scanner apanha uma sugestao com EV elevado.

Setup unico (~5 minutos).

## Passo 1 — Criar o bot

1. Abre o Telegram
2. Pesquisa por `@BotFather` (bot oficial do Telegram)
3. Envia `/newbot`
4. Escolhe um nome visivel (ex: "Ricardo Tips")
5. Escolhe um username unico que acabe em `bot` (ex: `ricardo_tips_bot`)
6. Vais receber um TOKEN: algo tipo `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`
7. **Copia esse TOKEN** — e a palavra-passe do teu bot

## Passo 2 — Obter o teu chat_id

1. Fala uma vez com o bot que acabaste de criar
   - Pesquisa o username que escolheste (ex: `@ricardo_tips_bot`)
   - Envia qualquer coisa, por exemplo `/start`
2. No browser do PC, cola este URL (substitui `<TOKEN>` pelo que apanhaste):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Vais ver JSON com uma linha tipo `"chat":{"id":123456789,...`
4. **Copia esse numero** — e o teu chat_id

## Passo 3 — Pôr no .env

Abre o ficheiro `.env` em `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\.env`
e adiciona (ou substitui) estas linhas:

```
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
MODO=producao
```

**Importante**: a linha `MODO=producao` tem de estar lá, senão em
`desenvolvimento` as mensagens vao para `dados/telegram_outbox.log`
em vez do Telegram.

## Passo 4 — Correr o scanner com Telegram ligado

```
.\automacao\scanner_live_v2.bat --telegram
```

Ou com limiar personalizado (so notifica EV >= 10%):
```
.\automacao\scanner_live_v2.bat --telegram --ev-minimo-telegram 0.10
```

## O que vais receber

Cada sugestao chega como mensagem Telegram tipo:

```
🎯 EV +8.5% LIVE

⚽ Elche vs Celta
🏆 LaLiga · [80'] 0-0
📊 xG 0.4-0.3 · SoT 1-0

Aposta: UNDER 2.5 golos
Odd: 1.08 (betano_uk)
Prob modelo: 97.5%

Abrir casa
```

## Dicas

- **Spam**: cada sugestao vai 1x por scan. Se o jogo continua a 0-0 ao 85',
  vais receber a sugestao Under 2.5 outra vez no proximo scan (porque o
  EV subiu). Normal.
- **Threshold**: `--ev-minimo-telegram 0.10` faz com que so os edges mais
  fortes te acordem. 0.07 (default) e o equilibrio.
- **Silenciar**: tira o `--telegram` da flag e volta a correr normal.
