"""
core/push.py
-------------
Notificacoes push do painel (PWA) — chegam no celular mesmo com o app
fechado, sem depender de WhatsApp/Telegram/e-mail.

Como funciona: quando alguem abre o painel no celular e autoriza notificacoes
(dashboard/app.js pede a permissao), o navegador gera uma "inscricao" (uma
URL + chaves de criptografia) e manda pro servidor via POST /api/push/subscribe.
A JARVIS guarda essa inscricao em data/push_subscriptions.json e, quando quer
avisar algo, manda a notificacao para TODAS as inscricoes guardadas usando o
protocolo Web Push (padrao do navegador, sem servico de terceiro).

As chaves VAPID (identificam a JARVIS perante os navegadores) ja vem
configuradas no .env — geradas uma vez, nao precisam de conta externa nenhuma.
Sem PUSH_VAPID_PUBLIC/PUSH_VAPID_PRIVATE_PEM configurados, o push fica
DESATIVADO e o resto do sistema continua normal.
"""

import json
import os
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.push")

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
SUBSCRICOES_PATH = RAIZ_PROJETO / "data" / "push_subscriptions.json"


def _config() -> dict:
    priv = os.getenv("PUSH_VAPID_PRIVATE_PEM", "").strip()
    if priv and not os.path.isabs(priv):
        priv = str(RAIZ_PROJETO / priv)
    return {
        "public": os.getenv("PUSH_VAPID_PUBLIC", "").strip(),
        "private_pem": priv,
        "contato_email": os.getenv("PUSH_VAPID_CONTATO_EMAIL", "").strip(),
    }


def disponivel() -> bool:
    c = _config()
    return bool(c["public"] and c["private_pem"] and c["contato_email"])


def chave_publica() -> str:
    """Chave publica VAPID, exposta pro front-end assinar a inscricao."""
    return _config()["public"]


def _ler_subscricoes() -> list[dict]:
    try:
        with open(SUBSCRICOES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - arquivo ainda nao existe ou esta corrompido
        return []


def _escrever_subscricoes(lista: list[dict]) -> None:
    try:
        SUBSCRICOES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SUBSCRICOES_PATH, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao gravar inscricoes de push: %s", erro)


def registrar_subscricao(subscricao: dict) -> None:
    """Guarda uma nova inscricao (chamado pelo endpoint /api/push/subscribe).
    Evita duplicata pelo endpoint (a URL unica de cada inscricao).
    """
    if not isinstance(subscricao, dict) or "endpoint" not in subscricao:
        return
    lista = _ler_subscricoes()
    if any(s.get("endpoint") == subscricao["endpoint"] for s in lista):
        return
    lista.append(subscricao)
    _escrever_subscricoes(lista)
    log.info("Nova inscricao de push registrada (total: %d).", len(lista))


def remover_subscricao(endpoint: str) -> None:
    lista = [s for s in _ler_subscricoes() if s.get("endpoint") != endpoint]
    _escrever_subscricoes(lista)


def enviar_notificacao(titulo: str, corpo: str) -> int:
    """
    Manda a notificacao para TODAS as inscricoes guardadas. Devolve quantas
    foram entregues com sucesso. Inscricoes que a Meta/navegador ja invalidou
    (ex.: usuario desinstalou o app) sao removidas automaticamente.
    """
    if not disponivel():
        log.debug("Push nao configurado; notificacao descartada: %s", titulo)
        return 0

    from pywebpush import webpush, WebPushException

    c = _config()
    lista = _ler_subscricoes()
    if not lista:
        return 0

    payload = json.dumps({"titulo": titulo, "corpo": corpo})
    validas = []
    entregues = 0

    for sub in lista:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=c["private_pem"],
                vapid_claims={"sub": f"mailto:{c['contato_email']}"},
            )
            validas.append(sub)
            entregues += 1
        except WebPushException as erro:
            codigo = getattr(erro.response, "status_code", None)
            if codigo in (404, 410):
                # Inscricao expirada/removida pelo usuario — descarta silenciosamente.
                continue
            log.warning("Falha ao entregar push: %s", erro)
            validas.append(sub)  # erro temporario: mantem pra tentar de novo depois
        except Exception as erro:  # noqa: BLE001 - push nunca derruba o sistema
            log.warning("Erro inesperado ao enviar push: %s", erro)
            validas.append(sub)

    if len(validas) != len(lista):
        _escrever_subscricoes(validas)

    return entregues
