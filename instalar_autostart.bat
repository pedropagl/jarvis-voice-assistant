@echo off
REM ===================================================================
REM  Instala (ou remove) a inicializacao automatica da JARVIS.
REM  Cria um atalho para jarvis_autostart.bat na pasta "Inicializar" do
REM  Windows, para que o painel abra sozinho toda vez que o PC ligar.
REM
REM  Uso:
REM     instalar_autostart.bat          -> instala
REM     instalar_autostart.bat remover  -> remove a inicializacao automatica
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "ATALHO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk"

if /I "%~1"=="remover" (
    if exist "%ATALHO%" (
        del "%ATALHO%"
        echo Inicializacao automatica da JARVIS REMOVIDA.
    ) else (
        echo Nao havia inicializacao automatica instalada.
    )
    goto :fim
)

powershell -NoProfile -Command ^
    "$s = (New-Object -COMObject WScript.Shell).CreateShortcut('%ATALHO%');" ^
    "$s.TargetPath = '%RAIZ%jarvis_autostart.bat';" ^
    "$s.WorkingDirectory = '%RAIZ%';" ^
    "$s.WindowStyle = 7;" ^
    "$s.Description = 'Abre o painel da JARVIS automaticamente';" ^
    "$s.Save()"

if exist "%ATALHO%" (
    echo Pronto! A JARVIS vai abrir sozinha na proxima vez que o PC ligar.
    echo Atalho criado em: %ATALHO%
    echo Para desfazer: instalar_autostart.bat remover
) else (
    echo [ERRO] Nao consegui criar o atalho. Tente rodar como administrador.
)

:fim
endlocal
pause
