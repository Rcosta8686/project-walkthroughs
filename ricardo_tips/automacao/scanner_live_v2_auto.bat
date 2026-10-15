@echo off
REM Scanner LIVE v2 AUTO — versao sem pause, com log.
REM Pensado para Task Scheduler Windows correr em janelas de jogos
REM (ou 24/7 — o scanner internamente espera entre ciclos).
REM
REM Agendamento (3 janelas tipicas de jogos europeus):
REM   Trigger 1: Daily @ 14:00  (jogos tarde: Portugal, Bundesliga, Serie A, EPL)
REM   Trigger 2: Daily @ 18:00  (jogos 18:30-19:00: Portugal, Europa League 1a hora)
REM   Trigger 3: Daily @ 20:30  (jogos 21:00: Champions, Europa, LaLiga/Serie A)
REM
REM Cada corrida fica a correr enquanto houver jogos. Scanner termina
REM automaticamente quando nao houver jogos nas ligas focais.

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

echo [scanner_live_v2.py --telegram] >> %LOG%
REM --ciclos 90: ate ~3h (90 ciclos * 180s = 4.5h max). Scanner sai antes
REM se nao houver jogos. Depois Task Scheduler repete em janela seguinte.
venv\Scripts\python.exe scripts\scanner_live_v2.py --ciclos 90 --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
