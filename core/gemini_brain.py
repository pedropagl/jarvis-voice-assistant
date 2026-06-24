"""
core/gemini_brain.py
--------------------
Cerebro de linguagem da JARVIS (FASE 7).

Fluxo de uma pergunta:
    1. interpretar_comando_com_gemini(texto)  -> identifica INTENCAO + extrai
       entidades (maquina, data, ticket, peca, material, cor).
    2. command_router.route(intencao, params) -> chama a consulta certa no
       company_system e devolve os DADOS REAIS + um resumo factual.
    3. responder(...)                          -> redige a resposta final, curta
       e natural, USANDO SOMENTE os dados retornados.

REGRA DE OURO (inviolavel):
    O modelo NUNCA inventa maquinas, pecas, materiais, tickets ou quantidades.
    Ele apenas (a) entende a pergunta e (b) reescreve com naturalidade o resumo
    factual que o sistema produziu. Se nao houver dados suficientes, responde:
        "Nao encontrei informacao suficiente no sistema para responder com seguranca."

PROVEDOR DE LLM (ver core/llm_client.py):
    A interpretacao e a redacao passam pelo llm_client, que escolhe o provedor
    pelo .env (KILO > GEMINI > LOCAL). As chaves vivem so no .env, nunca no codigo.

MODO LOCAL (sem provedor):
    Se nenhum provedor estiver configurado (ou a chamada falhar), a JARVIS cai
    num interpretador/redator LOCAL e deterministico. Mesmo nesse modo os dados
    continuam vindo apenas do company_system.

Obs.: o nome "gemini_brain" foi mantido por compatibilidade com as fases
anteriores; hoje o cerebro e agnostico de provedor.
"""

import json
import re
import unicodedata

from core import agente_acoes
from core import agente_mcp
from core import command_router
from core import llm_client
from core import metricas
from core import security
from core.command_router import (
    INTENCAO_PECA_COMPATIVEL,
    INTENCAO_FALTANTES_TICKET,
    INTENCAO_RECOMENDAR,
    INTENCAO_STATUS_MAQUINA,
    INTENCAO_STATUS_TICKET,
    INTENCAO_CONVERSA_GERAL,
    INTENCAO_DESCONHECIDA,
    DADOS_INSUFICIENTES,
    FUNCAO_POR_INTENCAO,
)
from core.logger import get_logger, registrar_interacao

log = get_logger("jarvis.gemini")

ANO_ATUAL = 2026  # alinhado com tickets.ANO_ATUAL; data sem ano usa este valor

# Historico de conversa: guarda as ultimas N trocas (user + assistant) para
# que o agente MCP mantenha contexto entre turnos ("M04" apos "qual peca posso
# fazer sem trocar filamento?" resolve corretamente, por exemplo).
_HISTORICO: list[dict] = []
_MAX_TURNOS_HISTORICO = 3  # 3 trocas = 6 mensagens; mais que isso vira ruido


_VERBOS_ACAO_LIBERADA = re.compile(
    r"\b(envi[ae]r?|mand[ae]r?|bot[ae]r?|coloca(r)?|coloque|poe|ponha|produz|"
    r"imprim[ei]r?|imprima|dispara(r)?|dispare|"
    r"agend\w*|program\w*|reserv\w*|"
    r"move(r)?|mova|muda(r)?|mude|transfere(r)?|transfira o agendamento|"
    r"registra(r)?|registre|marca(r)? como (conclu|falh)|"
    r"faca|faz (essa|esse|aquela|aquele|isso|a |o )|"
    r"manda imprimir|mande imprimir|"
    r"quero (mandar|enviar|botar|colocar|que (faca|imprima|agende)))\b",
    re.IGNORECASE,
)


def _eh_pedido_de_acao(texto: str) -> bool:
    """True se o texto pede uma das 4 acoes liberadas (nao consulta)."""
    limpo = _sem_acento(texto or "")
    return bool(_VERBOS_ACAO_LIBERADA.search(limpo))


