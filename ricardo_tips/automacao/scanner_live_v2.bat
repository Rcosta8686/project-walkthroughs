@echo off
REM Scanner LIVE v2: modelo preditivo (Sofascore) + odds live (Odds API).
REM Requer flaresolverr a correr (ver FLARESOLVERR_SETUP.md).

cd /d "%~dp0.."

REM Activa o ambiente virtual
call venv\Scripts\activate.bat

echo ============================================================
echo  Scanner LIVE v2 - modelo preditivo + odds live
echo ============================================================
echo.
echo  Pre-requisito: flaresolverr a correr
echo    docker ps ^| findstr flaresolverr
echo.
echo  Sai com Ctrl+C.
echo.

python scripts\scanner_live_v2.py %*

pause
