"""
core/llm_client.py
------------------
Abstracao do provedor de linguagem (LLM) da JARVIS.

A JARVIS pode falar com o modelo por 3 caminhos, escolhidos AUTOMATICAMENTE
pelo que estiver configurado no .env (sem mexer no codigo):

    1. KILO   -> gateway Kilo AI (compativel com a API da OpenAI).
                 Usa a biblioteca `openai` apontada para o endereco do Kilo.
                 Variaveis: KILO_API_KEY (obrigatoria), KILO_MODEL, KILO_BASE_URL.
    2. GEMINI -> Google Gemini direto (biblioteca google-generativeai).
                 Variaveis: GEMINI_API_KEY (obrigatoria), GEMINI_MODEL.
    3. LOCAL  -> nenhum provedor configurado: o cerebro usa o interpretador
                 deterministico (ver gemini_brain). Tudo continua funcionando offline.

Prioridade: KILO > GEMINI > LOCAL.

Este modulo expoe uma unica funcao de geracao, `gerar(prompt, json_mode, temperature)`,
que devolve o texto da resposta do modelo (ou None se nenhum provedor estiver ativo
ou se a chamada falhar). As CHAVES vem sempre do .env, nunca do codigo.
"""

import os

from core.logger import get_logger

log = get_logger("jarvis.llm")

# Padroes SEGUROS (baratos) caso o .env nao defina. NUNCA um modelo pesado/caro
# como padrao, para nao estourar o orcamento sem querer.
KILO_BASE_URL_PADRAO = "https://api.kilo.ai/api/gateway"
KILO_MODEL_PADRAO = "google/gemini-2.5-flash-lite"
GEMINI_MODEL_PADRAO = "gemini-2.0-flash"

# Estado (lazy + cache). IMPORTANTE: as variaveis do .env sao lidas dentro de
# _inicializar() (e nao no topo do modulo), porque o .env pode ainda nao ter
# sido carregado no momento do import.
_provedor = None          # "kilo" | "gemini" | None
_cliente = None           # objeto do cliente (OpenAI ou GenerativeModel)
_motivo_local = None      # por que caiu no modo local (para diagnostico)
_inicializado = False
_kilo_model = KILO_MODEL_PADRAO
_kilo_base = KILO_BASE_URL_PADRAO
_gemini_model = GEMINI_MODEL_PADRAO


def _inicializar():
    """Decide o provedor e inicializa o cliente uma unica vez."""
    global _provedor, _cliente, _motivo_local, _inicializado
    global _kilo_model, _kilo_base, _gemini_model
    if _inicializado:
        return
    _inicializado = True

    # Lê as configs do ambiente AGORA (apos o .env ter sido carregado).
    _kilo_base = os.getenv("KILO_BASE_URL", KILO_BASE_URL_PADRAO)
    _kilo_model = os.getenv("KILO_MODEL", KILO_MODEL_PADRAO)
    _gemini_model = os.getenv("GEMINI_MODEL", GEMINI_MODEL_PADRAO)

    chave_kilo = os.getenv("KILO_API_KEY", "").strip()
    chave_gemini = os.getenv("GEMINI_API_KEY", "").strip()

    # ---- 1) KILO (OpenAI-compativel) ----
    if chave_kilo:
        try:
            from openai import OpenAI
            _cliente = OpenAI(api_key=chave_kilo, base_url=_kilo_base)
            _provedor = "kilo"
            log.info("LLM conectado via KILO (modelo: %s, base: %s).", _kilo_model, _kilo_base)
            return
        except ImportError:
            _motivo_local = "KILO_API_KEY definida, mas a biblioteca 'openai' nao esta instalada (pip install openai)"
            log.warning("LLM em MODO LOCAL: %s.", _motivo_local)
            return
        except Exception as erro:  # noqa: BLE001
            _motivo_local = f"falha ao iniciar o cliente Kilo: {erro}"
            log.warning("LLM em MODO LOCAL: %s.", _motivo_local)
            return

    # ---- 2) GEMINI (Google) ----
    if chave_gemini:
        try:
            import google.generativeai as genai
            genai.configure(api_key=chave_gemini)
            _cliente = genai.GenerativeModel(_gemini_model)
            _provedor = "gemini"
            log.info("LLM conectado via GEMINI (modelo: %s).", _gemini_model)
            return
        except ImportError:
            _motivo_local = "GEMINI_API_KEY definida, mas a biblioteca 'google-generativeai' nao esta instalada"
            log.warning("LLM em MODO LOCAL: %s.", _motivo_local)
            return
        except Exception as erro:  # noqa: BLE001
            _motivo_local = f"falha ao iniciar o cliente Gemini: {erro}"
            log.warning("LLM em MODO LOCAL: %s.", _motivo_local)
            return

    # ---- 3) LOCAL ----
    _motivo_local = "nenhuma chave configurada (KILO_API_KEY / GEMINI_API_KEY)"
    log.info("LLM em MODO LOCAL: %s.", _motivo_local)