def gemini_disponivel() -> bool:
    """
    True se ha um provedor de LLM real ativo (Kilo ou Gemini).
    Nome mantido por compatibilidade; delega ao llm_client.
    """
    return llm_client.disponivel()


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def _sem_acento(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11,
    "dezembro": 12,
}


# ===========================================================================
# 1) INTERPRETACAO DA PERGUNTA
# ===========================================================================

_ENTIDADES_VAZIAS = {
    "intencao": INTENCAO_DESCONHECIDA,
    "maquina": None,
    "data": None,
    "ticket": None,
    "peca": None,
    "material": None,
    "cor": None,
}

_PROMPT_INTERPRETACAO = """Voce e o interpretador de comandos da JARVIS, uma assistente de uma fabrica de impressao 3D.
Sua tarefa e LER a frase do operador e devolver SOMENTE um JSON com a intencao e as entidades citadas.
NAO invente nada. Se algo nao foi dito, use null.

Intencoes possiveis (campo "intencao"):
- "peca_compativel_maquina"        : quer saber que peca da pra fazer numa maquina sem trocar material.
- "pecas_faltantes_ticket"         : quer saber que pecas faltam num ticket / numa data.
- "recomendar_peca_ticket_maquina" : quer saber o que falta num ticket E o que da pra fazer numa maquina.
- "status_maquina"                 : quer o status/estado de uma maquina.
- "status_ticket"                  : quer saber se um ticket esta completo.
- "conversa_geral"                 : pergunta que NAO e sobre a fabrica (maquinas, pecas, tickets).
                                     Ex.: conhecimento geral, conta de matematica, bate-papo, saudacao.
- "desconhecida"                   : so use se nao der pra entender a frase.

IMPORTANTE: se a pergunta for sobre a fabrica mas faltar o dado (ex.: "qual o status?" sem dizer a maquina),
ainda assim use a intencao da fabrica correspondente (NAO use conversa_geral nesse caso).

Entidades a extrair:
- "maquina"  : codigo como "M04" (maiusculo), ou null.
- "data"     : SEMPRE no formato YYYY-MM-DD. Se o ano nao foi dito, use {ano}. Ex.: "23 de maio" -> "{ano}-05-23". Se nao houver data, null.
- "ticket"   : id como "TK-2026-0523-01", ou null.
- "peca"     : nome da peca citada, ou null.
- "material" : material citado (ex.: APEX), ou null.
- "cor"      : cor citada, ou null.

Responda APENAS com o JSON, sem texto extra, sem markdown.

Frase do operador: "{frase}"
"""


def interpretar_comando_com_gemini(texto: str) -> dict:
    """
    Identifica a intencao e extrai as entidades da frase do operador.

    Retorna sempre um dict com as chaves:
        intencao, maquina, data, ticket, peca, material, cor

    Usa o provedor de LLM (Kilo/Gemini) se disponivel; caso contrario, o
    interpretador local.
    """
    texto = (texto or "").strip()
    if not texto:
        return dict(_ENTIDADES_VAZIAS)

    prompt = _PROMPT_INTERPRETACAO.format(ano=ANO_ATUAL, frase=texto)
    bruto = llm_client.gerar(prompt, json_mode=True, temperature=0.0)

    if not bruto:
        # Sem provedor ou chamada falhou -> interpretador local.
        return _interpretar_local(texto)

    dados = _json_seguro(bruto)
    if dados is None:
        log.warning("LLM devolveu JSON invalido; usando interpretador local.")
        return _interpretar_local(texto)
    return _normalizar_entidades(dados)


