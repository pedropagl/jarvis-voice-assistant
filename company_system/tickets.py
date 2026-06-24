"""
company_system/tickets.py
-------------------------
Consultas ao sistema de tickets (ordens de producao).
Os dados vem de database.load_all() — somente leitura.

FASE 5 — Base de tickets.

Regras de negocio:
  - Data sem ano → usa o ano corrente.
  - Mais de um ticket na mesma data → pede o numero do ticket.
  - Faltante = quantity_required - quantity_done.
  - Material e cor sao buscados em parts.json via part_id (sem duplicar dados).
"""

import datetime
from company_system import database

# Mensagens padrao
TICKET_NAO_ENCONTRADO   = "Nao encontrei esse ticket no sistema."
TICKET_COMPLETO         = "Esse ticket esta completo."
AMBIGUIDADE_DE_DATA     = (
    "Encontrei mais de um ticket nessa data. "
    "Qual o numero do ticket? Ex.: TK-2026-0523-01."
)

ANO_ATUAL = datetime.date.today().year


# ---------------------------------------------------------------------------
# Auxiliares privados
# ---------------------------------------------------------------------------

def _todos() -> list[dict]:
    return database.load_all()["tickets"]


def _mapa_pecas() -> dict[str, dict]:
    """Devolve {part_id: peca} para busca rapida de material/cor."""
    return {p["id"]: p for p in database.load_all()["parts"]}


def _normalizar_data(entrada: str) -> str | None:
    """
    Converte varios formatos de data para 'YYYY-MM-DD'.
    Formatos aceitos:
        2026-05-23        (ISO, com ano)
        23/05/2026        (BR, com ano)
        23/05             (BR, sem ano — assume ano atual)
        05-23             (MM-DD, sem ano — assume ano atual)
    Retorna None se nao reconhecer o formato.
    """
    entrada = entrada.strip()

    formatos = [
        ("%Y-%m-%d", entrada),
        ("%d/%m/%Y", entrada),
        ("%d/%m",    entrada),   # sem ano
        ("%m-%d",    entrada),   # sem ano
    ]

    for fmt, val in formatos:
        try:
            dt = datetime.datetime.strptime(val, fmt)
            # Se o formato nao tem ano, strptime usa 1900; substituimos pelo atual
            if dt.year == 1900:
                dt = dt.replace(year=ANO_ATUAL)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _calcular_faltantes(ticket: dict, mapa_pecas: dict[str, dict]) -> list[dict]:
    """
    Retorna os itens do ticket que ainda nao foram concluidos.
    Cada item devolvido inclui material e cor (via cross-reference com parts.json).
    """
    faltantes = []
    for item in ticket.get("items", []):
        falta = item["quantity_required"] - item["quantity_done"]
        if falta <= 0:
            continue

        peca = mapa_pecas.get(item["part_id"], {})
        faltantes.append({
            "part_id":            item["part_id"],
            "nome":               item["part_name"],
            "quantidade_necessaria": item["quantity_required"],
            "quantidade_pronta":     item["quantity_done"],
            "quantidade_faltante":   falta,
            "material":           peca.get("required_material", "nao informado"),
            "cor":                peca.get("required_color",    "nao informado"),
        })

    return faltantes


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def listar_tickets() -> list[dict]:
    """Retorna todos os tickets com campos resumidos."""
    return [
        {
            "id":       t["id"],
            "data":     t["date"],
            "cliente":  t["client"],
            "setor":    t.get("sector", "nao informado"),
            "prioridade": t.get("priority", "nao informada"),
            "status":   t["status"],
            "observacoes": t.get("notes", ""),
        }
        for t in _todos()
    ]


def consultar_ticket(ticket_id: str) -> dict | str:
    """
    Busca um ticket pelo ID exato (ex.: 'TK-2026-0523-01').
    Retorna o dict completo ou TICKET_NAO_ENCONTRADO.
    """
    tid = ticket_id.strip().upper()
    for t in _todos():
        if t["id"].upper() == tid:
            return t
    return TICKET_NAO_ENCONTRADO


def consultar_pecas_faltantes_por_data(data: str) -> list[dict] | str:
    """
    Localiza o ticket pela data e retorna as pecas que ainda faltam.

    Parametro:
        data — qualquer formato reconhecido por _normalizar_data().
               Ex.: '23/05', '23/05/2026', '2026-05-23'.

    Retorno (em ordem de prioridade):
        str  TICKET_NAO_ENCONTRADO    — nenhum ticket nessa data
        str  AMBIGUIDADE_DE_DATA      — mais de um ticket na data
        str  TICKET_COMPLETO          — ticket existe mas todas as pecas prontas
        list de dicts com os campos:
             part_id, nome, quantidade_necessaria, quantidade_pronta,
             quantidade_faltante, material, cor
             + meta: ticket_id, data, cliente, setor, prioridade, status
    """
    data_normalizada = _normalizar_data(data)
    if data_normalizada is None:
        return TICKET_NAO_ENCONTRADO

    tickets_na_data = [t for t in _todos() if t["date"] == data_normalizada]

    if not tickets_na_data:
        return TICKET_NAO_ENCONTRADO

    if len(tickets_na_data) > 1:
        ids = ", ".join(t["id"] for t in tickets_na_data)
        return (
            f"Encontrei mais de um ticket nessa data ({ids}). "
            f"Qual o numero do ticket?"
        )

    ticket = tickets_na_data[0]
    mapa   = _mapa_pecas()
    faltantes = _calcular_faltantes(ticket, mapa)

    if not faltantes:
        return TICKET_COMPLETO

    # Injeta metadados do ticket em cada item para facilitar exibicao
    for item in faltantes:
        item["ticket_id"]  = ticket["id"]
        item["data"]       = ticket["date"]
        item["cliente"]    = ticket["client"]
        item["setor"]      = ticket.get("sector", "nao informado")
        item["prioridade"] = ticket.get("priority", "nao informada")
        item["status"]     = ticket["status"]

    return faltantes


def consultar_pecas_faltantes_por_id(ticket_id: str) -> list[dict] | str:
    """
    Igual a consultar_pecas_faltantes_por_data, mas recebe o ID direto.
    Util quando o operador informa o numero apos a pergunta de ambiguidade.
    """
    ticket = consultar_ticket(ticket_id)
    if ticket == TICKET_NAO_ENCONTRADO:
        return TICKET_NAO_ENCONTRADO

    mapa = _mapa_pecas()
    faltantes = _calcular_faltantes(ticket, mapa)

    if not faltantes:
        return TICKET_COMPLETO

    for item in faltantes:
        item["ticket_id"]  = ticket["id"]
        item["data"]       = ticket["date"]
        item["cliente"]    = ticket["client"]
        item["setor"]      = ticket.get("sector", "nao informado")
        item["prioridade"] = ticket.get("priority", "nao informada")
        item["status"]     = ticket["status"]

    return faltantes
