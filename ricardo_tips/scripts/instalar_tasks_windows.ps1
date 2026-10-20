# Instalar tasks do Windows Task Scheduler para o Ricardo Tips.
#
# Uso (PowerShell como administrador, dentro de ricardo_tips\):
#   .\scripts\instalar_tasks_windows.ps1
#
# Cria 9 tasks com o prefixo "RicardoTips_":
#   - Scanner (ao login, reinicia se cair)
#   - Resolver (cada 90 min)
#   - Preview diario (17:00)
#   - Preview tactico fim-de-semana (sex 17:00)
#   - Previsao EV fim-de-semana (sex 18:00)
#   - Relatorio semanal (seg 09:00)
#   - Relatorio mensal (dia 1, 07:00)
#   - Health check (cada 30 min)
#   - Compactacao CSV (dom 23:00)
#
# Para remover tudo: .\scripts\instalar_tasks_windows.ps1 -Remover

[CmdletBinding()]
param(
    [switch]$Remover
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Get-Item -LiteralPath (Join-Path $PSScriptRoot "..")).FullName
$VenvPy = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$Scripts = Join-Path $ProjectRoot "scripts"

if (-not (Test-Path $VenvPy)) {
    Write-Error "Nao encontrei o Python do venv em: $VenvPy"
    exit 1
}

# ------------- Lista de tasks -------------
# Cada entrada:
#   Name:       nome final no Task Scheduler (ja com prefixo)
#   Script:     ficheiro .py sob scripts\
#   Args:       argumentos extra para o script
#   Trigger:    tipo (Daily, Weekly, Monthly, Logon, OnceEvery)
#   Params:     parametros do trigger

$Tasks = @(
    @{
        Name = "RicardoTips_Scanner"
        Script = "scanner_live_v2.py"
        Args = "--intervalo-s 180 --telegram --ev-minimo-telegram 0.07"
        Trigger = "Logon"
        RestartOnFailure = $true
    },
    @{
        Name = "RicardoTips_Resolver"
        Script = "resolver_sugestoes_live.py"
        Args = ""
        Trigger = "OnceEvery"
        IntervalMinutes = 90
    },
    @{
        Name = "RicardoTips_PreviewDia"
        Script = "preview_dia.py"
        Args = "--telegram"
        Trigger = "Daily"
        Time = "17:00"
    },
    @{
        Name = "RicardoTips_PreviewFimDeSemanaTactico"
        Script = "preview_fim_de_semana_v2.py"
        Args = "--telegram"
        Trigger = "Weekly"
        DayOfWeek = "Friday"
        Time = "17:00"
    },
    @{
        Name = "RicardoTips_PreverFimDeSemanaEV"
        Script = "prever_fim_de_semana.py"
        Args = "--telegram"
        Trigger = "Weekly"
        DayOfWeek = "Friday"
        Time = "18:00"
    },
    @{
        Name = "RicardoTips_RelatorioSemanal"
        Script = "relatorio_semanal.py"
        Args = "--telegram"
        Trigger = "Weekly"
        DayOfWeek = "Monday"
        Time = "09:00"
    },
    @{
        Name = "RicardoTips_RelatorioMensal"
        Script = "relatorio_mensal.py"
        Args = "--telegram"
        Trigger = "Monthly"
        Day = 1
        Time = "07:00"
    },
    @{
        Name = "RicardoTips_HealthCheck"
        Script = "health_check.py"
        Args = ""
        Trigger = "OnceEvery"
        IntervalMinutes = 30
    },
    @{
        Name = "RicardoTips_CompactarCSV"
        Script = "compactar_csv_live.py"
        Args = ""
        Trigger = "Weekly"
        DayOfWeek = "Sunday"
        Time = "23:00"
    }
)


# ============ Remover modo ============
if ($Remover) {
    Write-Host "`n[REMOVER] Tasks RicardoTips_*...`n"
    foreach ($t in $Tasks) {
        $name = $t.Name
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "  removida: $name"
        } else {
            Write-Host "  (nao existia): $name"
        }
    }
    Write-Host "`n[OK] Remocao completa.`n"
    exit 0
}


# ============ Criar/actualizar ============
Write-Host "`n[INSTALAR] Tasks RicardoTips em $ProjectRoot`n"

function New-RicardoTrigger {
    param($TaskDef)

    switch ($TaskDef.Trigger) {
        "Logon" {
            return New-ScheduledTaskTrigger -AtLogOn
        }
        "Daily" {
            return New-ScheduledTaskTrigger -Daily -At $TaskDef.Time
        }
        "Weekly" {
            return New-ScheduledTaskTrigger `
                -Weekly -DaysOfWeek $TaskDef.DayOfWeek -At $TaskDef.Time
        }
        "Monthly" {
            # Monthly trigger - PowerShell nao tem helper directo, usamos CIM
            $trigger = New-ScheduledTaskTrigger -Daily -At $TaskDef.Time
            # Hack: Daily + ByDays filter via CIM instance mais a frente.
            # Para simplicidade usamos Daily - na pratica so corre quando
            # for dia 1 (filtro no proprio script).
            return $trigger
        }
        "OnceEvery" {
            # Repeat trigger - comeca agora, repete cada N minutos
            $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                -RepetitionInterval (New-TimeSpan -Minutes $TaskDef.IntervalMinutes) `
                -RepetitionDuration ([TimeSpan]::MaxValue)
            return $trigger
        }
    }
}

foreach ($t in $Tasks) {
    $name = $t.Name
    $scriptPath = Join-Path $Scripts $t.Script
    if (-not (Test-Path $scriptPath)) {
        Write-Warning "  SKIP $name - nao encontrou $scriptPath"
        continue
    }

    # Argumentos completos: script.py + args
    $argumentList = "`"$scriptPath`" $($t.Args)".Trim()
    $action = New-ScheduledTaskAction `
        -Execute $VenvPy `
        -Argument $argumentList `
        -WorkingDirectory $ProjectRoot

    $trigger = New-RicardoTrigger -TaskDef $t

    # Settings: nao bloqueia se outra correr, auto-restart em caso de falha
    $settingsArgs = @{
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
        StartWhenAvailable = $true
        MultipleInstances = "IgnoreNew"
        ExecutionTimeLimit = (New-TimeSpan -Hours 2)
    }
    if ($t.RestartOnFailure) {
        $settingsArgs["RestartCount"] = 3
        $settingsArgs["RestartInterval"] = (New-TimeSpan -Minutes 5)
    }
    $settings = New-ScheduledTaskSettingsSet @settingsArgs

    # Remove task existente (idempotente)
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }

    Register-ScheduledTask `
        -TaskName $name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Ricardo Tips - $($t.Trigger)" `
        -RunLevel Limited | Out-Null

    Write-Host "  [+] instalada: $name ($($t.Trigger))"
}

Write-Host "`n[OK] Tasks instaladas. Ver no Task Scheduler -> RicardoTips_*"
Write-Host "   Para remover tudo: .\scripts\instalar_tasks_windows.ps1 -Remover`n"