def _json_seguro(bruto: str):
    """Tenta extrair um objeto JSON de uma string (tolera cercas markdown)."""
    if not bruto:
        return None
    bruto = bruto.strip()
    if bruto.startswith("```"):
        bruto = re.sub(r"^```[a-zA-Z]*", "", bruto).strip()
        bruto = bruto.rstrip("`").strip()
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", bruto, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _normalizar_entidades(dados: dict) -> dict:
    """Garante todas as chaves esperadas e tipos limpos."""
    saida = dict(_ENTIDADES_VAZIAS)
    if not isinstance(dados, dict):
        return saida

    for chave in saida:
        valor = dados.get(chave)
        if isinstance(valor, str):
            valor = valor.strip()
            if valor.lower() in ("", "null", "none", "nao informado"):
                valor = None
        saida[chave] = valor if valor not in ("",) else None

    if saida["maquina"]:
        saida["maquina"] = str(saida["maquina"]).strip().upper()
    if saida["intencao"] not in (
        INTENCAO_PECA_COMPATIVEL, INTENCAO_FALTANTES_TICKET, INTENCAO_RECOMENDAR,
        INTENCAO_STATUS_MAQUINA, INTENCAO_STATUS_TICKET, INTENCAO_CONVERSA_GERAL,
    ):
        saida["intencao"] = INTENCAO_DESCONHECIDA

    return saida


# ---------------------------------------------------------------------------
# Interpretador LOCAL (fallback deterministico, sem Gemini)
# ---------------------------------------------------------------------------

def _interpretar_local(texto: str) -> dict:
    """
    Extrai intencao e entidades por regras simples. Cobre os 5 casos da Fase 7.
    Nao e tao flexivel quanto o Gemini, mas garante teste offline do roteamento.
    """
    ent = dict(_ENTIDADES_VAZIAS)
    bruto = texto
    t = _sem_acento(texto)

    # Maquina: M seguido de digitos
    m = re.search(r"\bm\s*0*(\d{1,2})\b", t)
    if m:
        ent["maquina"] = f"M{int(m.group(1)):02d}"

    # Ticket explicito
    tk = re.search(r"\btk-?\s*\d{4}-?\d{4}-?\d{2}\b", t)
    if tk:
        ent["ticket"] = texto[tk.start():tk.end()].upper().replace(" ", "")

    # Data: "dia 23 de maio", "23 de maio", "23/05", "23/05/2026", "2026-05-23"
    ent["data"] = _extrair_data_local(t)

    # Material conhecido
    for mat in ("apex", "abs", "petg", "pla"):
        if re.search(rf"\b{mat}\b", t):
            ent["material"] = mat.upper()
            break

    # Cor conhecida
    for cor in ("vermelho", "preto", "branco", "cinza", "azul"):
        if re.search(rf"\b{cor}\b", t):
            ent["cor"] = cor
            break

    # ----- Intencao por palavras-chave -----
    tem_maquina = ent["maquina"] is not None
    tem_ticket_ou_data = ent["ticket"] is not None or ent["data"] is not None
    fala_falta = any(p in t for p in ("falta", "faltam", "faltando", "pendente"))
    fala_completo = any(p in t for p in ("completo", "concluido", "terminou", "pronto", "finalizado"))
    fala_status = any(p in t for p in ("status", "estado", "como esta", "situacao"))
    fala_recomendar = any(p in t for p in ("recomend",))
    fala_fazer = any(p in t for p in ("podemos fazer", "pode fazer", "da pra fazer",
                                       "consigo fazer", "consegue fazer", "fazer na"))
    fala_compativel = any(p in t for p in ("sem trocar", "sem substituir", "compativel",
                                           "sem trocar material")) or fala_fazer

    if fala_falta and tem_maquina and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_RECOMENDAR
    elif fala_recomendar and tem_maquina and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_RECOMENDAR
    elif fala_completo and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_STATUS_TICKET
    elif fala_falta and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_FALTANTES_TICKET
    elif (fala_compativel or fala_recomendar) and tem_maquina:
        ent["intencao"] = INTENCAO_PECA_COMPATIVEL
    elif fala_status and tem_maquina:
        ent["intencao"] = INTENCAO_STATUS_MAQUINA
    elif fala_status and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_STATUS_TICKET
    elif tem_maquina and tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_RECOMENDAR
    elif tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_FALTANTES_TICKET
    elif tem_maquina:
        ent["intencao"] = INTENCAO_STATUS_MAQUINA

    # Sem nenhum elemento de fabrica e sem intencao reconhecida -> conversa geral.
    if ent["intencao"] == INTENCAO_DESCONHECIDA and not tem_maquina and not tem_ticket_ou_data:
        ent["intencao"] = INTENCAO_CONVERSA_GERAL

    return ent


