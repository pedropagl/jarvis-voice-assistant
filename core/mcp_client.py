"""
core/mcp_client.py
------------------
Cliente MCP da JARVIS — conexao com o sistema real da empresa (sistema).

O sistema e um servidor MCP (Model Context Protocol) que da acesso aos dados
REAIS de producao: tickets, status das impressoras, producao pendente,
agendamentos, fatiamentos (Gcode) e envio para as impressoras.

A JARVIS conecta como CLIENTE MCP e usa as ferramentas do sistema para responder
com dados reais (em vez dos JSONs simulados em /data).

REGRAS (inviolaveis):
    1. SOMENTE LEITURA. A JARVIS so expoe e so chama ferramentas de leitura
       (consultar tickets, status de impressora, etc.). Qualquer ferramenta que
       executa ACAO no sistema (enviar para impressora, fatiar, alterar, cancelar)
       e BLOQUEADA aqui no codigo — ver eh_somente_leitura() e chamar_ferramenta().
    2. O TOKEN vive so no .env (MCP_TOKEN), nunca no codigo. Mesma regra do
       KILO_API_KEY. Ver [[llm-provedor-kilo-sem-free]].

Configuracao (.env):
    MCP_URL      = http://SEU_SERVIDOR:18080/mcp/
    MCP_TOKEN    = <token fornecido pelo administrador>
    MCP_ENABLED  = auto | 1 | 0   (auto = liga se URL+token validos existirem)

Conexao: usa o transporte "Streamable HTTP" do SDK oficial `mcp` diretamente
(sem depender do npx/mcp-remote em runtime). O endpoint /mcp/ + Bearer indica
exatamente esse transporte.
"""

import asyncio
import os
import re

from core.logger import get_logger

log = get_logger("jarvis.mcp")

# Verbos que indicam ACAO (escrita/efeito colateral). Se o nome da ferramenta
# contiver qualquer um destes — e nao houver anotacao explicita de leitura —,
# ela e tratada como ACAO e NUNCA exposta nem chamada pela JARVIS.
_VERBOS_ACAO = (
    "send", "enviar", "envio", "print", "imprim", "delete", "apag", "remov",
    "cancel", "cancelar", "slice", "fati", "upload", "subir", "create", "criar",
    "update", "atualiz", "edit", "editar", "alter", "modif", "set", "definir",
    "start", "iniciar", "stop", "parar", "pause", "pausar", "resume", "retomar",
    "move", "mover", "write", "escrever", "execute", "executar", "exec", "run",
    "dispatch", "enqueue", "queue", "agendar", "schedule", "reschedule", "abort",
    "reset", "reboot", "restart", "reiniciar", "pausa", "deletar", "post", "put",
    "patch", "approve", "aprovar", "reject", "rejeitar", "assign", "atribuir",
)

# Palavras que indicam LEITURA/consulta. Usadas quando NAO ha anotacao no schema:
# so liberamos ferramentas sem anotacao se o nome parecer claramente de consulta.
_VERBOS_LEITURA = (
    "get", "list", "listar", "consult", "consultar", "status", "find", "buscar",
    "busca", "search", "pesquisar", "read", "ler", "fetch", "show", "mostrar",
    "info", "detail", "detalh", "query", "ver", "obter", "describe", "descrever",
    "report", "relatorio", "available", "disponiv", "view", "summary", "resumo",
    "count", "contar", "history", "historico", "current", "atual",
)


# ===========================================================================
# Configuracao e disponibilidade
# ===========================================================================

def _config() -> dict:
    return {
        "url": os.getenv("MCP_URL", "").strip(),
        "token": os.getenv("MCP_TOKEN", "").strip(),
        "enabled": os.getenv("MCP_ENABLED", "auto").strip().lower(),
    }


def _token_placeholder(token: str) -> bool:
    """True se o token for o placeholder (ex.: 'XXXXXX') ou claramente invalido."""
    if not token:
        return True
    t = token.upper()
    # Tudo X (XXXX...), ou marcadores comuns de 'preencher depois'.
    if set(t) <= {"X"}:
        return True
    return t in ("TOKEN", "SEU_TOKEN", "BEARER", "CHANGEME", "TODO")


