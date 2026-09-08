"""
core/command_router.py
----------------------
Roteador de comandos (FASE 7).

Recebe a INTENCAO ja identificada (pelo Gemini ou pelo interpretador local) e
os PARAMETROS extraidos (maquina, data, ticket...), valida o minimo necessario
e chama a consulta correta no company_system.

REGRA DE OURO: este modulo nunca inventa dados. Tudo que ele devolve vem dos
arquivos JSON, atraves das funcoes das Fases 2 a 6. Se faltar parametro, devolve
a flag de dados insuficientes e nao chama nada.
"""

from company_system import machines
from company_system import production_rules
from company_system import tickets

# ---------------------------------------------------------------------------
# Intencoes reconhecidas (constantes para evitar erro de digitacao)
# ---------------------------------------------------------------------------
INTENCAO_PECA_COMPATIVEL   = "peca_compativel_maquina"
INTENCAO_FALTANTES_TICKET  = "pecas_faltantes_ticket"
INTENCAO_RECOMENDAR        = "recomendar_peca_ticket_maquina"
INTENCAO_STATUS_MAQUINA    = "status_maquina"
INTENCAO_STATUS_TICKET     = "status_ticket"
INTENCAO_CONVERSA_GERAL    = "conversa_geral"   # pergunta fora do dominio da fabrica
INTENCAO_DESCONHECIDA      = "desconhecida"

INTENCOES_VALIDAS = {
    INTENCAO_PECA_COMPATIVEL,
    INTENCAO_FALTANTES_TICKET,
    INTENCAO_RECOMENDAR,
    INTENCAO_STATUS_MAQUINA,
    INTENCAO_STATUS_TICKET,
    INTENCAO_CONVERSA_GERAL,
}

# Mensagem unica de dados insuficientes (sem acento, padrao do projeto p/ CMD)
DADOS_INSUFICIENTES = (
    "Nao encontrei informacao suficiente no sistema para responder com seguranca."
)

# Mapa intencao -> nome da funcao do company_system (usado nos logs da Fase 10)
FUNCAO_POR_INTENCAO = {
    INTENCAO_PECA_COMPATIVEL:  "production_rules.consultar_pecas_compativeis",
    INTENCAO_FALTANTES_TICKET: "tickets.consultar_pecas_faltantes_por_data",
    INTENCAO_RECOMENDAR:       "production_rules.recomendar_peca_do_ticket_para_maquina",
    INTENCAO_STATUS_MAQUINA:   "machines.descrever_maquina",
    INTENCAO_STATUS_TICKET:    "tickets.consultar_pecas_faltantes_por_data",
    INTENCAO_CONVERSA_GERAL:   "llm.conversa_geral",
    INTENCAO_DESCONHECIDA:     None,
}


def route(intencao: str, params: dict) -> dict:
    """
    Encaminha a intencao para a consulta certa do company_system.

    Parametros:
        intencao — uma das constantes INTENCAO_* acima
        params   — dict com chaves possiveis: maquina, data, ticket, peca,
                   material, cor (qualquer uma pode ser None)

    Retorno (sempre um dict padronizado):
        {
            "intencao":     intencao recebida,
            "ok":           True se encontrou dados uteis,
            "dados":        objeto bruto retornado pela funcao (list/dict/str),
            "resumo":       texto factual e fiel dos dados (base p/ a resposta),
            "insuficiente": True se faltou parametro essencial,
        }
    """
    params = params or {}
    maquina = params.get("maquina")
    data    = params.get("data")
    ticket  = params.get("ticket")

    if intencao == INTENCAO_PECA_COMPATIVEL:
        return _peca_compativel(maquina)

    if intencao == INTENCAO_FALTANTES_TICKET:
        return _faltantes_ticket(data, ticket)

    if intencao == INTENCAO_RECOMENDAR:
        return _recomendar(data, ticket, maquina)

    if intencao == INTENCAO_STATUS_MAQUINA:
        return _status_maquina(maquina)

    if intencao == INTENCAO_STATUS_TICKET:
        return _status_ticket(data, ticket)

    if intencao == INTENCAO_CONVERSA_GERAL:
        # Sem dados do company_system: o cerebro (gemini_brain) responde livremente.
        # Marcamos "livre" para sinalizar isso. Nao e dado da fabrica.
        return {"intencao": INTENCAO_CONVERSA_GERAL, "ok": True, "dados": None,
                "resumo": None, "insuficiente": False, "livre": True}

    # Intencao nao reconhecida -> dados insuficientes
    return _insuficiente(intencao)


# ---------------------------------------------------------------------------
# Helpers de montagem de resposta padronizada
# ---------------------------------------------------------------------------

def _resposta(intencao, ok, dados, resumo, insuficiente=False) -> dict:
    return {
        "intencao": intencao,
        "ok": ok,
        "dados": dados,
        "resumo": resumo,
        "insuficiente": insuficiente,
    }


def _insuficiente(intencao) -> dict:
    return _resposta(intencao, ok=False, dados=None,
                     resumo=DADOS_INSUFICIENTES, insuficiente=True)


