@echo off
REM Relatorio CLV semanal (M14) — M-doc "Medir, reduzir, proteger".
REM Mostra CLV medio dos ultimos 7 dias por mercado + total.
REM
REM Agendar domingo noite ~22:00 no Windows Task Scheduler:
REM   Trigger: Weekly > Sunday @ 22:00
REM
REM CLV > 0 = scanner tem edge real
REM CLV < 0 = scanner esta a apostar a precos piores que o fecho (mau)

cd /d "%~dp0.."

set LOG=dados\clv_semanal.log

echo === INICIO %date% %time% === >> %LOG%

git pull >> %LOG% 2>&1

echo [mostrar_clv --dias 7 --telegram] >> %LOG%
venv\Scripts\python.exe scripts\mostrar_clv.py --dias 7 --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
