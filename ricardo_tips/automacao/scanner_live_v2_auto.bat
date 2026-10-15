@echo off
REM Scanner LIVE v2 AUTO — correr 24/24 em loop infinito.
REM Sem pause, com log, auto-restart se cair.
REM
REM Agendamento (Task Scheduler Windows):
REM   Program/script: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\scanner_live_v2_auto.bat
REM   Start in: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
REM   Triggers:
REM     1. At startup   (arranca com o Windows)
REM     2. At logon     (arranca quando fazes login)
REM   Settings:
REM     - Allow task to be run on demand   ✓
REM     - If task fails, restart every     5 minutes, up to 999 times
REM     - Stop task if it runs longer than DESMARCAR (queremos infinito)
REM     - Do not start a new instance (se ja estiver a correr)
REM
REM O scanner internamente espera 180s entre ciclos. Se nao houver
REM jogos nas ligas focais, dorme e volta a verificar. Ciclos 0 = infinito.

cd /d "%~dp0.."

set LOG=dados\scanner_live.log

echo === INICIO %date% %time% === >> %LOG%

REM git pull para apanhar fixes
git pull >> %LOG% 2>&1

REM flaresolverr check
docker ps --filter "name=flaresolverr" --format "{{.Names}}" | findstr flaresolverr >nul
if errorlevel 1 (
    echo    arrancar flaresolverr... >> %LOG%
    docker start flaresolverr >> %LOG% 2>&1
    timeout /t 3 /nobreak >nul
)

echo [scanner_live_v2.py --ciclos 0 --telegram] 24/24 >> %LOG%
REM --ciclos 0 = loop infinito (nao sai sozinho).
REM Se crashar, Task Scheduler reinicia pela retry policy.
venv\Scripts\python.exe scripts\scanner_live_v2.py --ciclos 0 --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === (crashou? Task Scheduler vai reiniciar) >> %LOG%
echo. >> %LOG%
