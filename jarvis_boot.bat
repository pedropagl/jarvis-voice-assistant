@echo off
REM ===================================================================
REM  jarvis_boot.bat  -  inicio automatico do JARVIS (Agendador do Windows)
REM
REM  Sobe, minimizado:
REM    1) o painel (serve.py) na porta 8000  -> TV e rede local
REM    2) o tunel Cloudflare (acesso externo) e SALVA a URL num log
REM
REM  A URL externa muda a cada boot (tunel gratis). Para ver a URL atual,
REM  rode:  jarvis_url.bat   (ou abra logs\tunnel.log)
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "PY=%RAIZ%.venv\Scripts\python.exe"
set "CLOUDFLARED=C:\Users\PedroLeite\Downloads\cloudflared.exe"
if not exist "%RAIZ%logs" mkdir "%RAIZ%logs"

REM 1) Painel (minimizado). Aguarda a rede/servico subir por completo.
start "JARVIS Painel" /min "%PY%" "%RAIZ%dashboard\serve.py"

REM Espera o painel abrir a porta 8000 antes de subir o tunel.
timeout /t 8 /nobreak >nul

REM 2) Tunel Cloudflare (minimizado). Toda a saida vai para logs\tunnel.log,
REM    de onde extraimos a URL publica (linha com trycloudflare.com).
start "JARVIS Tunel" /min cmd /c ""%CLOUDFLARED%" tunnel --url http://127.0.0.1:8000 > "%RAIZ%logs\tunnel.log" 2>&1"

endlocal
