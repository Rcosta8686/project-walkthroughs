@echo off
REM EV Fim-de-semana AUTO — Sofascore odds pre-match (gratis).
REM
REM Decisao 2026-10-15: Odds API esgotou os creditos free tier (500/mes).
REM Reset so em Nov. Usamos Sofascore via flaresolverr que e gratis e
REM da-nos odds pre-match similares (varias casas incluindo Pinnacle).
REM
REM Agendar sexta-feira 12:00 no Windows Task Scheduler:
REM   Program/script: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\ev_fim_de_semana_auto.bat
REM   Start in: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
REM   Trigger: Weekly > Friday @ 12:00
REM
REM Fluxo:
REM   pre_live_diario.py --dias 3 --max-jogos 60 --so-com-ev --telegram
REM     - Puxa jogos sex+sab+dom via Sofascore
REM     - Para cada: le odds pre-match Sofascore -> p_mercado -> blend -> EV
REM     - Filtra jogos onde pelo menos 1 sinal tem EV>=3%
REM     - Agrupa por dia e envia Telegram estilo lista limpa

cd /d "%~dp0.."

set LOG=dados\ev_fim_de_semana.log

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

echo [pre_live_diario --dias 3 --max-jogos 60 --so-com-ev --telegram] >> %LOG%
venv\Scripts\python.exe scripts\pre_live_diario.py --dias 3 --max-jogos 60 --so-com-ev --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
