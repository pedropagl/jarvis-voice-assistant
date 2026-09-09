"""
core/memoria.py
-----------------
Memoria PERSISTENTE e COMPARTILHADA da JARVIS — sobrevive a reinicios do
processo e e a mesma pra qualquer pessoa do lab (nao e por operador).

Dois tipos de memoria:
  1. Historico de conversa: as ultimas trocas (pergunta/resposta), pra dar
     contexto entre turnos — e agora entre REINICIOS tambem (antes ficava
     so em RAM, sumia toda vez que o processo caia/reiniciava).
  2. Fatos de longo prazo: coisas que a propria JARVIS decide guardar, via
     a ferramenta lembrar_fato (exposta aos agentes de tool-calling).
     Preferencias, decisoes, contexto recorrente do lab.
     - Podem ter VALIDADE (validade_dias): fatos temporarios (ex.: "M04
       reservada por 2 semanas") somem sozinhos depois de vencer, em vez
       de ficar pra sempre.
     - Busca SEMANTICA (por significado, via embeddings) quando o provedor
       Kilo estiver disponivel; cai para busca por palavra-chave (Jaccard,
       sem custo de API) se os embeddings falharem ou nao existirem ainda —
       nunca quebra por causa disso.

Banco: data/memoria.db (SQLite, mesmo padrao usado em core/metricas.py).
Nunca levanta excecao: falha em qualquer operacao aqui degrada (memoria
vazia/nao guardada) em vez de derrubar o fluxo de conversa.
"""

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.memoria")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memoria.db"

# "Alta memoria": janela bem maior que os 3 turnos (6 mensagens) de antes.
# 40 mensagens = ~20 trocas de pergunta/resposta ainda cabem folgado no
# contexto do modelo (mensagens curtas, tom falado) sem estourar custo.
JANELA_HISTORICO_MENSAGENS = 40

# Quantos fatos de longo prazo entram automaticamente no prompt (memoria
# "ambiente" — o agente ja chega sabendo, sem precisar chamar buscar_fatos).
FATOS_NO_PROMPT = 12

# Modelo de embeddings usado na busca semantica (compativel com a API da
# OpenAI, via gateway Kilo). So e usado se o provedor Kilo estiver ativo.
MODELO_EMBEDDING = "text-embedding-3-small"


def _conectar() -> sqlite3.Connection:
    """
    Abre a conexao e GARANTE as tabelas/colunas, nesta ordem — porque o
    painel (dashboard/serve.py) chama gemini_brain direto, sem passar pelo
    boot do main.py, entao inicializar_db() pode nunca ter rodado nesse
    processo. Mesmo padrao defensivo usado em core/metricas.py.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(DB_PATH)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            role TEXT NOT NULL,
            conteudo TEXT NOT NULL
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS fatos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            fato TEXT NOT NULL,
            expira_em TEXT,
            embedding TEXT
        )
    """)
    # Migracao leve: bancos criados antes da validade/embeddings existirem
    # nao tem essas colunas ainda. SQLite nao suporta "ADD COLUMN IF NOT
    # EXISTS", entao tenta e ignora o erro de coluna duplicada.
    for coluna, tipo in (("expira_em", "TEXT"), ("embedding", "TEXT")):
        try:
            conexao.execute(f"ALTER TABLE fatos ADD COLUMN {coluna} {tipo}")
        except sqlite3.OperationalError:
            pass  # coluna ja existe
    return conexao


def inicializar_db() -> None:
    """Cria as tabelas se ainda nao existirem. Chamar uma vez no boot (idempotente)."""
    try:
        with closing(_conectar()) as conexao:
            conexao.commit()
        log.info("Banco de memoria pronto em %s", DB_PATH)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao inicializar banco de memoria: %s", erro)


def salvar_turno(role: str, conteudo: str) -> None:
    """Persiste uma mensagem (user ou assistant) no historico de conversa."""
    if not conteudo:
        return
    try:
        with closing(_conectar()) as conexao:
            conexao.execute(
                "INSERT INTO historico (timestamp, role, conteudo) VALUES (?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), role, conteudo),
            )
            conexao.commit()
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao salvar turno na memoria: %s", erro)


def carregar_historico(limite_mensagens: int = JANELA_HISTORICO_MENSAGENS) -> list[dict]:
    """
    Devolve as ultimas mensagens em ordem cronologica, no formato usado pelos
    agentes de tool-calling: [{"role": "user"|"assistant", "content": "..."}].
    """
    try:
        with closing(_conectar()) as conexao:
            conexao.row_factory = sqlite3.Row
            linhas = conexao.execute(
                "SELECT role, conteudo FROM historico ORDER BY id DESC LIMIT ?",
                (limite_mensagens,),
            ).fetchall()
        return [{"role": ln["role"], "content": ln["conteudo"]} for ln in reversed(linhas)]
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao carregar historico da memoria: %s", erro)
        return []


def _gerar_embedding(texto: str) -> list[float] | None:
    """
    Gera o vetor de embedding de um texto via Kilo (API compativel com a
    OpenAI). Devolve None se o Kilo nao estiver disponivel ou a chamada
    falhar — quem chama trata isso como "sem busca semantica agora".
    """
    try:
        from core import llm_client
        cliente, _ = llm_client.cliente_kilo()
        if cliente is None:
            return None
        resp = cliente.embeddings.create(model=MODELO_EMBEDDING, input=texto)
        return resp.data[0].embedding
    except Exception as erro:  # noqa: BLE001
        log.debug("Embedding indisponivel (%s); busca semantica cai para palavra-chave.", erro)
        return None


