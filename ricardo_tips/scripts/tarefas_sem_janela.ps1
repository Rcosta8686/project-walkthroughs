# Poe as tarefas RicardoTips a correr SEM JANELA (pythonw.exe em vez de python.exe):
# o scanner, o health check (de 30 em 30 min), o resolver, a noite... deixam de
# aparecer por cima dos outros programas. Continua tudo igual: horarios, argumentos,
# reinicios. O que faziam aparece no logs\ricardo_tips.log e no Telegram.
#
# Uso (PowerShell, dentro de ricardo_tips\):
#   powershell -ExecutionPolicy Bypass -File scripts\tarefas_sem_janela.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tarefas_sem_janela.ps1 -ComJanela   # voltar atras
# Se der "Acesso negado", abre o PowerShell como administrador.

param([switch]$ComJanela)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Item -LiteralPath (Join-Path $PSScriptRoot "..")).FullName
$Py = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$PyW = Join-Path $ProjectRoot "venv\Scripts\pythonw.exe"
if (-not (Test-Path $PyW)) {
    Write-Error "Nao encontrei $PyW"
    exit 1
}
$de, $para = if ($ComJanela) { "*\pythonw.exe", $Py } else { "*\python.exe", $PyW }

$mudadas = 0
foreach ($t in Get-ScheduledTask -TaskName "RicardoTips_*") {
    $acoes = @($t.Actions)
    $mudou = $false
    foreach ($a in $acoes) {
        if ($a.Execute -like $de) {
            $a.Execute = $para
            $mudou = $true
        }
    }
    if ($mudou) {
        Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Action $acoes | Out-Null
        Write-Host "  $($t.TaskName): $(Split-Path $para -Leaf)"
        $mudadas++
    }
}
Write-Host "`n$mudadas tarefas mudadas."
if (-not $ComJanela) {
    Write-Host "O scanner ja a correr continua com janela ate ser reiniciado:"
    Write-Host "  Stop-ScheduledTask -TaskName RicardoTips_Scanner; Start-ScheduledTask -TaskName RicardoTips_Scanner"
}
