@echo off
REM Pre-live diario — analise pre-match + envia resumo Telegram.
REM Para correr MANUALMENTE: duplo-clique neste ficheiro ou:
REM   .\automacao\pre_live_diario.bat
REM
REM Para AGENDAR no Task Scheduler Windows (12h diario):
REM   usa pre_live_diario_auto.bat em vez deste (sem pause, com log).

cd /d "%~dp0.."

echo ============================================================
echo  PRE-LIVE DIARIO - analise pre-match
echo ============================================================
echo.

echo [1/3] A puxar ultimo codigo do GitHub...
git pull
echo.

echo [2/3] A verificar flaresolverr...
docker ps --filter "name=flaresolverr" --format "{{.Names}}" | findstr flaresolverr >nul
if errorlevel 1 (
    echo    AVISO: flaresolverr nao esta a correr. A arrancar...
    docker start flaresolverr
    timeout /t 3 /nobreak >nul
) else (
    echo    flaresolverr OK
)
echo.

echo [3/3] A correr pre-live diario (envia para Telegram)...
echo.
venv\Scripts\python.exe scripts\pre_live_diario.py --telegram

echo.
echo ============================================================
echo  Terminado. Carrega qualquer tecla para fechar.
echo ============================================================
pause
