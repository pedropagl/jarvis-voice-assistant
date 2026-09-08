@echo off
REM ===================================================================
REM  JARVIS - atalho de execucao
REM  Usa SEMPRE o Python do venv (onde estao todas as bibliotecas:
REM  openai/Kilo, edge-tts, resemblyzer, etc.). Evita o erro de rodar
REM  com o "python" da Windows Store, que nao tem as dependencias.
REM
REM  Uso (dentro da pasta do projeto):
REM     jarvis              -> demos (Fases 2 a 10)
REM     jarvis --mic        -> microfone ao vivo + biometria + voz
REM     jarvis --chat       -> conversa digitando
REM     jarvis --chat --voz -> digitando, com resposta em audio
REM     jarvis --cadastrar-voz -> cadastra a voz das pessoas do lab
REM     jarvis painel       -> abre o PAINEL (interface) com a caixa de pergunta
REM     jarvis mcp          -> conecta ao catos e lista as ferramentas (dados reais)
REM ===================================================================
setlocal
set "RAIZ=%~dp0"
set "PY=%RAIZ%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERRO] Nao encontrei o venv em "%PY%".
    echo Crie o ambiente e instale as dependencias antes de rodar.
    exit /b 1
)

REM Atalho do painel: roda o servidor com o Python do venv (LLM conecta) e
REM abre o navegador na interface.
if /I "%~1"=="painel" (
    echo Abrindo o painel da JARVIS em http://127.0.0.1:8000/dashboard/index.html
    start "" "http://127.0.0.1:8000/dashboard/index.html"
    "%PY%" "%RAIZ%dashboard\serve.py"
    goto :fim
)

"%PY%" "%RAIZ%main.py" %*
:fim
endlocal
