@echo off
REM ===================================================================
REM  jarvis_url.bat  -  mostra a URL externa atual do tunel Cloudflare
REM  (lida do logs\tunnel.log gerado pelo jarvis_boot.bat)
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "LOG=%RAIZ%logs\tunnel.log"

if not exist "%LOG%" (
    echo Nao encontrei o log do tunel. O JARVIS ja foi iniciado pelo jarvis_boot.bat?
    pause
    exit /b 1
)

echo.
echo  URL externa atual do JARVIS:
echo  ---------------------------------------------
findstr /C:"trycloudflare.com" "%LOG%"
echo  ---------------------------------------------
echo  (Se aparecer mais de uma, use a ultima linha.)
echo.
pause
