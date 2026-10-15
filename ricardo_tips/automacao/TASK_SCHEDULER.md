# Task Scheduler Windows — os 3 fluxos automatizados

Depois destes passos, o teu PC envia sinais para o Telegram sem precisares
de fazer nada:

- **Diariamente às 12h00** → `pre_live_diario_auto.bat` → análise pre-match do dia
- **Sexta-feira às 09h00** → `fim_de_semana_auto.bat` → análise sex+sáb+dom
- **3 janelas por dia** → `scanner_live_v2_auto.bat` → scanner live durante jogos

Pré-requisitos (já tens):
- Python + venv instalado em `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\venv\`
- Docker Desktop com flaresolverr container (`docker start flaresolverr` nos .bats cobre o re-start)
- Token Telegram configurado em `.env`

---

## Pré-trabalho (1 vez só)

Abre PowerShell em `ricardo_tips\` e corre:

```powershell
git pull
venv\Scripts\python.exe scripts\migrar_m2_m13.py
venv\Scripts\python.exe scripts\atualizar_perfis_equipa.py
```

Explicação:
- `migrar_m2_m13.py` cria as 3 tabelas novas (OddsSnapshot, EquipaPerfil, BancaRegisto) **sem apagar** nada dos teus 1927 sinais.
- `atualizar_perfis_equipa.py` popula perfis das equipas que jogam hoje (~15 min no 1º run; depois cache 24h).

---

## 1. Pre-live diário — 12h00 todos os dias

### Passos no Task Scheduler

1. Abre **Task Scheduler** (Windows + R → `taskschd.msc`).
2. **Create Task** (não "Basic Task" — queremos mais opções).
3. **General**:
   - Name: `Ricardo Pre-Live 12h`
   - Run whether user is logged on or not ✓
   - Run with highest privileges ✓
4. **Triggers** → New:
   - Begin the task: `On a schedule`
   - Daily, start at **12:00:00**, recur every 1 day.
5. **Actions** → New:
   - Action: Start a program
   - Program/script: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\pre_live_diario_auto.bat`
   - Start in: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips`
6. **Conditions** → desactiva "Start the task only if the computer is on AC power" (para portáteis).
7. **Settings** → "Allow task to be run on demand" ✓
8. OK. Vai pedir a password da conta Windows.

### Testar imediatamente
Botão direito na tarefa → **Run**. Verifica em `ricardo_tips\dados\pre_live_diario.log`.

---

## 2. Fim-de-semana — Sexta 09h00

Mesmos passos, com estas diferenças:

- Name: `Ricardo Fim-de-Semana Sexta`
- Trigger: Weekly, **Friday** @ 09:00:00
- Action → Program: `C:\...\ricardo_tips\automacao\fim_de_semana_auto.bat`
- Action → Start in: `C:\...\ricardo_tips`

Isto corre o `pre_live_diario.py --dias 3` que apanha sex+sáb+dom (até 40 jogos). Vais receber TODOS os sinais do fim-de-semana numa só vez no Telegram.

Log em `dados\fim_de_semana.log`.

---

## 3. Scanner live — 3 janelas por dia

O scanner corre em modo contínuo (ciclo a cada 3 min). Agendamos 3 janelas típicas de jogos europeus:

### Trigger 1: tarde (jogos 14h-18h, Big-5 fim-de-semana)
- Name: `Ricardo Scanner Live Tarde`
- Trigger: Daily @ **13:30**
- Action: `scanner_live_v2_auto.bat`

### Trigger 2: início da noite (jogos 18:30-20:30, UEL/Portugal/Championship)
- Name: `Ricardo Scanner Live Noite 1`
- Trigger: Daily @ **18:00**
- Action: `scanner_live_v2_auto.bat`

### Trigger 3: noite tardia (jogos 21:00, UCL/UEL/LaLiga/Serie A)
- Name: `Ricardo Scanner Live Noite 2`
- Trigger: Daily @ **20:30**
- Action: `scanner_live_v2_auto.bat`

Cada janela corre até 90 ciclos (~4.5h max) e o scanner sai sozinho quando não houver mais jogos. Se já estiver a correr quando a janela seguinte começa, Task Scheduler salta (`Settings` → "Do not start a new instance").

Log em `dados\scanner_live.log`.

---

## Verificar banca a qualquer momento

```powershell
venv\Scripts\python.exe scripts\banca_status.py
```

Mostra: saldo, perda do dia, apostas abertas, se stop-loss foi atingido.

Também é usado como gate pelo `pre_live_diario.py` — se perdeste 5% hoje ou 10% na semana, o Telegram pre-live é bloqueado automaticamente (output continua a aparecer para contexto).

---

## 4. EV Fim-de-Semana (Odds API Pinnacle vs softs) — Sexta 10h00

Complementar ao fluxo 2 — este usa a Odds API (Pinnacle como fair price)
para encontrar sinais onde Betano/bet365 pagam mais que a probabilidade real.

### Passos
- Name: `Ricardo EV Fim-de-Semana`
- Trigger: Weekly, **Friday** @ 10:00:00
- Action → Program: `C:\...\ricardo_tips\automacao\ev_fim_de_semana_auto.bat`
- Action → Start in: `C:\...\ricardo_tips`

Faz 2 passos:
1. `atualizar_dados.py` — puxa fixtures + odds Pinnacle+softs via Odds API
2. `prever_fim_de_semana.py --modo soft-books --mercado todos --telegram`

Log em `dados\ev_fim_de_semana.log`.

> **Nota:** Odds API consome créditos (free tier: 500/mês). Se não quiseres gastar, podes usar só o fluxo 2 (`fim_de_semana_auto.bat`) que usa Sofascore gratuito.

---

## 5. Relatório CLV semanal (M14) — Domingo 22h00

Mostra closing line value médio dos últimos 7 dias por mercado + total.
CLV > 0 confirma que o scanner tem edge real.

### Passos
- Name: `Ricardo CLV Semanal`
- Trigger: Weekly, **Sunday** @ 22:00:00
- Action → Program: `C:\...\ricardo_tips\automacao\relatorio_clv_semanal.bat`

Envia resumo para Telegram: `n=X CLV +N.NN% positivo NN%`.

---

## Resumo horário

| Dia / hora | Fluxo |
|---|---|
| **Diário 12:00** | Pre-live diário Sofascore → Telegram |
| **Diário 13:30** | Scanner live tarde |
| **Diário 18:00** | Scanner live noite 1 |
| **Diário 20:30** | Scanner live noite 2 |
| **Sexta 09:00** | Fim-de-semana Sofascore (40 jogos sex+sáb+dom) |
| **Sexta 10:00** | EV Fim-de-semana Odds API (Pinnacle vs softs) |
| **Domingo 22:00** | Relatório CLV semanal |

---

## Troubleshooting

**Task "corre" mas nada acontece:**
- Abre o log em `dados\pre_live_diario.log` ou `dados\scanner_live.log`
- Confirma que o venv path é esse exacto (olha `where python` no PowerShell)

**Flaresolverr HTTP 403/404:**
- Docker container pode estar parado: `docker start flaresolverr`
- Confirma com `docker ps`

**Telegram não recebe:**
- Verifica banca_status: se stop-loss atingiu, Telegram é bloqueado
- Testa `venv\Scripts\python.exe scripts\debug_telegram.py`

**Portátil em sleep:**
- Nas definições de energia, "Wake timers": `Enabled`
- No Task Scheduler da tarefa: Settings → "Wake the computer to run this task" ✓
