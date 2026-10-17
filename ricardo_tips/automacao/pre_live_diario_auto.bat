@echo off
REM Pre-live diario — versao AUTO para Task Scheduler Windows.
REM Sem pause, grava output em dados\pre_live_diario.log.
REM
REM Agendar no Task Scheduler:
REM   Program/script: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\pre_live_diario_auto.bat
REM   Start in: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
REM   Trigger: Daily @ 13:30 (margem apos primeiros jogos da tarde comecarem)

cd /d "%~dp0.."

REM Forca UTF-8 no Python stdout/stderr (Windows cp1252 nao aceita emojis).
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set LOG=dados\pre_live_diario.log

echo === INICIO %date% %time% === >> %LOG%

echo [git pull] >> %LOG%
git pull >> %LOG% 2>&1

echo [flaresolverr check] >> %LOG%
docker ps --filter "name=flaresolverr" --format "{{.Names}}" | findstr flaresolverr >nul
if errorlevel 1 (
    echo    arrancar flaresolverr... >> %LOG%
    docker start flaresolverr >> %LOG% 2>&1
    timeout /t 3 /nobreak >nul
)

echo [pre_live_diario.py] >> %LOG%
venv\Scripts\python.exe scripts\pre_live_diario.py --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
