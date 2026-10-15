@echo off
REM Fim-de-semana AUTO: pre-live cobrindo 3 dias (sex+sab+dom).
REM Agendar sexta-feira ~09:00 ou ~12:00 no Windows Task Scheduler.
REM
REM Agendamento:
REM   Program/script: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\fim_de_semana_auto.bat
REM   Start in: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
REM   Trigger: Weekly > Friday @ 09:00

cd /d "%~dp0.."

set LOG=dados\fim_de_semana.log

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

echo [pre_live_diario --dias 3 --max-jogos 40 --telegram] >> %LOG%
venv\Scripts\python.exe scripts\pre_live_diario.py --dias 3 --max-jogos 40 --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