# ---------------------------------------------------------------------------
# Handlers por intencao
# ---------------------------------------------------------------------------

def _peca_compativel(maquina) -> dict:
    if not maquina:
        return _insuficiente(INTENCAO_PECA_COMPATIVEL)

    dados = production_rules.consultar_pecas_compativeis(maquina)

    if isinstance(dados, str):
        # mensagem de erro/vazio do company_system (ex.: maquina nao existe)
        return _resposta(INTENCAO_PECA_COMPATIVEL, ok=False, dados=dados, resumo=dados)

    nomes = ", ".join(f"{p['nome']} (extrusor {p['extrusor']})" for p in dados)
    resumo = (
        f"A maquina {maquina.upper()} pode produzir sem trocar material: {nomes}. "
        + " ".join(p["motivo"] for p in dados)
    )
    return _resposta(INTENCAO_PECA_COMPATIVEL, ok=True, dados=dados, resumo=resumo)


def _faltantes_ticket(data, ticket) -> dict:
    if not data and not ticket:
        return _insuficiente(INTENCAO_FALTANTES_TICKET)

    if ticket:
        dados = tickets.consultar_pecas_faltantes_por_id(ticket)
    else:
        dados = tickets.consultar_pecas_faltantes_por_data(data)

    if isinstance(dados, str):
        # TICKET_NAO_ENCONTRADO, TICKET_COMPLETO ou ambiguidade de data
        return _resposta(INTENCAO_FALTANTES_TICKET, ok=False, dados=dados, resumo=dados)

    partes = [
        f"{item['quantidade_faltante']} {item['nome']} ({item['material']} {item['cor']})"
        for item in dados
    ]
    ref = dados[0]
    resumo = (
        f"No ticket {ref['ticket_id']} (dia {ref['data']}, cliente {ref['cliente']}) "
        f"ainda faltam: " + "; ".join(partes) + "."
    )
    return _resposta(INTENCAO_FALTANTES_TICKET, ok=True, dados=dados, resumo=resumo)


def _recomendar(data, ticket, maquina) -> dict:
    if not maquina or (not data and not ticket):
        return _insuficiente(INTENCAO_RECOMENDAR)

    # A funcao da Fase 6 trabalha por data. Se veio so o ticket, resolvemos a
    # data a partir do proprio ticket para reaproveitar a regra de negocio.
    if not data and ticket:
        t = tickets.consultar_ticket(ticket)
        if isinstance(t, str):
            return _resposta(INTENCAO_RECOMENDAR, ok=False, dados=t, resumo=t)
        data = t["date"]

    dados = production_rules.recomendar_peca_do_ticket_para_maquina(data, maquina)

    if isinstance(dados, str):
        return _resposta(INTENCAO_RECOMENDAR, ok=False, dados=dados, resumo=dados)

    resumo = " ".join(r["recomendacao"] for r in dados)
    return _resposta(INTENCAO_RECOMENDAR, ok=True, dados=dados, resumo=resumo)


def _status_maquina(maquina) -> dict:
    if not maquina:
        return _insuficiente(INTENCAO_STATUS_MAQUINA)

    descricao = machines.descrever_maquina(maquina)

    if descricao == machines.MAQUINA_NAO_ENCONTRADA:
        return _resposta(INTENCAO_STATUS_MAQUINA, ok=False,
                         dados=descricao, resumo=descricao)

    return _resposta(INTENCAO_STATUS_MAQUINA, ok=True,
                     dados=descricao, resumo=descricao)


def _status_ticket(data, ticket) -> dict:
    if not data and not ticket:
        return _insuficiente(INTENCAO_STATUS_TICKET)

    if ticket:
        dados = tickets.consultar_pecas_faltantes_por_id(ticket)
        ref_txt = ticket
    else:
        dados = tickets.consultar_pecas_faltantes_por_data(data)
        ref_txt = f"do dia {data}"

    # Caso completo: a funcao retorna TICKET_COMPLETO
    if dados == tickets.TICKET_COMPLETO:
        resumo = f"O ticket {ref_txt} esta completo. Todas as pecas foram produzidas."
        return _resposta(INTENCAO_STATUS_TICKET, ok=True, dados=dados, resumo=resumo)

    # Outras strings: nao encontrado ou ambiguidade
    if isinstance(dados, str):
        return _resposta(INTENCAO_STATUS_TICKET, ok=False, dados=dados, resumo=dados)

    # Lista: ainda ha pendencias
    total_faltante = sum(item["quantidade_faltante"] for item in dados)
    ref = dados[0]
    partes = [f"{i['quantidade_faltante']} {i['nome']}" for i in dados]
    resumo = (
        f"O ticket {ref['ticket_id']} ({ref_txt}) NAO esta completo. "
        f"Ainda faltam {total_faltante} pecas no total: " + "; ".join(partes) + "."
    )
    return _resposta(INTENCAO_STATUS_TICKET, ok=True, dados=dados, resumo=resumo)
