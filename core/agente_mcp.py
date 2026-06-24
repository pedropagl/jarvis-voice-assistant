"""
core/agente_mcp.py
------------------
Agente da JARVIS sobre o MCP do sistema (dados REAIS do sistema).

Quando o sistema esta conectado (ver core/mcp_client.py), a JARVIS deixa de usar
os JSONs simulados e passa a responder com os DADOS REAIS: tickets, status das
impressoras, producao pendente, agendamentos, fatiamentos, etc.

Como funciona (tool-calling / agente):
    1. Lista as ferramentas SOMENTE LEITURA do sistema (mcp_client).
    2. Entrega essas ferramentas ao LLM (Kilo, compativel com OpenAI).
    3. O LLM decide qual ferramenta chamar para responder a pergunta.
    4. A JARVIS executa a ferramenta (sempre validando que e de leitura) e
       devolve o resultado ao LLM, que entao redige a resposta final.

SEGURANCA:
    - So ferramentas de LEITURA sao expostas ao LLM (mcp_client.eh_somente_leitura).
    - Toda chamada passa por mcp_client.chamar_ferramenta, que tem trava dura:
      ferramenta de acao = bloqueada, mesmo que o LLM peca.
    - Pedido explicito de ACAO do operador (enviar/imprimir/alterar) e recusado
      antes de tudo (a JARVIS e SOMENTE LEITURA).
"""

import json

from core import llm_client, mcp_client
from core.logger import get_logger

log = get_logger("jarvis.agente")

# Limite de rodadas de tool-calling por pergunta (evita loop e estoura de custo).
_MAX_ITERACOES = 5

# Fallback quando o modelo devolve vazio (ex.: assunto bloqueado pelo provedor).
_RESPOSTA_VAZIA = "Nao consegui responder isso agora. Pode reformular ou perguntar de outro jeito?"

_SYSTEM_PROMPT = """Voce e a JARVIS, assistente de producao de um laboratorio de impressao 3D.
Voce esta conectada ao sistema real da empresa (sistema) e responde com DADOS REAIS.

Como agir:
- Para perguntas sobre tickets, producao pendente, status das impressoras,
  agendamentos ou fatiamentos, USE as ferramentas disponiveis para buscar o dado
  real. NUNCA invente numeros, nomes, status ou quantidades.
- SEJA PROATIVA: prefira chamar uma ferramenta a pedir esclarecimento. So peca
  esclarecimento se realmente nao houver nenhuma ferramenta aplicavel.
- Quando o operador perguntar "o que produzir", "o que fazer/imprimir agora",
  "qual a melhor/proxima peca", "o que esta pendente" ou algo do tipo, isso e um
  pedido pela PRODUCAO PENDENTE: chame listar_demandas_pendentes e responda
  apontando o(s) componente(s) com MAIOR quantidade pendente. Deixe claro que a
  ordem e por quantidade pendente (voce nao define prioridade comercial), mas
  NAO se recuse a responder — entregue o dado real.
- "produzir" ou pedidos curtos sobre fazer pecas tambem significam consultar a
  producao pendente (listar_demandas_pendentes), nao executar nada.
- Quando a pergunta mencionar um NOME DE PECA + uma MAQUINA (ex.: "Placa X na M04"),
  o operador quer saber se essa peca esta sendo impressa naquela maquina agora OU
  se esta agendada/pendente para ela. Chame obter_status_impressora para ver o job
  atual e listar_demandas_pendentes para ver se a peca esta na fila. Cruze os dois
  e responda com o que encontrou em cada fonte.
- Se varias ferramentas forem necessarias, chame-as em sequencia.
- Se a pergunta NAO for sobre a fabrica (conversa, conhecimento geral, conta de
  matematica), responda direto, sem ferramentas.
- Voce e SOMENTE LEITURA: nunca tente enviar para impressora, fatiar, agendar,
  alterar ou cancelar nada. Se o operador pedir uma acao dessas, explique com
  educacao que voce so consulta informacoes, nao executa acoes no sistema.

Estilo da resposta final: portugues do Brasil, tom falado, curto e direto (1 a 2
frases). Sem markdown, sem listas, sem emojis. Se um dado nao existir no sistema,
diga isso com naturalidade."""


def disponivel() -> tuple[bool, str]:
    """O agente roda se o sistema estiver conectado E o provedor for Kilo (tool-calling)."""
    ok, motivo = mcp_client.disponivel()
    if not ok:
        return False, motivo
    cliente, _ = llm_client.cliente_kilo()
    if cliente is None:
        return False, "tool-calling exige o provedor Kilo (KILO_API_KEY no .env)"
    return True, ""


def _tools_openai(ferramentas: list[dict]) -> list[dict]:
    """Converte as ferramentas de leitura do MCP no formato de tools da OpenAI."""
    tools = []
    for f in ferramentas:
        schema = f.get("input_schema") or {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": f["name"],
                "description": (f.get("description") or "")[:1024],
                "parameters": schema,
            },
        })
    return tools