def _similaridade_cosseno(a: list[float], b: list[float]) -> float:
    produto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return produto / (norma_a * norma_b)


def lembrar_fato(fato: str, validade_dias: int | None = None) -> str:
    """
    Guarda um fato de longo prazo. Chamado PELA PROPRIA LLM, via ferramenta —
    ela decide sozinha o que vale a pena guardar (preferencia de um operador,
    uma decisao tomada, um contexto recorrente do lab).

    `validade_dias` — se o fato tem prazo (ex.: "M04 reservada por 2 semanas"
    -> validade_dias=14), ele para de aparecer nas buscas/memoria ambiente
    sozinho depois de vencer, em vez de ficar guardado pra sempre.
    """
    fato = (fato or "").strip()
    if not fato:
        return "Nada pra lembrar (fato vazio)."

    expira_em = None
    if validade_dias:
        try:
            expira_em = (datetime.now() + timedelta(days=int(validade_dias))).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            expira_em = None

    embedding = _gerar_embedding(fato)
    embedding_json = json.dumps(embedding) if embedding else None

    try:
        with closing(_conectar()) as conexao:
            conexao.execute(
                "INSERT INTO fatos (timestamp, fato, expira_em, embedding) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), fato, expira_em, embedding_json),
            )
            conexao.commit()
        log.info("Memoria: novo fato guardado (validade=%s dias): %s",
                 validade_dias or "sem prazo", fato[:80])
        return "Guardado na memoria." + (f" Vence em {validade_dias} dias." if validade_dias else "")
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao guardar fato: %s", erro)
        return "Nao consegui guardar isso agora."


def _fatos_validos(conexao: sqlite3.Connection) -> list[sqlite3.Row]:
    """Fatos que ainda nao venceram (expira_em nulo OU no futuro)."""
    agora = datetime.now().isoformat(timespec="seconds")
    conexao.row_factory = sqlite3.Row
    return conexao.execute(
        "SELECT fato, embedding FROM fatos "
        "WHERE expira_em IS NULL OR expira_em > ? "
        "ORDER BY id DESC LIMIT 300",
        (agora,),
    ).fetchall()


def buscar_fatos(consulta: str = "", limite: int = 8) -> list[str]:
    """
    Busca fatos guardados (ja excluindo os vencidos). Tenta busca SEMANTICA
    (por significado, via embedding) primeiro; se nao houver provedor ou
    embeddings salvos, cai para busca por palavra-chave (Jaccard, sem custo
    de API — igual ao core/conhecimento.py). Sem consulta, devolve os
    mais recentes.
    """
    try:
        with closing(_conectar()) as conexao:
            linhas = _fatos_validos(conexao)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao buscar fatos: %s", erro)
        return []

    fatos = [ln["fato"] for ln in linhas]
    consulta = (consulta or "").strip()
    if not consulta:
        return fatos[:limite]

    # --- Tentativa 1: busca semantica (embeddings) ---
    com_embedding = [(ln["fato"], json.loads(ln["embedding"]))
                      for ln in linhas if ln["embedding"]]
    if com_embedding:
        consulta_emb = _gerar_embedding(consulta)
        if consulta_emb:
            pontuados = [
                (_similaridade_cosseno(consulta_emb, emb), f)
                for f, emb in com_embedding
            ]
            pontuados.sort(key=lambda x: -x[0])
            # Limiar baixo: so descarta o claramente irrelevante.
            relevantes = [f for pontos, f in pontuados if pontos >= 0.2]
            if relevantes:
                return relevantes[:limite]

    # --- Fallback: busca por palavra-chave ---
    consulta_lower = consulta.lower()
    palavras = set(consulta_lower.split())
    pontuados = []
    for f in fatos:
        fl = f.lower()
        pontos = sum(1 for p in palavras if p in fl)
        if pontos > 0:
            pontuados.append((pontos, f))
    pontuados.sort(key=lambda x: -x[0])
    encontrados = [f for _, f in pontuados[:limite]]
    return encontrados if encontrados else fatos[:limite]


def listar_fatos_recentes(limite: int = FATOS_NO_PROMPT) -> list[str]:
    """Fatos mais recentes E ainda validos, pra injetar como memoria 'ambiente'."""
    return buscar_fatos("", limite)


def limpar_fatos_vencidos() -> int:
    """Remove do banco os fatos com validade ja expirada. Devolve quantos foram removidos."""
    try:
        agora = datetime.now().isoformat(timespec="seconds")
        with closing(_conectar()) as conexao:
            cursor = conexao.execute(
                "DELETE FROM fatos WHERE expira_em IS NOT NULL AND expira_em <= ?", (agora,)
            )
            conexao.commit()
            removidos = cursor.rowcount
        if removidos:
            log.info("Memoria: %d fato(s) vencido(s) removido(s).", removidos)
        return removidos
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao limpar fatos vencidos: %s", erro)
        return 0


def esquecer_tudo() -> None:
    """Apaga toda a memoria (historico + fatos). So uso manual/depuracao."""
    try:
        with closing(_conectar()) as conexao:
            conexao.execute("DELETE FROM historico")
            conexao.execute("DELETE FROM fatos")
            conexao.commit()
        log.info("Memoria apagada por completo.")
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao apagar memoria: %s", erro)
