"""
core/wake_word.py
-----------------
Deteccao da palavra de ativacao "Jarvis" (FASE 8).

Funcoes de TEXTO puro (sem audio) — faceis de testar:
    has_wake_word(texto)     -> True se a frase chama a JARVIS
    remove_wake_word(texto)  -> devolve so o comando, sem o "Jarvis,"

A deteccao ignora maiusculas/minusculas e acentos. Como a transcricao por voz
nem sempre acerta o nome, aceitamos algumas variacoes foneticas comuns.
"""

import re
import unicodedata

# Variacoes aceitas para a palavra de ativacao (a transcricao pode errar).
WAKE_WORDS = ("jarvis", "jarves", "jarvix", "jervis", "jarbis")


def _sem_acento(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def has_wake_word(texto: str) -> bool:
    """
    Retorna True se a frase contiver a palavra de ativacao.

    Ex.:
        has_wake_word("Jarvis, qual o status da M04?")  -> True
        has_wake_word("qual o status da M04?")          -> False
    """
    if not texto:
        return False
    limpo = _sem_acento(texto)
    for palavra in WAKE_WORDS:
        if re.search(rf"\b{palavra}\b", limpo):
            return True
    return False


def remove_wake_word(texto: str) -> str:
    """
    Remove a palavra de ativacao e a pontuacao logo apos ela, devolvendo
    apenas o comando.

    Ex.:
        remove_wake_word("Jarvis, qual o status da M04?")
            -> "qual o status da M04?"
        remove_wake_word("ok jarvis quais pecas faltam")
            -> "quais pecas faltam"

    Preserva os acentos/maiusculas do COMANDO (so a wake word e removida).
    Se nao houver wake word, devolve o texto original sem alteracao.
    """
    if not texto:
        return ""

    # Monta um padrao que casa qualquer variacao da wake word, com a pontuacao
    # imediatamente seguinte (virgula, ponto, dois-pontos) e espacos ao redor.
    alternativas = "|".join(WAKE_WORDS)
    padrao = re.compile(
        rf"\b({alternativas})\b[\s,.:;!?-]*",
        flags=re.IGNORECASE,
    )

    # Remove apenas a PRIMEIRA ocorrencia (a chamada), preservando o resto.
    # Comparamos sem acento para achar, mas removemos do texto original.
    # re.IGNORECASE ja cobre maiusculas; acentos nao aparecem em "jarvis".
    resultado = padrao.sub("", texto, count=1)

    return resultado.strip()
