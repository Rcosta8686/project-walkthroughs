@echo off
REM Automacao sexta-feira: puxa dados + scanner + grava sugestoes no Desktop.
REM Agendado via Windows Task Scheduler para correr sexta 10:00.

cd /d "%~dp0.."

REM Activa o ambiente virtual
call venv\Scripts\activate.bat

REM Ficheiro de output no Desktop (sobrescreve a cada corrida)
set OUTPUT=%USERPROFILE%\Desktop\apostas_sugestoes.txt

echo ============================================================ > "%OUTPUT%"
echo  Ricardo Tips - Sugestoes Sexta %DATE% %TIME% >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"
echo. >> "%OUTPUT%"

echo [1/3] A puxar codigo e dados... >> "%OUTPUT%"
git pull >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"
python scripts\atualizar_dados.py >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"

echo [2/3] A procurar sugestoes de valor... >> "%OUTPUT%"
python scripts\trackear_sugestoes_softs.py --ev-minimo 0.05 --stake 0.50 >> "%OUTPUT%" 2>&1
echo. >> "%OUTPUT%"

echo [3/3] A formatar lista de apostas... >> "%OUTPUT%"
python scripts\mostrar_sugestoes_pendentes.py --stake 0.50 >> "%OUTPUT%" 2>&1

echo. >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"
echo  Concluido %DATE% %TIME% >> "%OUTPUT%"
echo ============================================================ >> "%OUTPUT%"

exit /b 0