def _extrair_data_local(t: str):
    """Procura uma data no texto (ja sem acento) e devolve YYYY-MM-DD ou None."""
    # 2026-05-23
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if iso:
        return iso.group(0)

    # 23/05/2026 ou 23/05
    br = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", t)
    if br:
        dia, mes = int(br.group(1)), int(br.group(2))
        ano = int(br.group(3)) if br.group(3) else ANO_ATUAL
        return f"{ano:04d}-{mes:02d}-{dia:02d}"

    # "23 de maio" (com ou sem "dia"), ano opcional "de 2026"
    nome = re.search(r"\b(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b", t)
    if nome and nome.group(2) in _MESES:
        dia = int(nome.group(1))
        mes = _MESES[nome.group(2)]
        ano = int(nome.group(3)) if nome.group(3) else ANO_ATUAL
        return f"{ano:04d}-{mes:02d}-{dia:02d}"

    return None


# ===========================================================================
# 2) REDACAO DA RESPOSTA FINAL
# ===========================================================================

_PROMPT_RESPOSTA = """Voce e a JARVIS, assistente de producao de uma fabrica de impressao 3D.
Responda ao operador em portugues do Brasil, em TOM FALADO, curto e direto (1 ou 2 frases).

REGRAS ABSOLUTAS:
- Use SOMENTE os dados em "Resumo do sistema". NAO invente maquinas, pecas, materiais, tickets nem quantidades.
- Nao acrescente numeros nem nomes que nao estejam no resumo.
- Se o resumo disser que nao ha dados/nao foi encontrado, apenas comunique isso com naturalidade.
- Nao use markdown, listas nem emojis. So a frase falada.

Pergunta do operador: "{pergunta}"
Resumo do sistema (verdade absoluta): "{resumo}"

Resposta da JARVIS:"""

# Prompt para conversa geral (assuntos FORA da fabrica). Aqui o modelo pode
# responder com seu proprio conhecimento — NAO ha dados do sistema envolvidos.
_PROMPT_CONVERSA = """Voce e a JARVIS, assistente do laboratorio de impressao 3D, mas tambem conversa
sobre assuntos gerais. Responda em portugues do Brasil, em TOM FALADO, curto e direto (1 ou 2 frases).
Nao use markdown, listas nem emojis. Se nao souber, diga que nao sabe.

Pergunta: "{pergunta}"
Resposta da JARVIS:"""

# Mensagem quando e conversa geral mas NAO ha modelo (modo local nao responde geral).
SEM_MODELO_PARA_CONVERSA = (
    "No momento so consigo conversar sobre as maquinas, pecas e tickets do sistema."
)

# Mensagem quando HA modelo, mas ele devolveu vazio (tipico de filtro do provedor:
# Gemini bloqueia perguntas sobre eleicoes/figuras politicas e retorna conteudo vazio).
MODELO_NAO_RESPONDEU = (
    "Nao consegui responder isso agora. O modelo nao devolveu resposta — alguns "
    "assuntos, como politica e eleicoes, sao bloqueados pelo proprio provedor."
)


