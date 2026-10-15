# Task Scheduler Windows — 4 fluxos automatizados

Configuração final (confirmada 2026-10-15):

| Dia / hora | Tarefa | Frequência |
|---|---|---|
| **Diário 13h30** | `pre_live_diario_auto.bat` | Todos os dias |
| **24/24** | `scanner_live_v2_auto.bat` | Correr sempre (restart on crash) |
| **Sexta 12h00** | ⭐ `ev_fim_de_semana_auto.bat` | Só sexta |
| **Domingo 22h00** | `relatorio_clv_semanal.bat` | Só domingo |

---

## Pré-trabalho (1 vez só)

```powershell
cd ricardo_tips
git pull
venv\Scripts\python.exe scripts\migrar_m2_m13.py
venv\Scripts\python.exe scripts\atualizar_perfis_equipa.py
```

Verifica Docker Desktop está aberto (`docker ps` deve mostrar `flaresolverr`).

---

## 1. Pre-live Diário — 13h30 todos os dias

### Passos
1. Abre **Task Scheduler** (`Win+R` → `taskschd.msc`).
2. **Create Task** → tab **General**:
   - Name: `Ricardo Pre-Live 13h30`
   - Run whether user is logged on or not ✓
   - Run with highest privileges ✓
3. **Triggers** → New:
   - On a schedule, Daily, start at **13:30:00**, recur every 1 day
4. **Actions** → New:
   - Program: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\pre_live_diario_auto.bat`
   - Start in: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips`
5. **Conditions**: desactiva "Start only if on AC power" (para portáteis)
6. **Settings**: "Allow task to be run on demand" ✓

Log: `dados\pre_live_diario.log`

---

## 2. Scanner Live — 24/24

Correr permanentemente (restart automático se cair).

### Passos
1. **Create Task** → **General**:
   - Name: `Ricardo Scanner Live 24h`
   - Run whether user is logged on or not ✓
   - Run with highest privileges ✓
2. **Triggers** → New **2 triggers**:
   - **Trigger A**: Begin task = **At startup** (arranca com Windows)
   - **Trigger B**: Begin task = **At logon** (arranca ao fazer login)
3. **Actions** → New:
   - Program: `C:\...\ricardo_tips\automacao\scanner_live_v2_auto.bat`
   - Start in: `C:\...\ricardo_tips`
4. **Conditions**: desactiva "Start only if on AC power"
5. **Settings** — **IMPORTANTE para 24/24:**
   - "Allow task to be run on demand" ✓
   - "If the task fails, restart every" → **5 minutes**, up to **999 times**
   - "Stop the task if it runs longer than" → **DESMARCA** (queremos infinito)
   - "If the running task does not end when requested, force it to stop" ✓
   - "If the task is already running, then the following rule applies" → **Do not start a new instance**

Scanner corre `--ciclos 0` (loop infinito). Dorme 180s entre ciclos. Se nenhum jogo nas ligas focais, continua a verificar silenciosamente.

Log: `dados\scanner_live.log` (vai crescer — não apagar durante o dia)

---

## 3. ⭐ EV Fim-de-Semana — Sexta 12h00

Lista limpa estilo "Marítimo x Porto · Over 2.5 · EV +6.4% · stake 5 CHF".

### Passos
- Name: `Ricardo EV Fim-de-Semana`
- Trigger: Weekly, **Friday** @ **12:00:00**
- Action → Program: `C:\...\ricardo_tips\automacao\ev_fim_de_semana_auto.bat`
- Action → Start in: `C:\...\ricardo_tips`

Faz 2 passos:
1. `atualizar_dados.py` → puxa Pinnacle + softs via Odds API
2. `prever_fim_de_semana.py --modo soft-books --telegram` → Telegram agrupado por dia

Log: `dados\ev_fim_de_semana.log`

---

## 4. Relatório CLV Semanal — Domingo 22h00

Mostra CLV médio dos 7 dias por mercado + veredicto edge/sem edge.

### Passos
- Name: `Ricardo CLV Semanal`
- Trigger: Weekly, **Sunday** @ **22:00:00**
- Action → Program: `C:\...\ricardo_tips\automacao\relatorio_clv_semanal.bat`

---

## Teste manual antes de agendar

Correr cada um para confirmar que funciona:

```powershell
.\automacao\pre_live_diario.bat                          # pre-live (versao com pause)
venv\Scripts\python.exe scripts\scanner_live_v2.py --ciclos 1 --telegram  # 1 ciclo scanner
.\automacao\ev_fim_de_semana_auto.bat                    # EV FDS
venv\Scripts\python.exe scripts\mostrar_clv.py --dias 7 --telegram  # CLV
```

Se os 4 correm bem, agenda os `_auto.bat` no Task Scheduler.

---

## Verificar banca a qualquer momento

```powershell
venv\Scripts\python.exe scripts\banca_status.py
```

Mostra saldo CHF 50, perda hoje, apostas abertas, stop-loss state.
Se stop-loss diário (-5%) ou semanal (-10%) foi atingido, **Telegram é bloqueado automaticamente** nos 4 fluxos.

---

## Troubleshooting

**Scanner não envia Telegram:**
- Hard floor é 3 estrelas (default). Para ver mais: `--analista-tele-estrelas 2`
- Para ver só os melhores: `--analista-tele-estrelas 5`

**Flaresolverr HTTP 403/404:**
- Docker Desktop fechado? `docker start flaresolverr`

**Portátil em sleep:**
- Nas definições de energia Windows, "Allow wake timers": `Enable`
- Em cada task: Settings → "Wake the computer to run this task" ✓

**Scanner caiu e não reiniciou:**
- Verifica no Task Scheduler se a retry policy está `5 min / 999 vezes`
- Olha o `dados\scanner_live.log` para ver o erro
