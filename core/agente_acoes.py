"""
core/agente_acoes.py
--------------------
Agente de ACOES da JARVIS sobre o sistema (escrita no sistema real).

Fluxo com confirmacao obrigatoria:
    1. Operador pede uma acao ("manda imprimir a M04", "agenda CAPA PROT na M05")
    2. Agente interpreta, busca os dados necessarios (UUIDs) e descreve o que
       vai fazer em linguagem natural.
    3. JARVIS pede confirmacao: "Vou [descricao]. Confirma?"
    4. Estado fica PENDENTE (salvo em _PENDENTE).
    5. Proximo texto do operador: se for confirmacao -> executa; senao -> cancela.

ACOES LIBERADAS (as 4 aprovadas):
    - enviar_para_impressora      : manda o fatiamento pra impressora imprimir
    - agendar_mesa_impressao      : agenda producao numa maquina numa data
    - alterar_impressora_agendamento : move agendamento nao iniciado pra outra maquina
    - registrar_resultado_impressao  : registra conclusao ou falha de uma impressao

AUDITORIA:
    Toda acao executada registra: ferramenta, argumentos, operador (nome da
    biometria se disponivel), timestamp. Preparado para receber o nome do
    operador quando a biometria de voz (Fase 13) for reativada.

SEGURANCA:
    - So as 4 ferramentas acima sao chamadas. Qualquer outra e bloqueada.
    - Confirmacao eh OBRIGATORIA — sem ela nada executa.
    - Operacoes de leitura ainda passam pelo agente_mcp (este modulo nao responde
      a perguntas de consulta).
"""

import json
import re
import unicodedata
from datetime import datetime

from core import llm_client, mcp_client
from core.logger import get_logger

log = get_logger("jarvis.acoes")

# ===========================================================================
# Ferramentas liberadas (apenas estas 4 — trava dura no codigo)
# ===========================================================================
_ACOES_LIBERADAS = {
    "enviar_para_impressora",
    "agendar_mesa_impressao",
    "alterar_impressora_agendamento",
    "registrar_resultado_impressao",
}

# ===========================================================================
# Estado de confirmacao pendente (em memoria — uma acao por vez)
# ===========================================================================
_PENDENTE: dict | None = None   # {ferramenta, argumentos, descricao, operador}


def tem_confirmacao_pendente() -> bool:
    return _PENDENTE is not None


def cancelar_pendente() -> str:
    global _PENDENTE
    _PENDENTE = None
    return "Acao cancelada."


# ===========================================================================
# Palavras que indicam confirmacao ou cancelamento
# ===========================================================================
_SIM = re.compile(
    r"\b(sim|confirma|confirmo|pode|ok|vai|faz|manda|executa|continua|yes)\b",
    re.IGNORECASE,
)
_NAO = re.compile(
    r"\b(nao|nope|cancela|cancelo|para|stop|desiste|aborta|volta)\b",
    re.IGNORECASE,
)


def _ascii(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in norm if not unicodedata.combining(c))


def _normalizar_demandas(resultado_bruto: str) -> str:
    """
    Adiciona 'name_ascii' a cada demanda (nome sem acentos) para o LLM casar
    nomes digitados sem acento com nomes do sistema que tem acento.
    As demandas vem como objetos JSON concatenados (nao um array).
    """
    try:
        from core.painel import _parse_lista_json
        itens = _parse_lista_json(resultado_bruto)
        for d in itens:
            d["name_ascii"] = _ascii(d.get("name") or "")
        # Reconstroi como objetos concatenados (formato original do sistema)
        return "\n".join(json.dumps(d, ensure_ascii=False) for d in itens)
    except Exception:  # noqa: BLE001
        return resultado_bruto