def _normalizar_nome(nome_bruto: str, nomes_validos: set) -> str:
    """
    Normaliza o nome de ferramenta devolvido pelo modelo. O Gemini costuma
    prefixar com 'default_api.' (ex.: 'default_api.listar_impressoras').
    Devolve o nome real do sistema quando reconhecido.
    """
    if nome_bruto in nomes_validos:
        return nome_bruto
    curto = nome_bruto.split(".")[-1]  # tira 'default_api.' e afins
    if curto in nomes_validos:
        return curto
    return nome_bruto  # deixa o gate de seguranca decidir (bloqueia se invalido)


def _criar_com_retry(cliente, kwargs, tentativas: int = 3):
    """
    Chama o chat.completions.create com retry. O gateway/Gemini as vezes devolve
    finish_reason='error' sem conteudo (instabilidade transitoria com tool-calling);
    nesse caso tenta de novo. Devolve a 'choice' (choices[0]).
    """
    ultima = None
    for n in range(tentativas):
        resp = cliente.chat.completions.create(**kwargs)
        escolha = resp.choices[0]
        ultima = escolha
        tem_conteudo = bool(getattr(escolha.message, "content", None))
        tem_tools = bool(getattr(escolha.message, "tool_calls", None))
        if escolha.finish_reason != "error" and (tem_conteudo or tem_tools):
            return escolha
        log.info("Agente MCP: resposta vazia/erro (finish=%s), tentativa %d/%d.",
                 escolha.finish_reason, n + 1, tentativas)
    return ultima


def responder(pergunta: str,
              historico: list[dict] | None = None) -> tuple[str, dict]:
    """
    Responde a pergunta do operador usando os dados reais do sistema via tool-calling.
    Devolve (resposta_textual, entidades_extraidas_dict).
    Em caso de falha, levanta excecao (o chamador decide o fallback).

    `historico` — lista de mensagens anteriores no formato OpenAI
    ({"role": "user"|"assistant", "content": "..."}) para manter contexto
    entre turnos consecutivos. Injetadas entre o system prompt e a pergunta atual.
    """
    cliente, modelo = llm_client.cliente_kilo()
    if cliente is None:
        raise RuntimeError("provedor Kilo indisponivel para tool-calling")

    # So as ferramentas de leitura — e registra o conjunto liberado (trava de seguranca).
    ferramentas = mcp_client.ferramentas_de_leitura()
    nomes_validos = {f["name"] for f in ferramentas}
    mcp_client.registrar_liberadas(list(nomes_validos))
    tools = _tools_openai(ferramentas)

    log.info("Agente MCP: %d ferramentas de leitura disponiveis.", len(tools))

    mensagens = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if historico:
        mensagens.extend(historico)
    mensagens.append({"role": "user", "content": pergunta})

    # Rastreia entidades extraidas (quais ferramentas foram usadas)
    entidades = {"maquina": None, "ticket": None, "data": None, "peca": None,
                 "material": None, "cor": None, "intencao": "mcp_agente"}

    for iteracao in range(_MAX_ITERACOES):
        kwargs = {"model": modelo, "messages": mensagens, "temperature": 0.2}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        escolha = _criar_com_retry(cliente, kwargs)
        msg = escolha.message

        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            # Resposta final do modelo.
            return ((msg.content or "").strip() or _RESPOSTA_VAZIA, entidades)

        # Anexa a mensagem do assistente (com os tool_calls) ao historico.
        mensagens.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        # Executa cada ferramenta pedida (sempre validando leitura).
        for tc in tool_calls:
            # Alguns modelos (Gemini) prefixam o nome com 'default_api.'.
            # Normaliza para o nome real da ferramenta do sistema.
            nome = _normalizar_nome(tc.function.name, nomes_validos)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            # Extrai entidades a partir dos nomes e parâmetros das ferramentas chamadas.
            if "impressora" in nome or "status_impressora" in nome:
                entidades["maquina"] = args.get("printer") or args.get("impressora")
            if "ticket" in nome:
                entidades["ticket"] = args.get("ticket_codigo") or args.get("codigo_ticket")

            try:
                resultado = mcp_client.chamar_ferramenta(nome, args)
                log.info("Agente MCP: chamou '%s' (%d args).", nome, len(args))
            except mcp_client.FerramentaBloqueada as bloqueio:
                resultado = f"BLOQUEADO: {bloqueio}"
                log.warning("Agente MCP: %s", bloqueio)
            except Exception as erro:  # noqa: BLE001
                resultado = f"ERRO ao chamar a ferramenta: {erro}"
                log.warning("Agente MCP: falha em '%s': %s", nome, erro)

            mensagens.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": resultado or "(sem conteudo)",
            })

    # Esgotou as iteracoes sem resposta final: faz uma ultima sem ferramentas.
    resp = cliente.chat.completions.create(
        model=modelo, messages=mensagens, temperature=0.2
    )
    return ((resp.choices[0].message.content or "").strip() or _RESPOSTA_VAZIA, entidades)