def disponivel() -> tuple[bool, str]:
    """
    Diz se da pra usar o MCP do sistema agora.
    Retorna (True, "") ou (False, motivo legivel).
    """
    cfg = _config()

    if cfg["enabled"] in ("0", "false", "nao", "off", "desligado"):
        return False, "MCP desativado no .env (MCP_ENABLED=0)"

    if not cfg["url"]:
        return False, "MCP_URL nao configurada no .env"

    if _token_placeholder(cfg["token"]):
        return False, ("MCP_TOKEN ausente/placeholder — peca o token ao administrador "
                       "e coloque no .env")

    try:
        import mcp  # noqa: F401 — so verifica se a lib esta instalada
    except ImportError:
        return False, "biblioteca 'mcp' nao instalada (pip install mcp)"

    return True, ""


# ===========================================================================
# Sessao MCP (assincrona) + wrappers sincronos
# ===========================================================================

def _abrir_sessao():
    """
    Devolve um async context manager que entrega uma ClientSession ja
    inicializada e conectada ao sistema via Streamable HTTP.
    Uso interno (dentro de funcoes async).
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        cfg = _config()
        headers = {"Authorization": f"Bearer {cfg['token']}"}
        async with streamablehttp_client(cfg["url"], headers=headers) as (read, write, _):
            async with ClientSession(read, write) as sessao:
                await sessao.initialize()
                yield sessao

    return _ctx()


def _rodar(coro):
    """Executa uma corrotina num loop novo (a JARVIS e sincrona)."""
    return asyncio.run(coro)


# ===========================================================================
# Listagem de ferramentas
# ===========================================================================

def eh_somente_leitura(tool) -> bool:
    """
    Decide se uma ferramenta do MCP e SEGURA (somente leitura) para a JARVIS usar.

    Politica conservadora (na duvida, NEGA):
      1. annotations.readOnlyHint == True            -> LEITURA  (libera)
      2. annotations.readOnlyHint == False           -> ACAO     (bloqueia)
      3. annotations.destructiveHint == True         -> ACAO     (bloqueia)
      4. sem anotacao e nome com verbo de ACAO        -> ACAO     (bloqueia)
      5. sem anotacao e nome com verbo de LEITURA     -> LEITURA  (libera)
      6. sem anotacao e nome ambiguo                  -> ACAO     (bloqueia, por seguranca)
    """
    ann = getattr(tool, "annotations", None)
    if ann is not None:
        ro = getattr(ann, "readOnlyHint", None)
        if ro is True:
            return True
        if ro is False:
            return False
        if getattr(ann, "destructiveHint", None) is True:
            return False

    nome = (getattr(tool, "name", "") or "").lower()

    # O VERBO-PREFIXO (primeiro pedaco do nome) decide primeiro. O sistema usa a
    # convencao verbo_substantivo, entao 'listar_fatiamentos' e LEITURA mesmo
    # contendo 'fati' (que e verbo de acao em 'enviar fatiamento').
    primeiro = re.split(r"[^a-z]+", nome, maxsplit=1)[0] if nome else ""

    def _prefixo_bate(verbos) -> bool:
        return bool(primeiro) and any(
            primeiro == v or primeiro.startswith(v) for v in verbos
        )

    if _prefixo_bate(_VERBOS_LEITURA):
        return True
    if _prefixo_bate(_VERBOS_ACAO):
        return False

    # Sem prefixo claro: qualquer verbo de acao no nome -> acao.
    if any(v in nome for v in _VERBOS_ACAO):
        return False
    if any(v in nome for v in _VERBOS_LEITURA):
        return True
    # Ambiguo e sem anotacao: por seguranca, trata como acao (nao expoe).
    return False


def _tool_para_dict(tool) -> dict:
    """Converte um Tool do SDK num dict simples (para log/diagnostico)."""
    return {
        "name": getattr(tool, "name", "?"),
        "description": (getattr(tool, "description", "") or "").strip(),
        "input_schema": getattr(tool, "inputSchema", None) or {},
        "somente_leitura": eh_somente_leitura(tool),
    }


def listar_ferramentas() -> list[dict]:
    """
    Conecta ao sistema e devolve a lista de ferramentas (todas), cada uma como dict
    com: name, description, input_schema, somente_leitura.
    Levanta excecao se a conexao falhar (o chamador trata).
    """
    async def _async():
        async with _abrir_sessao() as sessao:
            resp = await sessao.list_tools()
            return [_tool_para_dict(t) for t in resp.tools]

    return _rodar(_async())


# Cache das ferramentas de leitura. Os schemas do sistema nao mudam durante uma
# sessao, entao buscamos UMA vez e reusamos — economiza um round-trip de rede
# (e tokens de processamento) a cada pergunta.
_CACHE_LEITURA: list[dict] | None = None


def ferramentas_de_leitura(forcar: bool = False) -> list[dict]:
    """
    So as ferramentas SEGURAS (somente leitura) — as unicas que a JARVIS usa.
    Resultado fica em cache; passe forcar=True para reconsultar o servidor.
    """
    global _CACHE_LEITURA
    if _CACHE_LEITURA is not None and not forcar:
        return _CACHE_LEITURA
    _CACHE_LEITURA = [t for t in listar_ferramentas() if t["somente_leitura"]]
    # Ja deixa o conjunto liberado pronto (trava de seguranca).
    registrar_liberadas([t["name"] for t in _CACHE_LEITURA])
    return _CACHE_LEITURA


def limpar_cache() -> None:
    """Esquece o cache de ferramentas (forca nova consulta na proxima chamada)."""
    global _CACHE_LEITURA
    _CACHE_LEITURA = None


# ===========================================================================
# Chamada de ferramenta (com trava de seguranca)
# ===========================================================================

class FerramentaBloqueada(Exception):
    """Levantada quando se tenta chamar uma ferramenta que NAO e de leitura."""


# Cache do conjunto de ferramentas liberadas (preenchido por ferramentas_de_leitura
# ou pelo agente). Evita reconsultar o servidor a cada chamada so para validar.
_LIBERADAS: set[str] | None = None


def registrar_liberadas(nomes: list[str]) -> None:
    """O agente informa quais ferramentas (de leitura) estao liberadas nesta sessao."""
    global _LIBERADAS
    _LIBERADAS = set(nomes)


def chamar_ferramenta(nome: str, argumentos: dict | None = None) -> str:
    """
    Chama uma ferramenta de LEITURA do sistema e devolve o resultado como texto.

    TRAVA DE SEGURANCA: se a ferramenta nao estiver no conjunto de liberadas
    (somente leitura), levanta FerramentaBloqueada — NUNCA executa acao.
    """
    if _LIBERADAS is not None and nome not in _LIBERADAS:
        raise FerramentaBloqueada(
            f"Ferramenta '{nome}' nao e de leitura (ou nao liberada). Bloqueada por seguranca."
        )

    async def _async():
        async with _abrir_sessao() as sessao:
            resultado = await sessao.call_tool(nome, argumentos or {})
            return _extrair_texto(resultado)

    return _rodar(_async())


def _extrair_texto(resultado) -> str:
    """Extrai texto legivel de um CallToolResult (lista de blocos de conteudo)."""
    partes = []
    for bloco in getattr(resultado, "content", []) or []:
        texto = getattr(bloco, "text", None)
        if texto:
            partes.append(texto)
            continue
        # Conteudo estruturado/outros tipos: serializa de forma defensiva.
        dados = getattr(bloco, "data", None)
        if dados is not None:
            partes.append(str(dados))
    if not partes:
        # Alguns servidores devolvem structuredContent em vez de content textual.
        estruturado = getattr(resultado, "structuredContent", None)
        if estruturado is not None:
            import json
            try:
                return json.dumps(estruturado, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                return str(estruturado)
    return "\n".join(partes) if partes else ""
