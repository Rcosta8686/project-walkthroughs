@echo off
REM Scanner LIVE: durante os jogos, procura divergencias Pinnacle vs softs
REM em tempo real. Nao e Task Scheduler, abres isto quando ha jogos a decorrer.
REM
REM Uso: clica duas vezes para abrir. Sai com Ctrl+C.
REM Para passar flags: scanner_live.bat --intervalo-s 180

cd /d "%~dp0.."

REM Activa o ambiente virtual
call venv\Scripts\activate.bat

echo ============================================================
echo  Scanner LIVE - Pinnacle vs softs em tempo real
echo ============================================================
echo.
echo  Sai com Ctrl+C.
echo  Verifica SEMPRE na casa se a aposta ainda esta disponivel
echo  antes de confirmar (softs suspendem mercados in-play).
echo.

python scripts\scanner_live.py %*

pause
