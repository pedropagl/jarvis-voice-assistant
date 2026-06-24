"""
core/logger.py
--------------
Configuracao central de logs da JARVIS (FASE 1 + FASE 10).

Dois tipos de registro:
  - logs/interactions.log   : log legivel da aplicacao (boot, avisos, erros).
  - logs/interactions.jsonl : registro ESTRUTURADO de cada interacao (1 linha
                              JSON por pergunta), para auditoria e analise.

Use get_logger() para o log legivel e registrar_interacao() para o estruturado.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "interactions.log"
INTERACOES_FILE = LOG_DIR / "interactions.jsonl"

_configured = False


def get_logger(name: str = "jarvis") -> logging.Logger:
    """Devolve um logger configurado para console e arquivo.

    A configuracao acontece apenas uma vez por execucao.
    """
    global _configured

    if not _configured:
        LOG_DIR.mkdir(exist_ok=True)

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Saida no console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        # Saida em arquivo (append, utf-8)
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)

        root = logging.getLogger("jarvis")
        root.setLevel(logging.INFO)
        root.addHandler(console_handler)
        root.addHandler(file_handler)
        root.propagate = False

        _configured = True

    return logging.getLogger(name if name.startswith("jarvis") else f"jarvis.{name}")


# ---------------------------------------------------------------------------
# FASE 10 - registro estruturado de interacoes
# ---------------------------------------------------------------------------

def registrar_interacao(
    *,
    texto_falado: str = "",
    texto_transcrito: str = "",
    intencao: str = "",
    funcao_chamada=None,
    dados_consultados=None,
    resposta: str = "",
    erro=None,
    faltaram_dados: bool = False,
    modo: str = "",
) -> dict:
    """
    Grava UMA interacao da JARVIS em logs/interactions.jsonl (uma linha JSON).

    Campos registrados (todos os pedidos na Fase 10):
        timestamp          - data e hora (ISO)
        texto_falado       - o que o operador disse (transcricao bruta, com "Jarvis")
        texto_transcrito   - o comando efetivamente processado (sem a wake word)
        intencao           - intencao identificada
        funcao_chamada     - funcao do company_system acionada
        dados_consultados  - resumo factual dos dados retornados pelo sistema
        resposta           - resposta final gerada
        erro               - mensagem de erro, se houve (senao null)
        faltaram_dados     - True se faltou informacao para responder
        modo               - "gemini" ou "local"

    Tambem escreve um resumo legivel no log da aplicacao. Nunca levanta excecao
    (se nao conseguir gravar, apenas avisa) para nao derrubar o fluxo de voz.
    """
    registro = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "texto_falado": texto_falado,
        "texto_transcrito": texto_transcrito,
        "intencao": intencao,
        "funcao_chamada": funcao_chamada,
        "dados_consultados": dados_consultados,
        "resposta": resposta,
        "erro": erro,
        "faltaram_dados": faltaram_dados,
        "modo": modo,
    }

    log = get_logger("jarvis.interacao")
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(INTERACOES_FILE, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception as falha:  # noqa: BLE001 - log nunca deve quebrar o sistema
        log.warning("Falha ao gravar interacao estruturada: %s", falha)

    # Resumo legivel no interactions.log
    marcador = "ERRO" if erro else ("SEM_DADOS" if faltaram_dados else "OK")
    log.info("[%s] intencao=%s funcao=%s | %r -> %r",
             marcador, intencao, funcao_chamada, texto_transcrito, resposta)

    return registro
