@echo off
REM ===================================================================
REM  JARVIS - inicializacao automatica ao ligar o PC
REM  Sobe o servidor do painel em segundo plano (minimizado, sem travar
REM  a tela) e abre a interface no navegador padrao assim que o servidor
REM  estiver pronto.
REM
REM  Este arquivo e chamado automaticamente pelo atalho na pasta
REM  Inicializar do Windows (veja instalar_autostart.bat). Tambem pode
REM  ser executado manualmente a qualquer momento com duplo clique.
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "PY=%RAIZ%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERRO] Nao encontrei o venv em "%PY%".
    echo Rode instalar.bat primeiro para criar o ambiente.
    pause
    exit /b 1
)

REM Sobe o servidor minimizado (janela fica na barra de tarefas, nao atrapalha).
start "JARVIS - Painel" /min "%PY%" "%RAIZ%dashboard\serve.py"

REM Espera o servidor subir antes de abrir o navegador.
timeout /t 4 /nobreak >nul

start "" "http://127.0.0.1:8000/dashboard/index.html"

endlocal
