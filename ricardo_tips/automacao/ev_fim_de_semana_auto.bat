@echo off
REM EV Fim-de-semana AUTO — Pinnacle vs softs via Odds API.
REM Agendar sexta-feira 12:00 no Windows Task Scheduler.
REM
REM Fluxo:
REM   1. atualizar_dados.py  (puxa fixtures + odds correntes via Odds API)
REM   2. prever_fim_de_semana.py --modo soft-books --mercado todos --telegram
REM
REM Diferente do fim_de_semana_auto.bat:
REM   - Este usa Odds API (Pinnacle como fair price) para encontrar
REM     onde softs (Betano/bet365) tem EV positivo
REM   - fim_de_semana_auto.bat usa Sofascore (modelo + perfil + H2H)
REM Os dois sao complementares — podes ter ambos agendados.
REM
REM Agendamento:
REM   Program/script: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\ev_fim_de_semana_auto.bat
REM   Start in: C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
REM   Trigger: Weekly > Friday @ 12:00

cd /d "%~dp0.."

set LOG=dados\ev_fim_de_semana.log

echo === INICIO %date% %time% === >> %LOG%

echo [git pull] >> %LOG%
git pull >> %LOG% 2>&1

echo [1/2 atualizar_dados.py — puxar odds Pinnacle+softs via Odds API] >> %LOG%
venv\Scripts\python.exe scripts\atualizar_dados.py >> %LOG% 2>&1

echo [2/2 prever_fim_de_semana --modo soft-books --mercado todos --telegram] >> %LOG%
venv\Scripts\python.exe scripts\prever_fim_de_semana.py --modo soft-books --mercado todos --telegram >> %LOG% 2>&1

echo === FIM %date% %time% === >> %LOG%
echo. >> %LOG%
