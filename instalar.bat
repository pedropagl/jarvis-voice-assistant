@echo off
REM ===================================================================
REM  instalar.bat - monta o ambiente do JARVIS em um PC novo
REM
REM  O que faz:
REM   1. Confere se o Python esta instalado
REM   2. Cria o ambiente virtual (.venv)
REM   3. Instala todas as dependencias na ordem certa
REM   4. Avisa se falta o arquivo .env
REM
REM  Uso: clique duas vezes neste arquivo (dentro da pasta jarvis_empresa)
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
cd /d "%RAIZ%"

echo.
echo  ===============================================
echo   JARVIS - instalacao do ambiente
echo  ===============================================
echo.

REM ---- 1) Confere Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale com: winget install Python.Python.3.13
    echo Depois feche e abra o terminal de novo e rode este arquivo outra vez.
    pause
    exit /b 1
)
echo [OK] Python encontrado:
python --version

REM ---- 2) Cria o venv (se ainda nao existir) ----
if exist "%RAIZ%.venv\Scripts\python.exe" (
    echo [OK] Ambiente virtual ja existe. Pulando criacao.
) else (
    echo.
    echo  Criando ambiente virtual em .venv ...
    python -m venv "%RAIZ%.venv"
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o venv.
        pause
        exit /b 1
    )
    echo [OK] Ambiente virtual criado.
)

set "PY=%RAIZ%.venv\Scripts\python.exe"
set "PIP=%RAIZ%.venv\Scripts\pip.exe"

echo.
echo  Atualizando pip...
"%PY%" -m pip install --upgrade pip

REM ---- 3) Instala dependencias, na ordem que importa ----
echo.
echo  [1/4] Basico + LLM + voz de saida...
"%PIP%" install python-dotenv==1.0.1 openai==2.43.0 google-generativeai==0.8.3
if errorlevel 1 goto :erro

"%PIP%" install SpeechRecognition==3.16.1 PyAudio==0.2.14
if errorlevel 1 goto :erro

"%PIP%" install edge-tts==7.2.8 pygame==2.6.1 pyttsx3==2.99
if errorlevel 1 goto :erro

echo.
echo  [2/4] MCP (dados reais do catos)...
"%PIP%" install mcp==1.18.0
if errorlevel 1 goto :erro

echo.
echo  [3/4] Biometria de voz (torch/librosa - pode demorar alguns minutos)...
"%PIP%" install numpy scipy librosa torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
if errorlevel 1 goto :erro

"%PIP%" install webrtcvad-wheels==2.0.14
if errorlevel 1 goto :erro

"%PIP%" install resemblyzer==0.1.4 --no-deps
if errorlevel 1 goto :erro

echo.
echo  [4/4] Verificando instalacao...
"%PY%" -c "import openai, edge_tts, mcp, resemblyzer, dotenv; print('[OK] Todas as bibliotecas principais importam corretamente.')"
if errorlevel 1 (
    echo [AVISO] Alguma biblioteca nao importou. Revise as mensagens acima.
)

REM ---- 4) Confere o .env ----
echo.
if exist "%RAIZ%.env" (
    echo [OK] Arquivo .env encontrado.
) else (
    echo [ATENCAO] Nao encontrei o arquivo .env nesta pasta.
    echo Copie o .env de outro PC ja configurado ^(nunca por e-mail/nuvem publica^)
    echo ou crie um novo com as chaves KILO_API_KEY e CATOS_MCP_TOKEN.
)

echo.
echo  ===============================================
echo   Instalacao concluida!
echo   Para rodar:
echo     jarvis.bat painel        -^> abre o painel
echo     jarvis_tunnel.bat        -^> painel + acesso externo
echo     jarvis.bat --mic         -^> microfone ao vivo
echo     jarvis.bat --chat        -^> conversa digitando
echo  ===============================================
echo.
pause
exit /b 0

:erro
echo.
echo [ERRO] Falha durante a instalacao de uma dependencia. Veja a mensagem acima.
pause
exit /b 1
