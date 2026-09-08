@echo off
REM ===================================================================
REM  jarvis_tunnel.bat
REM  Sobe o painel da JARVIS + tunel Cloudflare (acesso externo)
REM
REM  Uso: clique duas vezes ou rode no terminal
REM  Para encerrar: feche as duas janelas abertas
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "PY=%RAIZ%.venv\Scripts\python.exe"
set "CLOUDFLARED=C:\Users\PedroLeite\Downloads\cloudflared.exe"

echo.
echo  Iniciando painel da JARVIS...
start "JARVIS Painel" cmd /k ""%PY%" "%RAIZ%main.py" painel"

REM Aguarda o servidor subir
timeout /t 5 /nobreak >nul

echo  Iniciando tunel Cloudflare...
echo  A URL publica aparecera abaixo (linha com trycloudflare.com)
echo  Compartilhe essa URL com quem precisar acessar de fora do lab.
echo.
"%CLOUDFLARED%" tunnel --url http://127.0.0.1:8000

pause