def provedor_ativo() -> str | None:
    """Devolve 'kilo', 'gemini' ou None (modo local)."""
    _inicializar()
    return _provedor


def disponivel() -> bool:
    """True se ha um provedor de LLM real ativo (Kilo ou Gemini)."""
    return provedor_ativo() is not None


def nome_modelo() -> str:
    """Nome do modelo em uso (para exibir/diagnostico)."""
    p = provedor_ativo()
    if p == "kilo":
        return f"kilo:{_kilo_model}"
    if p == "gemini":
        return f"gemini:{_gemini_model}"
    return "local"


def motivo_local() -> str | None:
    """Motivo de estar em modo local (None se ha provedor ativo)."""
    _inicializar()
    return _motivo_local


def gerar(prompt: str, json_mode: bool = False, temperature: float = 0.2) -> str | None:
    """
    Gera texto com o provedor ativo.

    Parametros:
        prompt      — texto enviado ao modelo.
        json_mode   — se True, pede que a resposta seja um objeto JSON.
        temperature — criatividade (0.0 = deterministico).

    Retorno:
        - str com o texto da resposta, ou
        - None se nao ha provedor ativo OU se a chamada falhou (o chamador,
          gemini_brain, cai no modo local nesses casos).
    """
    p = provedor_ativo()
    if p is None:
        return None

    try:
        if p == "kilo":
            return _gerar_kilo(prompt, json_mode, temperature)
        if p == "gemini":
            return _gerar_gemini(prompt, json_mode, temperature)
    except Exception as erro:  # noqa: BLE001 - degradar com seguranca
        log.warning("Falha na geracao via %s (%s).", p, erro)
        return None

    return None


def cliente_kilo():
    """
    Devolve (cliente_openai, nome_modelo) quando o provedor ativo e o Kilo, para
    uso em tool-calling (agente MCP). Devolve (None, None) em qualquer outro caso.

    Tool-calling exige uma API compativel com OpenAI; hoje so o Kilo oferece isso
    aqui. Com Gemini direto ou modo local, o agente MCP nao roda.
    """
    if provedor_ativo() == "kilo":
        return _cliente, _kilo_model
    return None, None


def _gerar_kilo(prompt: str, json_mode: bool, temperature: float) -> str | None:
    """Chamada ao gateway Kilo (API compativel com OpenAI)."""
    mensagens = [{"role": "user", "content": prompt}]
    kwargs = {"model": _kilo_model, "messages": mensagens, "temperature": temperature}

    # response_format=json_object force JSON; nem todo modelo suporta -> com fallback.
    if json_mode:
        try:
            resp = _cliente.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as erro:  # noqa: BLE001
            log.info("Kilo: response_format json nao aceito (%s); tentando sem.", erro)

    resp = _cliente.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def _gerar_gemini(prompt: str, json_mode: bool, temperature: float) -> str | None:
    """Chamada ao Gemini (google-generativeai)."""
    config = {"temperature": temperature}
    if json_mode:
        config["response_mime_type"] = "application/json"
    resp = _cliente.generate_content(prompt, generation_config=config)
    return (resp.text or "").strip()
