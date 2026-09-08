"""
core/alexa.py
-------------
Ponte entre a Alexa (Amazon Echo) e a JARVIS.

A Alexa nao fala direto com a JARVIS: ela manda um JSON no formato do
Alexa Skills Kit (ASK) para um endpoint HTTPS, espera um JSON de resposta,
e le o texto de "outputSpeech" em voz alta.

Este modulo:
    1. Recebe o request da Alexa (ja como dict).
    2. Valida que o pedido veio da NOSSA skill (applicationId) e e recente
       (timestamp) — barra pedidos de outras skills e replays antigos.
    3. Extrai a pergunta (slot de texto livre) e chama gemini_brain.processar.
    4. Devolve a resposta no formato que a Alexa entende.

SEGURANCA / ESCOPO (v1):
    - SOMENTE CONSULTA. A Alexa nao identifica quem esta falando (sem biometria)
      e confirmar acao por voz e facil de disparar sem querer, entao as 4 acoes
      de producao ficam DESLIGADAS por este canal (permitir_acoes=False). O
      operador vai pro log como "alexa (nao identificado)".
    - Validacao COMPLETA de assinatura (SignatureCertChainUrl) NAO esta
      implementada — suficiente para skill em modo de desenvolvimento (privada,
      so nos seus proprios dispositivos). ANTES de publicar/certificar a skill,
      adicionar a verificacao de assinatura do request.
"""

import os
from datetime import datetime, timezone

from core.logger import get_logger

log = get_logger("jarvis.alexa")

# ID da nossa skill (Alexa Developer Console -> Endpoint -> "Your Skill ID").
# Se vazio, a validacao de applicationId fica DESLIGADA (util so em teste).
_SKILL_ID = os.getenv("ALEXA_SKILL_ID", "").strip()

# Nome do intent que carrega a pergunta em texto livre (definido no modelo de
# interacao da skill). Ex.: "PerguntarIntent" com um slot "texto".
_INTENT_PERGUNTA = os.getenv("ALEXA_INTENT_PERGUNTA", "PerguntarIntent").strip()
_SLOT_TEXTO = os.getenv("ALEXA_SLOT_TEXTO", "texto").strip()

# Janela maxima do timestamp do request (ASK recomenda 150s).
_TOLERANCIA_S = 150

_BOAS_VINDAS = ("Oi, aqui e a JARVIS. Pode perguntar sobre a producao, "
                "as impressoras ou os tickets.")
_NAO_ENTENDI = "Nao entendi. Pode repetir a pergunta?"
_ERRO = "Tive um problema ao consultar agora. Tenta de novo daqui a pouco."


def _resposta(texto: str, encerrar: bool = True, reprompt: str | None = None) -> dict:
    """Monta o JSON de resposta no formato do Alexa Skills Kit."""
    corpo = {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": texto},
            "shouldEndSession": encerrar,
        },
    }
    if reprompt:
        corpo["response"]["reprompt"] = {
            "outputSpeech": {"type": "PlainText", "text": reprompt}
        }
    return corpo


def _skill_id_do_request(req: dict) -> str:
    try:
        return (req.get("context", {}).get("System", {})
                .get("application", {}).get("applicationId", "")) or ""
    except Exception:  # noqa: BLE001
        return ""


def _timestamp_valido(req: dict) -> bool:
    """True se o timestamp do request esta dentro da tolerancia (anti-replay)."""
    ts = (req.get("request", {}) or {}).get("timestamp")
    if not ts:
        return False
    try:
        # Ex.: "2026-07-07T12:34:56Z"
        momento = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    delta = abs((datetime.now(timezone.utc) - momento).total_seconds())
    return delta <= _TOLERANCIA_S


def processar_request(req: dict) -> tuple[dict, int]:
    """
    Recebe o dict do request da Alexa e devolve (corpo_resposta, status_http).
    status 200 sempre que a resposta for valida para a Alexa; outros codigos
    apenas para pedidos rejeitados (skill errada / replay).
    """
    # 1) Skill correta?
    if _SKILL_ID:
        if _skill_id_do_request(req) != _SKILL_ID:
            log.warning("alexa: applicationId nao confere — pedido recusado")
            return {"erro": "applicationId invalido"}, 403

    # 2) Timestamp recente? (anti-replay). So exige se veio timestamp.
    if (req.get("request", {}) or {}).get("timestamp") and not _timestamp_valido(req):
        log.warning("alexa: timestamp fora da janela — pedido recusado")
        return {"erro": "timestamp invalido"}, 400

    tipo = (req.get("request", {}) or {}).get("type", "")

    # LaunchRequest: usuario abriu a skill sem perguntar nada ("abrir jarvis").
    if tipo == "LaunchRequest":
        return _resposta(_BOAS_VINDAS, encerrar=False,
                         reprompt="Pode perguntar. O que voce quer saber?"), 200

    # SessionEndedRequest: nada a responder.
    if tipo == "SessionEndedRequest":
        return _resposta("", encerrar=True), 200

    if tipo != "IntentRequest":
        return _resposta(_NAO_ENTENDI, encerrar=False), 200

    intent = (req.get("request", {}) or {}).get("intent", {}) or {}
    nome_intent = intent.get("name", "")

    # Intents nativos da Amazon (parar/cancelar/ajuda).
    if nome_intent in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
        return _resposta("Ate mais.", encerrar=True), 200
    if nome_intent in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent"):
        return _resposta(_BOAS_VINDAS, encerrar=False,
                         reprompt="O que voce quer saber?"), 200

    # Nosso intent de pergunta livre.
    if nome_intent == _INTENT_PERGUNTA:
        slots = intent.get("slots", {}) or {}
        slot = slots.get(_SLOT_TEXTO, {}) or {}
        pergunta = (slot.get("value") or "").strip()
        if not pergunta:
            return _resposta(_NAO_ENTENDI, encerrar=False,
                             reprompt="Pode repetir a pergunta?"), 200
        try:
            from core import gemini_brain
            # operador identifica a ORIGEM no log (nao ha biometria na Alexa).
            resultado = gemini_brain.processar(
                pergunta, operador="alexa (nao identificado)", permitir_acoes=False)
            texto = (resultado.get("resposta") or "").strip() or _NAO_ENTENDI
        except Exception as erro:  # noqa: BLE001
            log.error("alexa: erro ao processar '%s': %s", pergunta, erro)
            texto = _ERRO
        # Encerra a sessao apos responder (pergunta unica). Para perguntar de
        # novo, o operador diz a wake word da skill outra vez.
        return _resposta(texto, encerrar=True), 200

    return _resposta(_NAO_ENTENDI, encerrar=False), 200
