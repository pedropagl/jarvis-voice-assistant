"""
core/conhecimento.py
---------------------
Catalogo local de perguntas e respostas (FAQ) que a JARVIS usa ANTES de
chamar a API de LLM (Kilo/Gemini). Serve para:
  - responder perguntas comuns sem gastar chamada de API;
  - continuar respondendo essas mesmas perguntas mesmo se a API cair
    ou a chave nao estiver configurada (modo local).

Arquivo editavel em data/conhecimento.json, formato:
    [
      {"perguntas": ["quem e voce", "o que voce faz"], "resposta": "..."},
      ...
    ]

Casamento por palavras-chave (sem acento, minusculo) — nao precisa ser
uma frase identica, so ter palavras suficientes em comum com alguma das
variantes cadastradas.
"""

import json
import re
import unicodedata
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.conhecimento")

CATALOGO_PATH = Path(__file__).resolve().parent.parent / "data" / "conhecimento.json"

_LIMIAR_SIMILARIDADE = 0.5  # similaridade de Jaccard minima entre as duas frases

_catalogo_cache = None
_mtime_cache = None


def _sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalizar(texto: str) -> str:
    t = _sem_acento((texto or "").strip().lower())
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Palavras muito comuns que nao ajudam a identificar o assunto da pergunta.
# Sem isso, uma variante curta como "o que e o g7" sobrava so com "que" depois
# do filtro de tamanho, e "que" batia com QUALQUER pergunta que tivesse essa
# palavra (falso positivo com pontuacao 1.0).
_PARADAS = {
    "de", "do", "da", "em", "um", "no", "na", "ao", "aos", "as", "os", "eu",
    "se", "ou", "ja", "so", "la", "que", "para", "com", "como", "uma", "dos",
    "das", "por", "sao", "foi", "foram", "sera", "esta", "isso", "essa",
    "esse", "nos", "eles", "elas", "mais", "menos", "muito", "pouco", "tem",
    "ter", "sobre", "quando", "onde", "qual", "quais", "quanto", "quantos",
    "quantas", "quem",
}


def _singular(w: str) -> str:
    """Heuristica simples de plural em portugues (carros -> carro, faceis ->
    facil nao e tratado, mas o caso comum de 's' no final cobre a maioria).
    """
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _palavras(texto: str) -> set:
    return set(
        _singular(w) for w in _normalizar(texto).split(" ")
        if len(w) >= 2 and w not in _PARADAS
    )


def _carregar() -> list[dict]:
    """Le data/conhecimento.json (com cache; recarrega se o arquivo mudar)."""
    global _catalogo_cache, _mtime_cache
    try:
        mtime = CATALOGO_PATH.stat().st_mtime
    except OSError:
        return []

    if _catalogo_cache is not None and mtime == _mtime_cache:
        return _catalogo_cache

    try:
        with open(CATALOGO_PATH, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if not isinstance(dados, list):
            dados = []
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao ler catalogo de conhecimento: %s", erro)
        dados = []

    _catalogo_cache = dados
    _mtime_cache = mtime
    return dados


def buscar(pergunta: str) -> str | None:
    """
    Procura a pergunta no catalogo local. Devolve a resposta cadastrada se
    achar uma variante parecida o bastante, ou None se nao achar nada (ai
    quem chamou decide se cai pra API ou pra outra resposta padrao).
    """
    if not pergunta or not pergunta.strip():
        return None

    palavras_pergunta = _palavras(pergunta)
    if not palavras_pergunta:
        return None

    melhor_resposta = None
    melhor_pontuacao = 0.0

    for item in _carregar():
        resposta = item.get("resposta")
        variantes = item.get("perguntas") or []
        if not resposta or not variantes:
            continue

        for variante in variantes:
            palavras_variante = _palavras(variante)
            if not palavras_variante:
                continue
            # Similaridade de Jaccard (intersecao sobre uniao) em vez de so
            # "intersecao sobre a variante": isso evita que uma variante
            # curtinha (ex.: so a palavra "voce" depois de tirar as palavras
            # de parada) "vença" por coincidência com qualquer pergunta que
            # contenha essa unica palavra.
            uniao = palavras_pergunta | palavras_variante
            intersecao = palavras_pergunta & palavras_variante
            pontuacao = len(intersecao) / len(uniao) if uniao else 0.0
            if pontuacao > melhor_pontuacao:
                melhor_pontuacao = pontuacao
                melhor_resposta = resposta

    if melhor_pontuacao >= _LIMIAR_SIMILARIDADE:
        return melhor_resposta
    return None
