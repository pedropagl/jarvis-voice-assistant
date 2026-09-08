"""
core/whatsapp.py
------------------
Alertas criticos da JARVIS via WhatsApp (API oficial da Meta - Cloud API).

Configuracao no .env:
    WHATSAPP_TOKEN     -> token de acesso do app no Meta for Developers
    WHATSAPP_PHONE_ID  -> ID do numero de telefone comercial (phone_number_id)
    WHATSAPP_TO        -> numero de destino, formato internacional sem
                           simbolos (ex.: 5562999999999)

Sem essas 3 variaveis configuradas, os alertas ficam DESATIVADOS e o
restante do sistema continua funcionando normalmente (nunca derruba nada).

COMO CONSEGUIR AS CREDENCIAIS (a JARVIS nao pode criar isso sozinha —
precisa ser feito uma vez, manualmente, no site da Meta):
    1. Criar um app em https://developers.facebook.com/apps
    2. Adicionar o produto "WhatsApp" ao app.
    3. Em WhatsApp > Introducao, pegar o "Temporary access token" (vale 24h,
       para testar) ou gerar um token PERMANENTE em System Users (producao).
    4. Copiar o "Phone number ID" que aparece na mesma tela.
    5. O numero de destino (WHATSAPP_TO) precisa ter mandado uma mensagem
       para o numero comercial nas ultimas 24h, OU usar um template
       pre-aprovado pela Meta (a API bloqueia texto livre fora dessa janela
       de 24h — restricao da propria Meta, nao da JARVIS).
"""

import os

import requests

from core.logger import get_logger

log = get_logger("jarvis.whatsapp")

API_VERSAO = "v20.0"


def _config() -> dict:
    return {
        "token": os.getenv("WHATSAPP_TOKEN", "").strip(),
        "phone_id": os.getenv("WHATSAPP_PHONE_ID", "").strip(),
        "to": os.getenv("WHATSAPP_TO", "").strip(),
    }


def disponivel() -> bool:
    """True se as 3 variaveis do WhatsApp estao configuradas no .env."""
    c = _config()
    return bool(c["token"] and c["phone_id"] and c["to"])


def enviar_alerta(mensagem: str) -> bool:
    """
    Envia uma mensagem de texto para o numero configurado. Devolve True se a
    Meta aceitou o envio, False em qualquer outro caso (nunca levanta
    excecao — um alerta que falha nao pode derrubar o resto do sistema).
    """
    c = _config()
    if not disponivel():
        log.debug("WhatsApp nao configurado; alerta descartado: %s", mensagem)
        return False

    url = f"https://graph.facebook.com/{API_VERSAO}/{c['phone_id']}/messages"
    headers = {
        "Authorization": f"Bearer {c['token']}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": c["to"],
        "type": "text",
        "text": {"body": f"🤖 JARVIS — {mensagem}"},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code >= 400:
            log.warning(
                "WhatsApp recusou o envio (HTTP %s): %s",
                resp.status_code, resp.text[:300],
            )
            return False
        return True
    except Exception as erro:  # noqa: BLE001 - alerta nunca derruba o sistema
        log.warning("Falha ao enviar alerta por WhatsApp: %s", erro)
        return False