def _conversa_local(pergunta: str) -> str | None:
    """
    Responde conversa geral SIMPLES sem LLM (usado quando o modelo nao respondeu
    ou esta em modo local). Cobre saudacao e conta de matematica basica.
    Devolve a frase ou None se nao souber responder localmente.
    """
    t = _sem_acento((pergunta or "").strip()).rstrip("?.!")

    # Saudacoes
    if t in ("ola", "oi", "ola jarvis", "oi jarvis", "bom dia", "boa tarde", "boa noite", "e ai"):
        return "Ola! Sou a JARVIS. Posso te ajudar com as maquinas, pecas e tickets do lab."

    # Conta de matematica: extrai SOMENTE numeros e operadores (sem eval inseguro).
    expr_match = re.search(r"[-+/*x().\d\s,]+", t)
    if expr_match:
        bruto = expr_match.group(0).strip()
        # precisa ter ao menos um operador e um digito para valer a pena
        if any(op in bruto for op in "+-*/x") and any(c.isdigit() for c in bruto):
            expr = bruto.replace("x", "*").replace(",", ".")
            # so permite caracteres seguros de aritmetica
            if re.fullmatch(r"[-+*/().\d\s]+", expr):
                try:
                    resultado = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - expr ja sanitizada
                    if isinstance(resultado, (int, float)):
                        # mostra inteiro quando for redondo (4.0 -> 4)
                        if isinstance(resultado, float) and resultado.is_integer():
                            resultado = int(resultado)
                        return f"{bruto.replace('x', 'x')} = {resultado}."
                except Exception:  # noqa: BLE001 - conta invalida, ignora
                    pass

    return None


def responder(pergunta: str, resultado_router: dict) -> str:
    """
    Redige a resposta final a partir do resultado do roteador.

    - insuficiente            -> mensagem fixa de seguranca.
    - conversa geral ("livre") -> o modelo responde livremente (sem dados do sistema).
    - caso normal             -> o modelo reescreve o resumo factual; sem modelo,
                                devolve o proprio resumo (ja fiel aos dados).
    """
    if resultado_router.get("insuficiente"):
        return DADOS_INSUFICIENTES

    # Conversa geral: assunto fora da fabrica, resposta livre do modelo.
    if resultado_router.get("livre"):
        texto = llm_client.gerar(
            _PROMPT_CONVERSA.format(pergunta=pergunta), json_mode=False, temperature=0.5
        )
        if texto:
            return texto
        # Modelo nao respondeu (ou nao ha modelo). Antes de desistir, tentamos
        # resolver localmente o que da: saudacao e conta de matematica simples.
        local = _conversa_local(pergunta)
        if local:
            return local
        # Distinguir "nao ha modelo" (modo local) de "modelo respondeu vazio"
        # (filtro do provedor — ex.: Gemini bloqueia politica/eleicoes).
        if llm_client.disponivel():
            return MODELO_NAO_RESPONDEU
        return SEM_MODELO_PARA_CONVERSA

    resumo = resultado_router.get("resumo") or DADOS_INSUFICIENTES

    prompt = _PROMPT_RESPOSTA.format(pergunta=pergunta, resumo=resumo)
    texto = llm_client.gerar(prompt, json_mode=False, temperature=0.2)

    # Sem provedor / falha -> devolve o resumo (ja e fiel aos dados).
    return texto if texto else resumo


# ===========================================================================
# 3) ORQUESTRADOR DE PONTA A PONTA
# ===========================================================================

def _atualizar_historico(pergunta: str, resposta: str) -> None:
    """Adiciona a troca atual ao historico e descarta as mais antigas se necessario."""
    global _HISTORICO
    _HISTORICO.append({"role": "user", "content": pergunta})
    _HISTORICO.append({"role": "assistant", "content": resposta})
    # Mantém só os últimos _MAX_TURNOS_HISTORICO turnos (2 msgs por turno).
    max_msgs = _MAX_TURNOS_HISTORICO * 2
    if len(_HISTORICO) > max_msgs:
        _HISTORICO = _HISTORICO[-max_msgs:]


