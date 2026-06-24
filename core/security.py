"""
core/security.py
----------------
Camada de seguranca e guarda de integridade (FASE 10).

Centraliza as REGRAS INVIOLAVEIS da JARVIS:

  1. A JARVIS NAO inventa dados      -> so responde com base no company_system.
  2. A JARVIS NAO altera producao    -> nenhuma escrita/edicao/exclusao.
  3. Nesta versao ela SO consulta    -> modo SOMENTE LEITURA.
  4. Faltou informacao               -> pede esclarecimento.
  5. Dados conflitantes              -> avisa, nao escolhe sozinha.
  6. Maquina inexistente             -> "Nao encontrei essa maquina no sistema."
  7. Ticket inexistente              -> "Nao encontrei esse ticket no sistema."

As regras 1, 6 e 7 ja sao garantidas estruturalmente pelo company_system (os
dados so vem dos JSONs; consultas a itens inexistentes retornam a mensagem
padrao). Este modulo reforca as regras 2-5 e concentra as mensagens oficiais.
"""

import re
import unicodedata

# Reaproveita as mensagens ja padronizadas nas fases anteriores.
from company_system.machines import MAQUINA_NAO_ENCONTRADA
from company_system.tickets import TICKET_NAO_ENCONTRADO
from core.command_router import DADOS_INSUFICIENTES

# ---------------------------------------------------------------------------
# Modo SOMENTE LEITURA
# ---------------------------------------------------------------------------
READ_ONLY = True

MSG_SOMENTE_LEITURA = (
    "Nesta versao eu apenas consulto dados. Nao posso alterar, criar nem apagar "
    "informacoes de producao."
)

MSG_PEDIR_ESCLARECIMENTO = (
    "Nao encontrei informacao suficiente no sistema para responder com seguranca. "
    "Pode me dizer a maquina, a data ou o numero do ticket?"
)

MSG_CONFLITO = (
    "Encontrei dados conflitantes no sistema. Por seguranca, prefiro nao adivinhar: "
    "confira a informacao e me diga qual usar."
)


def assert_read_only(operation: str) -> None:
    """
    Barreira fisica contra escrita. Qualquer tentativa de operacao que altere
    dados de producao levanta PermissionError. Use isto ao redor de qualquer
    futura funcao de escrita para garantir o modo somente leitura.
    """
    if READ_ONLY:
        raise PermissionError(
            f"Operacao '{operation}' bloqueada: JARVIS esta em modo SOMENTE LEITURA."
        )


# ---------------------------------------------------------------------------
# Regra 2 e 3: detectar pedidos de ALTERACAO (que devem ser recusados)
# ---------------------------------------------------------------------------

# Verbos/expressoes que indicam intencao de MODIFICAR producao.
# Obs.: "trocar material" sozinho NAO entra aqui, pois "sem trocar material"
# e uma consulta legitima. So bloqueamos verbos de comando no imperativo/infinitivo
# claramente ligados a escrita.
_PADROES_ESCRITA = (
    r"\bapagar\b", r"\bapague\b", r"\bdeletar\b", r"\bdelete\b",
    r"\bremover\b", r"\bremova\b", r"\bexcluir\b", r"\bexclua\b",
    r"\balterar\b", r"\baltere\b", r"\beditar\b", r"\bedite\b",
    r"\batualizar\b", r"\batualize\b", r"\bcadastrar\b", r"\bcadastre\b",
    r"\bcriar\b", r"\bcrie\b", r"\bgravar\b", r"\bgrave\b", r"\bsalvar\b", r"\bsalve\b",
    r"\bmarcar como\b", r"\bmarque como\b", r"\bdefinir\b", r"\bdefina\b",
    r"\bzerar\b", r"\bzere\b", r"\bmudar o status\b", r"\bmude o status\b",
    r"\bdar baixa\b", r"\bde baixa\b",
    # Acoes de PRODUCAO do sistema real que continuam BLOQUEADAS (nao estao entre
    # as 4 acoes liberadas). As 4 liberadas (enviar_para_impressora,
    # agendar_mesa_impressao, alterar_impressora_agendamento,
    # registrar_resultado_impressao) sao tratadas pelo agente_acoes com confirmacao
    # obrigatoria — por isso seus verbos NAO ficam mais aqui.
    r"\bfatiar\b", r"\bfatie\b",
    r"\bmontar\b", r"\bmonte\b", r"\btransferir\b", r"\btransfira\b",
)


def _sem_acento(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def eh_pedido_de_alteracao(texto: str) -> bool:
    """
    True se a fala do operador tenta ALTERAR/criar/apagar dados de producao.
    Usado para recusar a acao com MSG_SOMENTE_LEITURA antes de qualquer consulta.
    """
    if not texto:
        return False
    limpo = _sem_acento(texto)
    return any(re.search(p, limpo) for p in _PADROES_ESCRITA)


# ---------------------------------------------------------------------------
# Regra 5: detectar dados conflitantes
# ---------------------------------------------------------------------------

def ha_conflito(dados) -> bool:
    """
    Deteccao defensiva de conflito nos dados consultados.

    Exemplos de conflito tratados:
      - dois extrusores diferentes com o MESMO id na mesma maquina;
      - quantidade pronta maior que a necessaria num item de ticket.

    A ambiguidade de data (mais de um ticket) ja e tratada no modulo de tickets,
    que devolve uma mensagem pedindo o numero — nao precisa entrar aqui.
    """
    if isinstance(dados, dict) and "extruders" in dados:
        ids = [e.get("id") for e in dados.get("extruders", [])]
        if len(ids) != len(set(ids)):
            return True

    if isinstance(dados, list):
        for item in dados:
            if not isinstance(item, dict):
                continue
            req = item.get("quantidade_necessaria")
            feito = item.get("quantidade_pronta")
            if isinstance(req, int) and isinstance(feito, int) and feito > req:
                return True

    return False


# ---------------------------------------------------------------------------
# Mensagens oficiais reexportadas (fonte unica de verdade para as fases)
# ---------------------------------------------------------------------------
__all__ = [
    "READ_ONLY", "assert_read_only",
    "MSG_SOMENTE_LEITURA", "MSG_PEDIR_ESCLARECIMENTO", "MSG_CONFLITO",
    "MAQUINA_NAO_ENCONTRADA", "TICKET_NAO_ENCONTRADO", "DADOS_INSUFICIENTES",
    "eh_pedido_de_alteracao", "ha_conflito",
]