def _normalizar_fatiamentos(resultado_bruto: str) -> str:
    """
    Adiciona um campo 'filename_ascii' a cada fatiamento com o nome sem acentos.
    Permite o LLM casar "Obrigatorio" com "Obrigatório" sem perder os UUIDs.
    """
    try:
        dados = json.loads(resultado_bruto)
        for f in dados.get("fatiamentos") or []:
            nm = f.get("filename") or ""
            norm = unicodedata.normalize("NFKD", nm)
            f["filename_ascii"] = "".join(
                c for c in norm if not unicodedata.combining(c)
            )
        return json.dumps(dados, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return resultado_bruto


def _sem_acento(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in norm if not unicodedata.combining(c))


def eh_confirmacao(texto: str) -> bool:
    return bool(_SIM.search(_sem_acento(texto)))


def eh_cancelamento(texto: str) -> bool:
    return bool(_NAO.search(_sem_acento(texto)))


# ===========================================================================
# System prompt para o agente de acoes
# ===========================================================================
_SYSTEM_PROMPT = """Voce e a JARVIS, assistente de producao de um laboratorio de impressao 3D.
O operador pediu uma ACAO no sistema (nao uma consulta). Sua tarefa e:

1. Identificar QUAL das 4 acoes liberadas o operador quer:
   - enviar_para_impressora      : "manda imprimir", "envia pra impressora", "dispara o job"
   - agendar_mesa_impressao      : "agenda", "programa", "reserva a mesa"
   - alterar_impressora_agendamento : "muda", "move", "transfere o agendamento"
   - registrar_resultado_impressao  : "registra resultado", "marca como concluido/falha"

2. Buscar os dados necessarios usando as ferramentas de LEITURA disponiveis
   (listar_impressoras, listar_agendamentos, listar_fatiamentos, etc.).

3. Montar os argumentos corretos para a acao.

4. Descrever o que vai fazer de forma CLARA e CURTA em portugues do Brasil,
   para que o operador possa confirmar. Exemplo:
   "Vou enviar o fatiamento '1x_CAPA.ufp' para a M04. Confirma?"

IMPORTANTE:
- Use ferramentas de leitura para buscar UUIDs e nomes tecnicos — o operador
  nao fala UUID, ele fala "M04" ou "CAPA PROT".
- listar_agendamentos devolve {"ok": true, "agendamentos": [...]} — leia o
  campo "agendamentos" para ver schedule_uuid, printer, status, slice_uuid.
  Status "pending" = ainda nao enviado; "failed_pending_confirmation" = falhou e aguarda reenvio.
- listar_fatiamentos devolve {"ok": true, "total": N, "fatiamentos": [...]} — leia o
  campo "fatiamentos". Cada item tem: slice_uuid, filename, filename_ascii (nome sem
  acentos, use ESTE para comparar com o nome pedido pelo operador), is_active,
  material_0_label, material_0_color. Busque pelo campo filename_ascii ignorando
  maiusculas/minusculas. Ex.: operador diz "Placa Obrigatorio" -> busca "obrigatorio"
  em filename_ascii de cada item.
- listar_demandas_pendentes devolve objetos JSON concatenados. Cada um tem:
  componente_uuid, name (com acentos), name_ascii (sem acentos — use ESTE para
  comparar com o nome pedido), quantidade_pendente, tickets[].
  Busque pelo campo name_ascii ignorando maiusculas/minusculas.
- Para ENVIAR UMA PECA NOVA para imprimir (sem agendamento existente), o fluxo e:
  1. listar_fatiamentos -> achar o slice_uuid da peca pelo filename
  2. listar_demandas_pendentes -> achar o componente_uuid da peca
  3. agendar_mesa_impressao (printer, data_agendada=agora ou data pedida, componentes=[{componente_uuid, quantidade}])
     -> isso retorna um schedule_uuid (no preparo vira "AUTO_AGENDAMENTO")
  4. enviar_para_impressora (schedule_uuid="AUTO_AGENDAMENTO", slice_uuid)
     -> use EXATAMENTE o schedule_uuid devolvido pelo passo 3 (sera "AUTO_AGENDAMENTO"
        no preparo; o sistema troca pelo valor real ao confirmar).
  Chame os DOIS passos (agendar e enviar) na mesma preparacao antes de descrever.
  Se nao houver demanda pendente para a peca, avise o operador — nao da pra agendar sem componente_uuid.
  Se o fatiamento nao for encontrado, avise que o arquivo UFP nao esta no sistema.
- REGRA DE QUANTIDADE (critica): se o operador NAO disser um numero, agende SEMPRE
  quantidade=1 — NUNCA a demanda pendente inteira (uma mesa de impressao nao comporta
  dezenas de pecas, e pedir mais do que existe falha no sistema). So use uma quantidade
  maior se o operador pedir explicitamente ("produz 5 capas", "agenda 3"). Mesmo assim,
  nunca passe de quantidade_pendente. O campo quantidade_pendente serve para ESCOLHER a
  peca mais urgente, NAO para definir quantas agendar.
- DISTINCAO IMPORTANTE entre AGENDAR e IMPRIMIR AGORA:
  * AGENDAR = so RESERVAR a mesa pra depois (verbos: "deixa agendado", "agenda",
    "programa pra depois", "reserva"). NAO precisa imprimir agora, NAO precisa casar
    material agora (o filamento pode ser trocado ate a data). Pode ser em QUALQUER
    impressora. Use SO agendar_mesa_impressao — NAO chame enviar_para_impressora.
  * IMPRIMIR AGORA = comecar a impressao ja (verbos: "manda imprimir", "imprime
    agora", "bota pra rodar"). Aqui SIM o material carregado precisa bater, porque
    vai imprimir na hora. Use agendar_mesa_impressao + enviar_para_impressora.

- AGENDAMENTO (operador quer DEIXAR AGENDADO pra depois, sem imprimir agora):
  1. listar_demandas_pendentes -> a peca pedida; se nao nomeou, a de MAIOR
     quantidade_pendente (mais urgente). Anote componente_uuid. A quantidade a agendar
     e 1 (salvo se o operador pediu um numero) — veja REGRA DE QUANTIDADE acima.
  2. Escolha a impressora:
     - Se o operador pediu "onde nao precise trocar filamento": ache o fatiamento
       (listar_fatiamentos) pra pegar material_0_guid e chame buscar_impressora_compativel;
       use a 1a de "compativeis". Se "compativeis" vier vazio, ai sim avise que nenhuma
       ociosa tem o material — mas lembre que pra AGENDAR qualquer impressora online serve.
     - Se o operador disse "qualquer impressora" ou nao se importou com material:
       pegue qualquer impressora online (listar_impressoras).
  3. agendar_mesa_impressao (printer, data_agendada=data pedida ou agora, componentes=
     [{componente_uuid, quantidade}]). NAO chame enviar_para_impressora.

- IMPRIMIR AGORA NUMA MAQUINA COMPATIVEL (operador quer comecar ja, sem trocar filamento):
  1. listar_demandas_pendentes -> peca pedida ou a de maior quantidade_pendente.
  2. listar_fatiamentos -> fatiamento dessa peca (name_ascii vs filename_ascii). Anote
     slice_uuid, material_0_label e material_0_guid. Se material_0_guid for null, avise
     que nao da pra checar compatibilidade.
  3. buscar_impressora_compativel (material_guid=material_0_guid) -> confie no campo
     "compativeis"; NAO cheque impressora por impressora na mao.
  4. Se "compativeis" tiver impressora: use a 1a e faca agendar + enviar.
     Se vier vazio: "Nao consegui achar uma impressora ociosa com [material] disponivel
     — todas estao ocupadas ou com outro filamento."
- REGRA ANTI-SUBSTITUICAO (critica para acoes): so use uma demanda/fatiamento se
  o NOME realmente corresponder ao que o operador pediu (comparando name_ascii /
  filename_ascii, ignorando acentos/maiusculas). Se o item pedido NAO existir na
  lista, responda comecando com "Nao consegui" e diga que nao achou aquele item
  especifico. NUNCA escolha outra peca parecida ou a unica disponivel no lugar da
  pedida — agendar/imprimir a peca errada e um erro grave.
  EXCECAO: no fluxo de AGENDAMENTO INTELIGENTE (acima), o operador nao nomeou peca
  especifica — pode usar a mais urgente da lista.
- USE O CONTEXTO DA CONVERSA: o pedido de acao quase sempre vem DEPOIS de uma consulta
  que ja nomeou a peca e/ou a maquina. Se o operador disser "bota essa capa pra produzir
  nela", "manda essa peca", "agenda essa", "produz ela na M05" — "essa/ela/nela" se
  referem ao que foi falado nas mensagens anteriores. Resolva a referencia pelo historico
  ANTES de perguntar. So pergunte "qual peca?" se o historico realmente nao deixar claro.
- NUNCA pergunte ao operador antes de chamar as ferramentas de leitura. Se faltarem
  dados (peca, maquina), use o contexto da conversa e as ferramentas para descobrir —
  NAO pergunte. So pergunte se, DEPOIS de checar contexto e ferramentas, ainda houver
  ambiguidade real (ex.: 3 agendamentos na mesma maquina e nao da pra saber qual).
  "Qual peca?" nunca e uma primeira resposta.
- Se nao encontrar os dados necessarios nas ferramentas, diga o que falta em vez de inventar.
- Para enviar_para_impressora: SEMPRE verifique o material carregado na impressora
  (campo material_active.extruders[].label em obter_status_impressora) e compare
  com o material do fatiamento (material_0_label em listar_fatiamentos). Se forem
  diferentes, informe na descricao de confirmacao. Ex.:
  "Vou enviar 'PLACA.ufp' para a M04. Atencao: a M04 esta com Tough PLA mas o
   job precisa de Black UltraPLA — troque o filamento antes de confirmar."
- NAO execute nada — apenas descreva e peca confirmacao.
- Resposta final DEVE comecar com "Vou " ou "Nao consegui". So use "Qual " se depois
  de chamar as ferramentas ainda houver ambiguidade real que o operador precisa resolver.
- Sem markdown, sem listas, sem emojis."""


# ===========================================================================
# Ferramenta de leitura + acao
# ===========================================================================

def _tools_leitura_openai() -> list[dict]:
    """Ferramentas de LEITURA no formato OpenAI (para o agente buscar dados)."""
    ferramentas = mcp_client.ferramentas_de_leitura()
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
    if nome_bruto in nomes_validos:
        return nome_bruto
    curto = nome_bruto.split(".")[-1]
    return curto if curto in nomes_validos else nome_bruto


def _criar_com_retry(cliente, kwargs, tentativas: int = 3):
    """
    chat.completions.create com retry. O Gemini (via Kilo) as vezes devolve
    finish_reason='error' ou conteudo vazio sem tool_calls com tool-calling;
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
        log.info("Agente de acoes: resposta vazia/erro (finish=%s), tentativa %d/%d.",
                 escolha.finish_reason, n + 1, tentativas)
    return ultima


def _schema_acao(nome: str) -> dict:
    """Busca o schema de entrada de uma ferramenta de acao pelo nome."""
    todas = mcp_client.listar_ferramentas()
    for t in todas:
        if t["name"] == nome:
            return t.get("input_schema") or {}
    return {}


# Ferramenta SINTETICA (nao existe no sistema — resolvida localmente em Python).
# Existe porque o LLM erra ao cruzar 6 impressoras x 2 extrusores na mao: ele
# pulava obter_status_impressora e alucinava "nenhuma compativel". Aqui o
# cruzamento e deterministico e por GUID exato.
_TOOL_BUSCAR_COMPATIVEL = "buscar_impressora_compativel"


def _impressoras_compativeis(material_guid: str, somente_ociosas: bool = True) -> str:
    """
    Cruzamento DETERMINISTICO: dado o material_guid de um fatiamento, devolve as
    impressoras cujo extrusor ja tem esse material carregado (logo, sem troca de
    filamento). Junta listar_impressoras + obter_status_impressora numa unica
    resposta JSON para o agente nao precisar raciocinar sobre cada impressora.
    """
    from core.painel import _parse_lista_json
    try:
        imp = _parse_lista_json(mcp_client.chamar_ferramenta("listar_impressoras", {}))
    except Exception as err:  # noqa: BLE001
        return json.dumps({"ok": False, "erro": str(err)}, ensure_ascii=False)

    compativeis: list[dict] = []
    todas_info: list[dict] = []
    for m in imp:
        nome = m.get("name")
        try:
            detalhe = json.loads(
                mcp_client.chamar_ferramenta("obter_status_impressora", {"printer": nome})
            )
        except Exception:  # noqa: BLE001
            continue
        status = str(detalhe.get("status", "")).lower()
        online = detalhe.get("online", m.get("online"))
        mat = detalhe.get("material_active", {}) or {}
        extrusores = mat.get("extruders", []) or []
        casa = [
            {"index": e.get("index"), "label": e.get("label"), "color": e.get("color")}
            for e in extrusores
            if isinstance(e, dict) and e.get("guid") == material_guid
        ]
        info = {"printer": nome, "status": status, "online": online,
                "extrusores_compativeis": casa}
        todas_info.append(info)
        if casa and online is not False and (not somente_ociosas or status == "idle"):
            compativeis.append(info)

    return json.dumps({
        "ok": True,
        "material_guid": material_guid,
        "compativeis": compativeis,  # impressoras prontas pra esse material
        "todas": todas_info,         # panorama completo (pra explicar se nao houver)
    }, ensure_ascii=False)


# ===========================================================================
# FASE 1: interpretar pedido e montar descricao para confirmacao
# ===========================================================================

def interpretar(pergunta: str, operador: str | None = None,
                historico: list[dict] | None = None) -> str:
    """
    Recebe o pedido de acao do operador. Usa o LLM + ferramentas de leitura
    para entender o que precisa ser feito, busca UUIDs/dados reais, e devolve
    uma descricao para confirmacao ("Vou fazer X. Confirma?").

    Tambem popula _PENDENTE com a acao preparada (ferramenta + args).
    Se algo falhar ou for ambiguo, devolve pergunta de esclarecimento e NAO
    popula _PENDENTE.

    `historico` — trocas anteriores ({"role","content"}) para resolver referencias
    do tipo "essa capa", "nela", "essa peca": o pedido de acao costuma vir depois de
    uma consulta que ja nomeou a peca/maquina. Sem isso o agente nao sabe a que se refere.
    """
    global _PENDENTE
    _PENDENTE = None  # limpa estado anterior

    cliente, modelo = llm_client.cliente_kilo()
    if cliente is None:
        return "Preciso do provedor Kilo para executar acoes. Configure o KILO_API_KEY."

    ferramentas_leitura = mcp_client.ferramentas_de_leitura()
    nomes_leitura = {f["name"] for f in ferramentas_leitura}
    tools = _tools_leitura_openai()

    # Adiciona as 4 ferramentas de ACAO para o agente poder "montar" a chamada.
    # Elas NAO sao executadas aqui — apenas usadas para o modelo saber os schemas.
    todas = mcp_client.listar_ferramentas()
    for t in todas:
        if t["name"] in _ACOES_LIBERADAS:
            schema = t.get("input_schema") or {"type": "object", "properties": {}}
            tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": (t.get("description") or "")[:1024],
                    "parameters": schema,
                },
            })

    # Ferramenta SINTETICA: cruzamento deterministico material -> impressora ociosa.
    tools.append({
        "type": "function",
        "function": {
            "name": _TOOL_BUSCAR_COMPATIVEL,
            "description": (
                "Dado o material_0_guid de um fatiamento, devolve as impressoras "
                "OCIOSAS que ja tem esse material carregado (sem trocar filamento). "
                "Faz o cruzamento exato por GUID — CONFIE no campo 'compativeis': se "
                "estiver vazio, nao ha impressora ociosa com esse material. Use esta "
                "ferramenta no fluxo de agendamento inteligente em vez de checar cada "
                "impressora manualmente."),
            "parameters": {
                "type": "object",
                "properties": {
                    "material_guid": {
                        "type": "string",
                        "description": "O material_0_guid do fatiamento da peca.",
                    },
                    "somente_ociosas": {
                        "type": "boolean",
                        "description": "Se true (padrao), so impressoras com status idle.",
                    },
                },
                "required": ["material_guid"],
            },
        },
    })

    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    contexto_tempo = (f"\n\nReferencia de tempo: agora sao {agora} (use isto para "
                      f"data_agendada — 'imprimir agora' = use esta data/hora; "
                      f"datas relativas como 'amanha 8h' calcule a partir daqui).")
    mensagens = [{"role": "system", "content": _SYSTEM_PROMPT + contexto_tempo}]
    if historico:
        # Contexto da conversa: resolve "essa capa", "nela", etc. So mensagens
        # de texto (user/assistant) — sem tool_calls antigos, que confundiriam.
        for h in historico:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                mensagens.append({"role": h["role"], "content": h["content"]})
    mensagens.append({"role": "user", "content": pergunta})
    nomes_acao_validos = set(_ACOES_LIBERADAS)

    # Coleciona a SEQUENCIA de acoes que o modelo monta. Botar uma peca nova pra
    # imprimir, por exemplo, sao 2 passos: agendar_mesa_impressao -> enviar_para_impressora.
    passos: list[dict] = []

    for iteracao in range(6):
        # Na primeira iteracao forcamos uma chamada de ferramenta (nao pode responder
        # com texto sem consultar dados). Isso evita que o modelo pergunte "qual peca?"
        # antes de tentar descobrir sozinho via listar_demandas_pendentes etc.
        tool_choice = "required" if iteracao == 0 else "auto"
        escolha = _criar_com_retry(cliente, {
            "model": modelo, "messages": mensagens, "tools": tools,
            "tool_choice": tool_choice, "temperature": 0.1,
        })
        msg = escolha.message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            # Modelo chegou a uma resposta textual (descricao ou pergunta de esclarecimento).
            texto = (msg.content or "").strip()
            if passos and texto.startswith("Vou "):
                # Temos a sequencia de acoes preparada + descricao de confirmacao.
                _PENDENTE = {
                    "passos": passos,
                    "descricao": texto,
                    "operador": operador or "Desconhecido",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                log.info("Acao pendente (%d passo(s)): %s",
                         len(passos), [p["ferramenta"] for p in passos])
            return texto or "Nao consegui preparar essa acao. Pode reformular?"

        mensagens.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            nome = _normalizar_nome(tc.function.name, nomes_leitura | nomes_acao_validos)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if nome in nomes_acao_validos:
                # Nao executa — apenas registra que o modelo quer chamar esta acao.
                passos.append({"ferramenta": nome, "argumentos": args})
                if nome == "agendar_mesa_impressao":
                    # O agendamento ainda nao foi criado (so na confirmacao), entao
                    # devolvemos um schedule_uuid SINTETICO. O modelo deve usar esse
                    # valor no passo enviar_para_impressora; na execucao real ele e
                    # substituido pelo schedule_uuid verdadeiro.
                    resultado = json.dumps({
                        "status": "preparado", "acao": nome,
                        "schedule_uuid": "AUTO_AGENDAMENTO",
                        "message": "Agendamento preparado; sera criado ao confirmar.",
                    }, ensure_ascii=False)
                else:
                    resultado = json.dumps({"status": "preparado", "acao": nome, "args": args},
                                           ensure_ascii=False)
                log.info("Agente de acoes: preparou '%s' com args %s", nome, args)
            elif nome == _TOOL_BUSCAR_COMPATIVEL:
                try:
                    resultado = _impressoras_compativeis(
                        args.get("material_guid", ""),
                        args.get("somente_ociosas", True),
                    )
                    log.info("Agente de acoes: buscou impressoras compativeis (guid=%s).",
                             args.get("material_guid"))
                except Exception as err:  # noqa: BLE001
                    resultado = f"ERRO ao buscar impressoras compativeis: {err}"
            elif nome in nomes_leitura:
                try:
                    # Para ferramentas com campo 'search' que usa nomes acentuados,
                    # removemos o search e retornamos TUDO — nossa normalizacao
                    # adiciona campos ascii e o LLM filtra client-side.
                    # Isso evita o sistema retornar vazio quando o operador digita
                    # sem acento (ex.: "Obrigatorio" vs "Obrigatório").
                    args_efetivos = {k: v for k, v in args.items() if k != "search"} \
                        if nome in ("listar_fatiamentos", "listar_demandas_pendentes") \
                        else args
                    resultado = mcp_client.chamar_ferramenta(nome, args_efetivos)
                    if nome == "listar_fatiamentos":
                        resultado = _normalizar_fatiamentos(resultado)
                    elif nome == "listar_demandas_pendentes":
                        resultado = _normalizar_demandas(resultado)
                    log.info("Agente de acoes: leu '%s'.", nome)
                except Exception as err:  # noqa: BLE001
                    resultado = f"ERRO ao ler: {err}"
            else:
                resultado = f"Ferramenta '{nome}' nao reconhecida ou bloqueada."

            mensagens.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": resultado or "(sem conteudo)",
            })

    return "Nao consegui preparar essa acao a tempo. Pode reformular?"


# ===========================================================================
# FASE 2: executar acao confirmada
# ===========================================================================

_PLACEHOLDERS_SCHEDULE = {"AUTO_AGENDAMENTO", "AUTO", "<schedule_uuid>", "PENDENTE"}


def executar_confirmado(operador: str | None = None) -> str:
    """
    Executa a SEQUENCIA de acoes que estava em _PENDENTE (so chamada apos
    confirmacao explicita do operador). Encadeia o schedule_uuid: o agendamento
    e criado primeiro e seu schedule_uuid alimenta o passo de enviar.
    Registra cada acao em log (auditoria). Limpa _PENDENTE ao final.

    `operador` = quem CONFIRMOU (autorizou) a acao, pela biometria de voz.
    Tem prioridade no log; se None, usa quem preparou a acao.
    """
    global _PENDENTE
    if _PENDENTE is None:
        return "Nao ha acao pendente para executar."

    passos = _PENDENTE.get("passos") or []
    # Quem autoriza (disse "sim") manda no log; senao, quem pediu.
    # `pediu` guarda o nome CRU de quem preparou (sobrevive a re-pendencia).
    pediu = _PENDENTE.get("pediu") or _PENDENTE.get("operador") or "Desconhecido"
    autorizou = operador or pediu or "Desconhecido"
    operador_log = (f"{autorizou} (autorizou)" if autorizou == pediu
                    else f"{autorizou} (autorizou; pedido por {pediu})")

    if not passos:
        _PENDENTE = None
        return "Nao ha acao pendente para executar."

    for p in passos:
        if p["ferramenta"] not in _ACOES_LIBERADAS:
            _PENDENTE = None
            return f"Acao '{p['ferramenta']}' nao esta na lista de acoes liberadas. Cancelando."

    contexto: dict = {}  # captura saidas entre passos (ex.: schedule_uuid real)
    ultima_resposta = ""

    try:
        for i, passo in enumerate(passos):
            ferramenta = passo["ferramenta"]
            argumentos = dict(passo["argumentos"])

            # Encadeamento: troca o schedule_uuid sintetico pelo real do agendamento.
            for chave, valor in list(argumentos.items()):
                if isinstance(valor, str) and valor in _PLACEHOLDERS_SCHEDULE:
                    if "schedule_uuid" in contexto:
                        argumentos[chave] = contexto["schedule_uuid"]

            # GUARDA: se sobrou um placeholder nao substituido, o passo anterior
            # (agendamento) nao gerou schedule_uuid — entao NAO envie nada ao sistema
            # com um UUID sintetico (isso gerava o confuso "agendamento nao encontrado").
            placeholder_pendente = [
                c for c, v in argumentos.items()
                if isinstance(v, str) and v in _PLACEHOLDERS_SCHEDULE
            ]
            if placeholder_pendente:
                _PENDENTE = None
                log.warning("Passo '%s' tem placeholder nao resolvido %s — abortado.",
                            ferramenta, placeholder_pendente)
                return ("Nao consegui concluir: o agendamento nao foi criado, entao "
                        "nao da pra enviar pra impressora. Nada foi enviado.")

            resultado_bruto = mcp_client._rodar(_executar_acao(ferramenta, argumentos))
            log.info(
                "ACAO EXECUTADA | operador=%s | ferramenta=%s | args=%s | resultado=%s",
                operador_log, ferramenta, argumentos, resultado_bruto[:200],
            )

            # Captura o schedule_uuid criado (pra alimentar o proximo passo).
            dados = None
            try:
                dados = json.loads(resultado_bruto)
            except Exception:  # noqa: BLE001
                dados = None
            if isinstance(dados, dict) and dados.get("schedule_uuid"):
                contexto["schedule_uuid"] = dados["schedule_uuid"]

            # Aviso de material errado no envio: nao reagenda — mantem so o passo
            # de enviar pronto (com o schedule_uuid real) pra reenvio forcado.
            aviso_material = _checar_aviso_material(ferramenta, resultado_bruto)
            if aviso_material:
                passo_envio = dict(passo)
                passo_envio["argumentos"] = dict(argumentos)
                passo_envio["argumentos"]["confirmar_material_diferente"] = True
                _PENDENTE = {
                    "passos": [passo_envio],
                    "descricao": aviso_material,
                    # guarda os nomes CRUS pra reformatar certo no reenvio
                    "pediu": pediu,
                    "operador": autorizou,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                return (aviso_material + " Troque o filamento e diga 'confirma', "
                        "ou diga 'confirma' pra enviar assim mesmo.")

            # Qualquer outra falha do sistema (ok:false que NAO seja material):
            # ABORTA a sequencia e reporta o erro real. Nao roda os passos seguintes
            # — senao um agendamento que falhou levaria a um envio com UUID invalido.
            if isinstance(dados, dict) and dados.get("ok") is False:
                _PENDENTE = None
                msg = dados.get("message") or "a acao falhou no sistema"
                log.warning("Passo '%s' falhou (%s) — sequencia abortada.", ferramenta, msg)
                return (f"Nao consegui concluir: {msg}. "
                        "Cancelei os passos seguintes — nada mais foi enviado.")

            ultima_resposta = _redigir_resultado(ferramenta, argumentos, resultado_bruto)
    except mcp_client.FerramentaBloqueada as bloqueio:
        log.warning("Acao bloqueada pelo gate: %s", bloqueio)
        _PENDENTE = None
        return "Acao bloqueada por seguranca. Isso nao deveria acontecer — avise o suporte."
    except Exception as erro:  # noqa: BLE001
        log.error("Falha ao executar acao: %s", erro)
        _PENDENTE = None
        return f"Ocorreu um erro ao executar a acao: {erro}"

    _PENDENTE = None
    return ultima_resposta or "Pronto, acao executada."


def _checar_aviso_material(ferramenta: str, resultado_bruto: str) -> str | None:
    """
    Se a ferramenta for enviar_para_impressora e o sistema retornou aviso de
    material diferente, devolve uma mensagem em portugues explicando o problema.
    Caso contrario devolve None.
    """
    if ferramenta != "enviar_para_impressora":
        return None
    try:
        dados = json.loads(resultado_bruto)
    except Exception:  # noqa: BLE001
        return None
    if dados.get("ok"):
        return None
    msg = dados.get("message", "")
    avisos = dados.get("avisos") or []
    if "material" not in msg.lower() and not avisos:
        return None

    # Extrai nomes amigaveis dos materiais dos avisos
    # Ex.: "Extrusor 0: o fatiamento espera material guid XXXX, que nao consta entre os carregados."
    # Tenta pegar o label do material carregado do campo material_carregado
    carregado = dados.get("material_carregado") or {}
    extrusores = carregado.get("extruders") or []
    nomes_carregados = [e.get("label") or e.get("name") or e.get("guid", "?")
                        for e in extrusores if isinstance(e, dict)]
    carregado_str = " e ".join(nomes_carregados) if nomes_carregados else "material diferente"

    # Tenta pegar o material esperado do fatiamento
    esperado = dados.get("material_esperado") or {}
    esperado_label = esperado.get("label") or esperado.get("name")
    if not esperado_label and avisos:
        # Tenta extrair do hint ou avisos
        esperado_label = None  # o sistema nao manda o label esperado, so o guid

    if esperado_label:
        return (f"A impressora esta com {carregado_str}, mas o job precisa de "
                f"{esperado_label}.")
    return (f"A impressora esta com {carregado_str} carregado, mas o fatiamento "
            f"precisa de um material diferente.")


async def _executar_acao(ferramenta: str, argumentos: dict) -> str:
    """Executa a ferramenta de acao diretamente via sessao MCP (sem gate de leitura)."""
    async with mcp_client._abrir_sessao() as sessao:
        resultado = await sessao.call_tool(ferramenta, argumentos or {})
        return mcp_client._extrair_texto(resultado)


def _redigir_resultado(ferramenta: str, args: dict, resultado_bruto: str) -> str:
    """Converte o resultado tecnico da acao numa resposta natural."""
    cliente, modelo = llm_client.cliente_kilo()
    if not cliente:
        return resultado_bruto or "Acao executada."

    prompt = (
        f"A acao '{ferramenta}' foi executada com os argumentos {json.dumps(args, ensure_ascii=False)}.\n"
        f"Resultado tecnico do sistema: {resultado_bruto[:600]}\n\n"
        "Redija uma resposta curta (1-2 frases) em portugues do Brasil, tom falado, "
        "informando que a acao foi concluida. Sem markdown, sem listas, sem emojis."
    )
    texto = llm_client.gerar(prompt, json_mode=False, temperature=0.2)
    return texto or "Acao executada com sucesso."


# ===========================================================================
# Ponto de entrada publico (chamado pelo gemini_brain)
# ===========================================================================

def disponivel() -> tuple[bool, str]:
    """Requer sistema conectado + provedor Kilo."""
    ok, motivo = mcp_client.disponivel()
    if not ok:
        return False, motivo
    c, _ = llm_client.cliente_kilo()
    if c is None:
        return False, "acoes exigem o provedor Kilo (KILO_API_KEY no .env)"
    return True, ""