@metricas.medir_comando  # cronometra, classifica e grava a metrica em background
def processar(texto: str, texto_falado: str | None = None,
              operador: str | None = None) -> dict:
    """
    Pipeline completo de uma pergunta:
        seguranca -> interpretar -> rotear -> responder -> registrar.

    Parametros:
        texto        — o comando a processar (ja sem a wake word).
        texto_falado — opcional: a fala bruta do operador (com "Jarvis"), so
                       para o log. Se None, usa o proprio texto.
        operador     — opcional: quem falou (biometria de voz). Usado para
                       auditar quem PEDIU e quem AUTORIZOU cada acao no sistema.

    Retorna um dict com tudo (util para logs e para o painel da Fase 11):
        {
            "pergunta", "entidades", "intencao", "sistema",
            "resposta", "modo", "erro", "faltaram_dados"
        }

    REGRAS DE SEGURANCA aplicadas aqui (Fase 10):
        - pedido de alteracao de producao  -> recusa (modo somente leitura);
        - dados insuficientes              -> pede esclarecimento;
        - dados conflitantes               -> avisa em vez de adivinhar;
        - qualquer erro inesperado         -> resposta segura + log do erro.
    """
    modo = llm_client.provedor_ativo() or "local"
    falado = texto_falado if texto_falado is not None else texto

    entidades = dict(_ENTIDADES_VAZIAS)
    intencao = INTENCAO_DESCONHECIDA
    resultado = None
    erro = None
    faltaram_dados = False

    try:
        # --- Regra 2/3: bloquear pedidos de ALTERACAO antes de qualquer consulta
        if security.eh_pedido_de_alteracao(texto):
            intencao = "bloqueada_escrita"
            resposta = security.MSG_SOMENTE_LEITURA
            resultado = {"intencao": intencao, "ok": False,
                         "dados": None, "resumo": resposta, "insuficiente": False}
            return _finalizar(falado, texto, intencao, None, resultado,
                              resposta, erro, faltaram_dados, modo, entidades)

        # --- Confirmacao de acao pendente: operador disse "confirma" ou "cancela"
        # depois de a JARVIS ter pedido confirmacao de uma acao.
        if agente_acoes.tem_confirmacao_pendente():
            if agente_acoes.eh_confirmacao(texto):
                intencao = "acao_confirmada"
                # Quem diz "sim" e quem AUTORIZA — e o operador que vai pro log.
                resposta = agente_acoes.executar_confirmado(operador=operador)
                resultado = {"intencao": intencao, "ok": True, "dados": None,
                             "resumo": resposta, "insuficiente": False}
                return _finalizar(falado, texto, intencao, "sistema.acao",
                                  resultado, resposta, None, False, modo, entidades)
            if agente_acoes.eh_cancelamento(texto):
                intencao = "acao_cancelada"
                resposta = agente_acoes.cancelar_pendente()
                resultado = {"intencao": intencao, "ok": True, "dados": None,
                             "resumo": resposta, "insuficiente": False}
                return _finalizar(falado, texto, intencao, "sistema.acao",
                                  resultado, resposta, None, False, modo, entidades)
            # Texto ambiguo com acao pendente: lembra o operador da confirmacao.
            descricao = agente_acoes._PENDENTE["descricao"] if agente_acoes._PENDENTE else "acao"
            resposta = f"{descricao} — Confirma ou cancela?"
            resultado = {"intencao": "acao_aguardando", "ok": True, "dados": None,
                         "resumo": resposta, "insuficiente": False}
            return _finalizar(falado, texto, "acao_aguardando", "sistema.acao",
                              resultado, resposta, None, False, modo, entidades)

        # --- Pedido de ACAO (enviar/agendar/registrar): passa pro agente de acoes
        # que busca os dados, prepara a chamada e pede confirmacao ao operador.
        acoes_ok, _ = agente_acoes.disponivel()
        if acoes_ok and _eh_pedido_de_acao(texto):
            intencao = "acao_pendente"
            erro = None
            try:
                resposta = agente_acoes.interpretar(
                    texto, operador=operador, historico=list(_HISTORICO))
                resultado = {"intencao": intencao, "ok": True, "dados": None,
                             "resumo": resposta, "insuficiente": False}
                # Guarda a troca: a descricao ("Vou agendar X na M04") da contexto
                # a follow-ups, mesmo que a acao ainda dependa de confirmacao.
                _atualizar_historico(texto, resposta)
            except Exception as falha_acao:  # noqa: BLE001
                erro = f"{type(falha_acao).__name__}: {falha_acao}"
                resposta = "Tive um problema ao preparar essa acao. Pode repetir?"
                resultado = {"intencao": intencao, "ok": False, "dados": None,
                             "resumo": resposta, "insuficiente": False}
            return _finalizar(falado, texto, intencao, "sistema.acao",
                              resultado, resposta, erro, False, modo, entidades)

        # --- Caminho MCP (DADOS REAIS): se o sistema estiver conectado, o agente
        # responde usando as ferramentas de leitura do sistema real, em vez dos
        # JSONs simulados. So entra aqui quando ha token valido + Kilo ativo.
        mcp_ok, _ = agente_mcp.disponivel()
        if mcp_ok:
            intencao = "mcp_agente"
            erro = None
            try:
                resposta, ent_mcp = agente_mcp.responder(texto, historico=list(_HISTORICO))
                entidades = ent_mcp  # agente MCP popula as entidades
                resultado = {"intencao": intencao, "ok": True, "dados": None,
                             "resumo": resposta, "insuficiente": False}
                _atualizar_historico(texto, resposta)
            except Exception as falha_mcp:  # noqa: BLE001
                # Falha ao consultar o sistema real: NAO cai no mock (seria dado
                # errado). Responde com honestidade e registra o erro.
                erro = f"{type(falha_mcp).__name__}: {falha_mcp}"
                log.error("Falha no agente MCP para %r: %s", texto, erro)
                resposta = ("Tive um problema ao consultar o sistema agora. "
                            "Pode repetir daqui a pouco?")
                resultado = {"intencao": intencao, "ok": False, "dados": None,
                             "resumo": resposta, "insuficiente": False}
            funcao = "sistema.mcp"
            return _finalizar(falado, texto, intencao, funcao, resultado,
                              resposta, erro, faltaram_dados, modo, entidades)

        # --- Interpretacao + roteamento (MODO MOCK: dados simulados em /data)
        entidades = interpretar_comando_com_gemini(texto)
        intencao = entidades.get("intencao", INTENCAO_DESCONHECIDA)

        params = {
            "maquina":  entidades.get("maquina"),
            "data":     entidades.get("data"),
            "ticket":   entidades.get("ticket"),
            "peca":     entidades.get("peca"),
            "material": entidades.get("material"),
            "cor":      entidades.get("cor"),
        }

        resultado = command_router.route(intencao, params)
        faltaram_dados = bool(resultado.get("insuficiente"))

        # --- Regra 4: faltou informacao -> pedir esclarecimento
        if faltaram_dados:
            resposta = security.MSG_PEDIR_ESCLARECIMENTO
        # --- Regra 5: dados conflitantes -> avisar
        elif security.ha_conflito(resultado.get("dados")):
            resposta = security.MSG_CONFLITO
        else:
            resposta = responder(texto, resultado)

    except Exception as falha:  # noqa: BLE001 - nunca derrubar o fluxo de voz
        erro = f"{type(falha).__name__}: {falha}"
        log.error("Erro ao processar %r: %s", texto, erro)
        resposta = DADOS_INSUFICIENTES

    funcao = FUNCAO_POR_INTENCAO.get(intencao)
    return _finalizar(falado, texto, intencao, funcao, resultado,
                      resposta, erro, faltaram_dados, modo, entidades)


def _finalizar(falado, texto, intencao, funcao, resultado,
               resposta, erro, faltaram_dados, modo, entidades) -> dict:
    """Registra a interacao (Fase 10) e monta o dict de retorno."""
    dados_consultados = resultado.get("resumo") if isinstance(resultado, dict) else None

    registrar_interacao(
        texto_falado=falado,
        texto_transcrito=texto,
        intencao=intencao,
        funcao_chamada=funcao,
        dados_consultados=dados_consultados,
        resposta=resposta,
        erro=erro,
        faltaram_dados=faltaram_dados,
        modo=modo,
    )

    return {
        "pergunta": texto,
        "entidades": entidades,
        "intencao": intencao,
        "sistema": resultado,
        "resposta": resposta,
        "modo": modo,
        "erro": erro,
        "faltaram_dados": faltaram_dados,
    }
