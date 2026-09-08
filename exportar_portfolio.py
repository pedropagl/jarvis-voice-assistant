"""
exportar_portfolio.py
---------------------
Cria uma copia anonimizada do projeto em ../jarvis_portfolio/
para uso em portfolio publico no GitHub.

O projeto original (jarvis_empresa/) NAO e tocado.

Como usar:
    python exportar_portfolio.py

O que faz:
    1. Copia os arquivos do projeto (excluindo .venv, __pycache__, dados sensiveis)
    2. Substitui referencias especificas da empresa por nomes genericos
    3. Cria .env.example sem credenciais reais
    4. Substitui data/ por versao de demonstracao (dados ficticios)
"""

import os
import re
import shutil
from pathlib import Path

ORIGEM = Path(__file__).resolve().parent
DESTINO = ORIGEM.parent / "jarvis_portfolio"

# ------------------------------------------------------------------ #
# Arquivos/pastas que NAO copiar
# ------------------------------------------------------------------ #
EXCLUIR_DIRS = {
    ".venv", "__pycache__", ".git", "node_modules",
    "voiceprints",       # dados biometricos
    "logs",
}
EXCLUIR_ARQUIVOS = {
    ".env",              # credenciais reais
    "voiceprints.json",  # biometria
    "exportar_portfolio.py",  # este script
}
EXCLUIR_EXTENSOES = {".jsonl", ".pyc", ".pyo"}

# ------------------------------------------------------------------ #
# Substituicoes de texto nos arquivos copiados
# ------------------------------------------------------------------ #
SUBSTITUICOES = [
    # IP interno
    (r"192\.168\.148\.19:18080", "SEU_SERVIDOR:18080"),
    # Nome da pessoa de contato
    (r"\bGabriel\b", "administrador"),
    # Comentarios com nome da empresa (so em comentarios Python/JS)
    (r"(#.*?)Odilon Santos", r"\1empresa"),
    (r"(#.*?)GOS\b", r"\1empresa"),
    # Clientes ficticios nos dados de demo (tratados separadamente)
]

# Extensoes de arquivo onde aplicar substituicoes de texto
EXTENSOES_TEXTO = {
    ".py", ".md", ".txt", ".json", ".html", ".js", ".css",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env",
}

# ------------------------------------------------------------------ #
# Conteudo do .env.example
# ------------------------------------------------------------------ #
ENV_EXAMPLE = """\
# Copie este arquivo para .env e preencha com suas credenciais reais.

# --- LLM (Kilo AI / OpenAI-compatible) ---
KILO_API_KEY=sua_chave_api_aqui
KILO_BASE_URL=https://api.kilo.ai/v1   # ou outro endpoint OpenAI-compatible
KILO_MODEL=google/gemini-2.0-flash-001  # modelo padrao

# --- Sistema de producao (MCP) ---
CATOS_MCP_URL=http://SEU_SERVIDOR:18080/mcp/
CATOS_MCP_TOKEN=seu_token_mcp_aqui

# --- Dashboard web ---
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000

# --- Voz (opcional) ---
# TTS_ENGINE=pyttsx3   # pyttsx3 (offline) ou elevenlabs
# ELEVENLABS_API_KEY=sua_chave_elevenlabs
"""

# ------------------------------------------------------------------ #
# Dados de demonstracao para data/
# ------------------------------------------------------------------ #
DEMO_DASHBOARD_STATE = """\
{
  "_demo": true,
  "_aviso": "Este arquivo e gerado em tempo real pelo JARVIS. Os dados aqui sao de demonstracao.",
  "maquinas": [],
  "tickets": [],
  "alertas": [],
  "main_response": "JARVIS inicializado. Faca uma pergunta para comecar.",
  "operador": "Demo",
  "status": "pronto"
}
"""

# ------------------------------------------------------------------ #
# Funcoes auxiliares
# ------------------------------------------------------------------ #

def deve_excluir(caminho: Path) -> bool:
    for parte in caminho.parts:
        if parte in EXCLUIR_DIRS:
            return True
    if caminho.name in EXCLUIR_ARQUIVOS:
        return True
    if caminho.suffix in EXCLUIR_EXTENSOES:
        return True
    return False


def aplicar_substituicoes(texto: str) -> str:
    for padrao, substituto in SUBSTITUICOES:
        texto = re.sub(padrao, substituto, texto)
    return texto


def copiar_arquivo(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.suffix in EXTENSOES_TEXTO:
        try:
            conteudo = src.read_text(encoding="utf-8")
            conteudo = aplicar_substituicoes(conteudo)
            dst.write_text(conteudo, encoding="utf-8")
            return
        except UnicodeDecodeError:
            pass  # binario — copia direto

    shutil.copy2(src, dst)


def criar_dados_demo(destino_data: Path) -> None:
    destino_data.mkdir(parents=True, exist_ok=True)
    (destino_data / "dashboard_state.json").write_text(
        DEMO_DASHBOARD_STATE, encoding="utf-8"
    )
    # Arquivo de voiceprints vazio (estrutura sem dados reais)
    (destino_data / "voiceprints.json").write_text("{}", encoding="utf-8")


# ------------------------------------------------------------------ #
# Execucao principal
# ------------------------------------------------------------------ #

def main() -> None:
    if DESTINO.exists():
        resposta = input(f"\nA pasta '{DESTINO.name}' ja existe. Sobrescrever? [s/N] ").strip().lower()
        if resposta != "s":
            print("Cancelado.")
            return
        shutil.rmtree(DESTINO)

    print(f"\nExportando para: {DESTINO}")
    copiados = 0
    ignorados = 0

    for src in ORIGEM.rglob("*"):
        if src.is_dir():
            continue

        relativo = src.relative_to(ORIGEM)

        if deve_excluir(relativo):
            ignorados += 1
            continue

        # Pasta data/ e substituida por versao de demo
        if relativo.parts[0] == "data":
            ignorados += 1
            continue

        dst = DESTINO / relativo
        copiar_arquivo(src, dst)
        copiados += 1

    # Cria arquivos especiais
    (DESTINO / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    criar_dados_demo(DESTINO / "data")

    # .gitignore robusto
    gitignore = """\
.env
.env.*
!.env.example
.venv/
__pycache__/
*.pyc
*.pyo
voiceprints/
data/dashboard_state.json
data/voiceprints.json
logs/
*.jsonl
*.log
"""
    (DESTINO / ".gitignore").write_text(gitignore, encoding="utf-8")

    print(f"  Arquivos copiados : {copiados}")
    print(f"  Arquivos ignorados: {ignorados}")
    print(f"\nPronto! Proximos passos:")
    print(f"  1. Revise a pasta: {DESTINO}")
    print(f"  2. Verifique se o README.md esta ok")
    print(f"  3. git init && git add . && git commit -m 'JARVIS portfolio'")
    print(f"  4. Crie o repo no GitHub e faca o push")
    print(f"\nO projeto original em jarvis_empresa/ nao foi alterado.")


if __name__ == "__main__":
    main()
