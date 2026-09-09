"""
core/agente_mcp.py
------------------
Agente da JARVIS sobre o MCP do catos (dados REAIS do sistema).

Quando o catos esta conectado (ver core/mcp_client.py), a JARVIS deixa de usar
os JSONs simulados e passa a responder com os DADOS REAIS: tickets, status das
impressoras, producao pendente, agendamentos, fatiamentos, etc.

Como funciona (tool-calling / agente):
    1. Lista as ferramentas SOMENTE LEITURA do catos (mcp_client).
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
import re
import unicodedata

from core import llm_client, mcp_client, memoria
from core.logger import get_logger

log = get_logger("jarvis.agente")

# Ferramenta SINTETICA (nao existe no catos — resolvida localmente em Python).
# Cruza, de forma DETERMINISTICA e por GUID exato, o material carregado numa
# impressora com as pecas pendentes. Existe porque o LLM erra esse cruzamento:
# ignora que um fatiamento usa DOIS materiais (extrusor 0 e 1), agrupa peca sob
# o material errado e ate inventa compatibilidade. Aqui o casamento e exato.
_TOOL_PECAS_IMPRESSORA = "pecas_compativeis_na_impressora"

# Ferramentas de MEMORIA DE LONGO PRAZO (core/memoria.py) — a propria LLM
# decide o que vale a pena guardar (preferencias, decisoes, contexto
# recorrente) e pode buscar isso depois, mesmo em outra sessao/reinicio.
_TOOL_LEMBRAR = "lembrar_fato"
_TOOL_BUSCAR_FATOS = "buscar_fatos"

# Limite de rodadas de tool-calling por pergunta (evita loop e estoura de custo).
_MAX_ITERACOES = 5

# Fallback quando o modelo devolve vazio (ex.: assunto bloqueado pelo provedor).
_RESPOSTA_VAZIA = "Nao consegui responder isso agora. Pode reformular ou perguntar de outro jeito?"

_SYSTEM_PROMPT = """Voce e a JARVIS, assistente de producao de um laboratorio de impressao 3D.
Voce esta conectada ao sistema real da empresa (catos) e responde com DADOS REAIS.

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
- Quando o operador perguntar QUAL PECA DA PRA FAZER numa impressora especifica
  "sem trocar o material/filamento" (ex.: "o que da pra fazer na M08 sem trocar o
  material?", "qual peca posso imprimir na M05 sem trocar filamento?"), use a
  ferramenta pecas_compativeis_na_impressora(maquina=<Mxx>). Ela ja faz o cruzamento
  exato (material carregado x pecas pendentes, considerando os dois extrusores).
  NUNCA pergunte qual material esta carregado e NAO cruze na mao.
  Responda assim, com base no retorno:
  * Se "pendentes_compativeis" tiver itens: diga as pecas (campo "peca") e quanto
    falta, de forma curta. Ex.: "Na M05, com APEX Preto da pra fazer a CAPA PROT
    ALAVANCA (faltam 12)."
  * Se "pendentes_compativeis" vier VAZIO: diga claramente que NAO ha peca pendente
    que caiba no material atual, citando o que esta carregado. NAO invente peca.
  NUNCA liste nomes de arquivo .ufp na resposta (o campo "outros_prontos" e so
  contexto interno). So pergunte se a MAQUINA nao tiver sido dita.
- Se varias ferramentas forem necessarias, chame-as em sequencia.
- Se a pergunta NAO for sobre a fabrica (conversa, conhecimento geral, conta de
  matematica), responda direto, sem ferramentas.
- Voce (neste modulo) e SOMENTE LEITURA: nunca tente enviar para impressora,
  fatiar, agendar, alterar ou cancelar nada por conta propria. MAS existe um
  fluxo de acao com confirmacao para 4 operacoes (enviar para impressora,
  agendar, mudar agendamento, registrar resultado). Se o operador disser so o
  nome/descricao de uma peca, sem deixar claro se quer consultar ou agir,
  NAO recuse de cara: pergunte objetivamente o que ele quer, ex.: "Quer que eu
  inicie a producao dessa peca, ou so quer saber o status dela?" Se ele deixar
  claro que quer uma ACAO real (iniciar, mandar imprimir, agendar, etc.), diga
  para ele repetir o pedido com essa palavra (ex.: "diga 'inicia a producao
  dessa peca' que eu preparo e peco sua confirmacao"), pois so assim o pedido
  entra no fluxo de acao com confirmacao. NUNCA finja executar a acao aqui.
- MEMORIA DE LONGO PRAZO: voce tem a ferramenta lembrar_fato para guardar
  permanentemente algo que vale a pena lembrar em conversas futuras — uma
  preferencia de operador, uma decisao tomada, um contexto recorrente do lab
  (ex.: "a M04 costuma ser reservada pro Pedro", "o ticket X e prioridade alta").
  Use-a PROATIVAMENTE quando notar algo assim, sem precisar que o operador peca
  "lembra disso" explicitamente — mas so guarde fatos genuinamente uteis para o
  futuro, nao cada pergunta trivial. Use buscar_fatos quando precisar checar algo
  que pode ja ter sido guardado antes (ex.: preferencia de alguem, decisao antiga).
  Abaixo, na secao "MEMORIA DE LONGO PRAZO", ja estao os fatos mais recentes —
  consulte-os primeiro antes de decidir se vale chamar buscar_fatos de novo.

Estilo da resposta final: portugues do Brasil, tom falado, curto e direto (1 a 2
frases). Sem markdown, sem listas, sem emojis. Se um dado nao existir no sistema,
diga isso com naturalidade."""


def _norm(texto: str) -> str:
    """Normaliza nome para comparacao: sem acento, minusculo, sem extensao/prefixo tecnico."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"\.ufp$", "", t)
    t = re.sub(r"^(um[sf]?\d+_|auto_\d+_|\d+x?_)+", "", t)  # tira UMS7_, AUTO_2026..., 3x_
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return t


def _pecas_compativeis_na_impressora(maquina: str) -> str:
    """
    Cruzamento DETERMINISTICO: dada uma impressora, devolve quais pecas PENDENTES
    dao pra imprimir com o material JA carregado nela (sem trocar filamento).

    Regra correta (que o LLM erra): um fatiamento usa ate DOIS materiais (extrusor 0
    e 1). So da pra imprimir sem troca se TODOS os materiais nao-nulos do fatiamento
    estiverem carregados na impressora. O casamento de material e por GUID exato.
    A peca pendente e ligada ao fatiamento pelo nome (componente_codigo vem vazio).
    """
    from core.painel import _parse_lista_json
    try:
        det = json.loads(mcp_client.chamar_ferramenta("obter_status_impressora", {"printer": maquina}))
    except Exception as err:  # noqa: BLE001
        return json.dumps({"ok": False, "erro": f"nao consegui ler a {maquina}: {err}"}, ensure_ascii=False)

    extrusores = (det.get("material_active") or {}).get("extruders") or []
    carregados = {e.get("guid"): e.get("label") for e in extrusores
                  if isinstance(e, dict) and e.get("guid")}
    if not carregados:
        return json.dumps({"ok": True, "maquina": maquina, "material_carregado": [],
                           "pendentes_compativeis": [], "outros_prontos": [],
                           "obs": "nenhum material identificado na impressora"}, ensure_ascii=False)

    try:
        dem = _parse_lista_json(mcp_client.chamar_ferramenta("listar_demandas_pendentes", {}))
    except Exception:  # noqa: BLE001
        dem = []
    dem_norm = [(_norm(d.get("name")), d.get("name"), d.get("quantidade_pendente")) for d in dem]

    try:
        fat = json.loads(mcp_client.chamar_ferramenta("listar_fatiamentos", {})).get("fatiamentos", [])
    except Exception:  # noqa: BLE001
        fat = []

    pendentes: list[dict] = []     # demandas pendentes que dao pra fazer sem troca
    outros_prontos: list[str] = [] # fatiamentos ativos que cabem mas sem demanda na fila
    vistos_dem = set()
    for f in fat:
        if not f.get("is_active"):
            continue
        req = [(f.get("material_0_guid"), f.get("material_0_label")),
               (f.get("material_1_guid"), f.get("material_1_label"))]
        req = [(g, l) for g, l in req if g]
        if not req or not all(g in carregados for g, _ in req):
            continue  # exige TODOS os materiais do fatiamento carregados
        mats = [l for _, l in req]
        comps = [c.get("name") for c in (f.get("componentes_preview") or []) if c.get("name")]
        casou = False
        for cnome in comps:
            cn = _norm(cnome)
            for dn, dorig, qtd in dem_norm:
                if not cn or not dn:
                    continue
                ov = {w for w in cn.split() if len(w) >= 4} & {w for w in dn.split() if len(w) >= 4}
                if cn in dn or dn in cn or len(ov) >= 2:
                    if dorig not in vistos_dem:
                        vistos_dem.add(dorig)
                        pendentes.append({"peca": dorig, "faltam": qtd, "material": mats})
                    casou = True
                    break
        if not casou:
            outros_prontos.append(f.get("filename"))

    return json.dumps({
        "ok": True,
        "maquina": maquina,
        "material_carregado": list(carregados.values()),
        "pendentes_compativeis": pendentes,        # PRIORIDADE: peca na fila + cabe no material
        "outros_prontos": outros_prontos[:8],      # fatiamentos que cabem mas nao estao na fila
    }, ensure_ascii=False)


def disponivel() -> tuple[bool, str]:
    """O agente roda se o catos estiver conectado E o provedor for Kilo (tool-calling)."""
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
    Devolve o nome real do catos quando reconhecido.
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
    Responde a pergunta do operador usando os dados reais do catos via tool-calling.
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

    # Ferramenta SINTETICA: cruzamento deterministico impressora -> pecas pendentes
    # compativeis com o material carregado (resolvida localmente, nao vai ao catos).
    nomes_validos.add(_TOOL_PECAS_IMPRESSORA)
    tools.append({
        "type": "function",
        "function": {
            "name": _TOOL_PECAS_IMPRESSORA,
            "description": (
                "Dada UMA impressora (ex.: 'M08'), devolve quais pecas PENDENTES dao "
                "pra imprimir com o material JA carregado nela, sem trocar filamento. "
                "Faz o cruzamento exato por GUID (considerando os dois extrusores). "
                "USE ESTA FERRAMENTA sempre que o operador perguntar 'o que da pra fazer "
                "na Mxx sem trocar o material/filamento'. CONFIE no campo "
                "'pendentes_compativeis': se vier vazio, NAO ha peca na fila que caiba no "
                "material atual (mesmo que existam outros fatiamentos prontos)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "maquina": {"type": "string", "description": "Codigo da impressora, ex.: M08, M05."}
                },
                "required": ["maquina"],
            },
        },
    })

    # Ferramentas de MEMORIA DE LONGO PRAZO (core/memoria.py) — sinteticas,
    # nao vao ao catos.
    nomes_validos.add(_TOOL_LEMBRAR)
    tools.append({
        "type": "function",
        "function": {
            "name": _TOOL_LEMBRAR,
            "description": (
                "Guarda um fato para lembrar em conversas futuras (mesmo apos "
                "reiniciar) — preferencia de um operador, decisao tomada, contexto "
                "recorrente do lab. Use com moderacao: so fatos genuinamente uteis "
                "pro futuro, nao cada pergunta trivial. Se o fato tiver PRAZO/validade "
                "(ex.: 'M04 reservada por 2 semanas', 'peca urgente ate sexta'), "
                "preencha validade_dias para ele expirar sozinho — nao fique guardado "
                "pra sempre coisa que so vale por um tempo."),
            "parameters": {
                "type": "object",
                "properties": {
                    "fato": {"type": "string", "description": "O fato a guardar, em uma frase clara e autocontida."},
                    "validade_dias": {
                        "type": "integer",
                        "description": "Opcional: em quantos dias esse fato deixa de valer/ser verdade. Omita para fatos permanentes.",
                    },
                },
                "required": ["fato"],
            },
        },
    })
    nomes_validos.add(_TOOL_BUSCAR_FATOS)
    tools.append({
        "type": "function",
        "function": {
            "name": _TOOL_BUSCAR_FATOS,
            "description": (
                "Busca fatos guardados anteriormente na memoria de longo prazo, por "
                "palavra-chave. Use quando suspeitar que algo relevante ja foi "
                "guardado antes (preferencia de alguem, decisao antiga) e isso nao "
                "aparece na secao MEMORIA DE LONGO PRAZO do prompt."),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Palavras-chave para buscar."}
                },
                "required": ["consulta"],
            },
        },
    })

    log.info("Agente MCP: %d ferramentas disponiveis (inclui sinteticas).", len(tools))

    # Memoria "ambiente": os fatos de longo prazo mais recentes ja entram no
    # prompt, sem precisar de uma chamada de ferramenta so pra descobrir o que
    # a JARVIS ja sabe.
    fatos_recentes = memoria.listar_fatos_recentes()
    prompt_sistema = _SYSTEM_PROMPT
    if fatos_recentes:
        bloco_fatos = "\n".join(f"- {f}" for f in fatos_recentes)
        prompt_sistema += f"\n\nMEMORIA DE LONGO PRAZO (fatos guardados antes):\n{bloco_fatos}"

    mensagens = [{"role": "system", "content": prompt_sistema}]
    if historico:
        mensagens.extend(historico)
    mensagens.append({"role": "user", "content": pergunta})

    # Pergunta do tipo "o que da pra fazer na Mxx sem trocar material/filamento"?
    # Nesse caso FORCAMOS a ferramenta deterministica na 1a rodada — o LLM as vezes
    # ignora a ferramenta e despeja fatiamentos crus / erra o casamento de material.
    forcar_pecas = bool(
        re.search(r"\bm-?0?\d{1,2}\b", pergunta, re.IGNORECASE)
        and re.search(r"sem\s+(troc|substitu|mud)\w*", pergunta, re.IGNORECASE)
        and re.search(r"material|filament", pergunta, re.IGNORECASE)
    )

    # Rastreia entidades extraidas (quais ferramentas foram usadas)
    entidades = {"maquina": None, "ticket": None, "data": None, "peca": None,
                 "material": None, "cor": None, "intencao": "mcp_agente"}

    for iteracao in range(_MAX_ITERACOES):
        kwargs = {"model": modelo, "messages": mensagens, "temperature": 0.2}
        if tools:
            kwargs["tools"] = tools
            # Pergunta de compatibilidade: 1a rodada OBRIGA a ferramenta deterministica;
            # depois PROIBE novas ferramentas, pra responder so com aquele resultado
            # (sem chamar listar_impressoras etc. e derrapar pra outro assunto).
            if forcar_pecas:
                kwargs["tool_choice"] = (
                    {"type": "function", "function": {"name": _TOOL_PECAS_IMPRESSORA}}
                    if iteracao == 0 else "none"
                )
            else:
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
            # Normaliza para o nome real da ferramenta do catos.
            nome = _normalizar_nome(tc.function.name, nomes_validos)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            # Extrai entidades a partir dos nomes e parâmetros das ferramentas chamadas.
            if "impressora" in nome or "status_impressora" in nome:
                entidades["maquina"] = args.get("printer") or args.get("impressora") or args.get("maquina")
            if "ticket" in nome:
                entidades["ticket"] = args.get("ticket_codigo") or args.get("codigo_ticket")

            # Ferramenta SINTETICA: resolvida localmente, NAO vai ao catos.
            if nome == _TOOL_PECAS_IMPRESSORA:
                maquina = (args.get("maquina") or args.get("printer") or "").strip()
                entidades["maquina"] = maquina or entidades["maquina"]
                resultado = _pecas_compativeis_na_impressora(maquina)
                log.info("Agente MCP: cruzamento deterministico pecas x %s.", maquina)
                mensagens.append({"role": "tool", "tool_call_id": tc.id,
                                  "content": resultado or "(sem conteudo)"})
                continue

            # Ferramentas de MEMORIA: resolvidas localmente (core/memoria.py).
            if nome == _TOOL_LEMBRAR:
                resultado = memoria.lembrar_fato(args.get("fato", ""), args.get("validade_dias"))
                mensagens.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})
                continue
            if nome == _TOOL_BUSCAR_FATOS:
                encontrados = memoria.buscar_fatos(args.get("consulta", ""))
                resultado = "\n".join(f"- {f}" for f in encontrados) if encontrados else "Nenhum fato guardado sobre isso."
                mensagens.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})
                continue

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
