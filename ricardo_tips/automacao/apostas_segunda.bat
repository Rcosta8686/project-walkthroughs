@echo off
REM Automacao segunda-feira: puxa resultados + resolve apostas + mostra P&L.
REM Agendado via Windows Task Scheduler para correr segunda 10:00.

cd /d "%~dp0.."

REM Activa o ambiente virtual
call venv\Scripts\activate.bat

REM Ficheiro de output no Desktop (sobrescreve a cada corrida)
set OUTPUT=%USERPROFILE%\Desktop\apostas_pnl.txt

echo ============================================================ > "%OUTPUT%"
echo  Ricardo Tips - P^&L Segunda %DATE% %TIME% >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"
echo. >> "%OUTPUT%"

echo [1/3] A puxar codigo e resultados dos jogos... >> "%OUTPUT%"
git pull >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"
python scripts\atualizar_dados.py >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"

echo [2/3] A resolver apostas (W/L por sugestao)... >> "%OUTPUT%"
python scripts\resolver_sugestoes.py >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"

echo [3/3] P^&L cumulativo: >> "%OUTPUT%"
python scripts\mostrar_pnl.py >> "%OUTPUT%" 2>&1

echo. >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"
echo  Concluido %DATE% %TIME% >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"

exit /b 0
