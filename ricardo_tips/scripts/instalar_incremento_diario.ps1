# Instala a tarefa do incremento diario (00:15) e tira o limite de 2 horas
# ao scanner, sem mexer nas outras tarefas nem nos argumentos do scanner.
#
# Uso (PowerShell como administrador, dentro de ricardo_tips\):
#   .\scripts\instalar_incremento_diario.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = (Get-Item -LiteralPath (Join-Path $PSScriptRoot "..")).FullName
$VenvPy = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\incremento_diario.py"

# 1. Tarefa diaria as 00:15. StartWhenAvailable: se o PC estiver desligado
#    ou a dormir a essa hora, corre assim que voltar.
$nome = "RicardoTips_IncrementoDiario"
$acao = New-ScheduledTaskAction -Execute $VenvPy -Argument "`"$Script`"" -WorkingDirectory $ProjectRoot
$quando = New-ScheduledTaskTrigger -Daily -At "00:15"
$cfg = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false
}
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $quando -Settings $cfg `
    -Description "Ricardo Tips - incremento diario dos mini-cerebros (00:15)" | Out-Null
Write-Host "[OK] $nome instalada: todos os dias as 00:15"

# 2. Scanner sem limite de tempo (antes: parado pelo Windows ao fim de 2 horas).
$scanner = Get-ScheduledTask -TaskName "RicardoTips_Scanner" -ErrorAction SilentlyContinue
if ($scanner) {
    $scanner.Settings.ExecutionTimeLimit = "PT0S"
    Set-ScheduledTask -InputObject $scanner | Out-Null
    Write-Host "[OK] RicardoTips_Scanner: sem limite de tempo (argumentos mantidos)"
} else {
    Write-Warning "Nao encontrei a tarefa RicardoTips_Scanner"
}

# 3. Resolver com mais folga (uma corrida grande pode passar das 2 horas).
$resolver = Get-ScheduledTask -TaskName "RicardoTips_Resolver" -ErrorAction SilentlyContinue
if ($resolver) {
    $resolver.Settings.ExecutionTimeLimit = "PT6H"
    Set-ScheduledTask -InputObject $resolver | Out-Null
    Write-Host "[OK] RicardoTips_Resolver: limite 6 horas"
}
