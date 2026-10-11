@echo off
REM Scanner LIVE v2: modelo preditivo (Sofascore) + odds live (Odds API).
REM Requer flaresolverr a correr (ver FLARESOLVERR_SETUP.md).

cd /d "%~dp0.."

echo ============================================================
echo  Scanner LIVE v2 - modelo preditivo + odds live
echo ============================================================
echo.

REM Puxa automaticamente a ultima versao do codigo antes de arrancar.
REM Assim, cada vez que fechas e re-arrancas, apanhas sempre o fix mais recente.
echo [1/3] A verificar actualizacoes no GitHub...
git pull
echo.

REM Activa o ambiente virtual
echo [2/3] A activar ambiente Python...
call venv\Scripts\activate.bat
echo.

echo [3/3] A iniciar scanner (sai com Ctrl+C):
echo.

python scripts\scanner_live_v2.py %*

pause
