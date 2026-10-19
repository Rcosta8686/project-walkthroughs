# Automacao Windows — Ricardo Tips

Como instalar as tasks que fazem o sistema correr sozinho.

## O que fica agendado

| Task | Quando | Envia Telegram? |
|---|---|---|
| **Scanner live** | Ao login do PC (reinicia se cair) | ✅ Alertas em tempo real |
| **Resolver** (fecha jogos + grava histórico) | Cada 90 min | ❌ Silencioso |
| **Preview do dia** (jogos de hoje) | **17:00** diário | ✅ |
| **Preview tático fim-de-semana** | **Sex 17:00** | ✅ |
| **Previsão EV fim-de-semana** | **Sex 18:00** | ✅ |
| **Relatório semanal** | **Seg 09:00** | ✅ |
| **Relatório mensal** | **Dia 1, 07:00** | ✅ |
| **Health check** (flaresolverr + CSV + BD) | Cada 30 min | ⚠ Só se houver problema |
| **Compactar CSV** (arquiva >90 dias) | **Dom 23:00** | ❌ Silencioso |

## Instalar

1. Abre PowerShell **como administrador**.
2. `cd C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips`
3. Permitir execução do script esta sessão:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   ```
4. Correr:
   ```powershell
   .\scripts\instalar_tasks_windows.ps1
   ```

As tasks aparecem no **Task Scheduler** com prefixo `RicardoTips_`.

## Mudar horas

Edita `scripts\instalar_tasks_windows.ps1` nas secções `Time = "HH:MM"`
e re-corre o comando acima. É idempotente — substitui as tasks existentes.

## Remover tudo

```powershell
.\scripts\instalar_tasks_windows.ps1 -Remover
```

## Ver estado / correr manualmente

```powershell
# Listar
Get-ScheduledTask -TaskName "RicardoTips_*" | Format-Table TaskName, State

# Correr agora (para testar)
Start-ScheduledTask -TaskName "RicardoTips_PreviewDia"

# Ver ultima execução
Get-ScheduledTaskInfo -TaskName "RicardoTips_PreviewDia"
```

## Como funciona o health check

Corre cada 30 min. Em silêncio quando tudo OK. Envia Telegram **só** se
detectar:
- Flaresolverr parado / lento
- CSV live sem update há >30 min (scanner provavelmente morreu)
- BD sqlite corrompida

Se receberes a mensagem, abre a janela do scanner e vê o log. Se o
flaresolverr for o problema: `docker restart flaresolverr`.

## Compactação do CSV

Ao domingo 23:00, linhas com `ts_scan` >90 dias são movidas de
`sugestoes_live_v2.csv` para `sugestoes_live_v2_arquivo.csv`. Mantém o
principal rápido sem perder histórico.

Para correr manualmente (dry-run só mostra contagens):
```powershell
venv\Scripts\python.exe scripts\compactar_csv_live.py --dry-run
venv\Scripts\python.exe scripts\compactar_csv_live.py --manter 90
```
