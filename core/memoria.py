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
     Preferencias, decisoes, contexto recorrente do lab — persistem
     indefinidamente, sem limite de janela como o historico.

Banco: data/memoria.db (SQLite, mesmo padrao usado em core/metricas.py).
Nunca levanta excecao: falha em qualquer operacao aqui degrada (memoria
vazia/nao guardada) em vez de derrubar o fluxo de conversa.
"""

import sqlite3
from contextlib import closing
from datetime import datetime
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


def _conectar() -> sqlite3.Connection:
    """
    Abre a conexao e GARANTE as tabelas, nesta ordem — porque o painel
    (dashboard/serve.py) chama gemini_brain direto, sem passar pelo boot do
    main.py, entao inicializar_db() pode nunca ter rodado nesse processo.
    Mesmo padrao defensivo usado em core/metricas.py.
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
            fato TEXT NOT NULL
        )
    """)
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


def lembrar_fato(fato: str) -> str:
    """
    Guarda um fato de longo prazo. Chamado PELA PROPRIA LLM, via ferramenta —
    ela decide sozinha o que vale a pena guardar (preferencia de um operador,
    uma decisao tomada, um contexto recorrente do lab).
    """
    fato = (fato or "").strip()
    if not fato:
        return "Nada pra lembrar (fato vazio)."
    try:
        with closing(_conectar()) as conexao:
            conexao.execute(
                "INSERT INTO fatos (timestamp, fato) VALUES (?, ?)",
                (datetime.now().isoformat(timespec="seconds"), fato),
            )
            conexao.commit()
        log.info("Memoria: novo fato guardado: %s", fato[:80])
        return "Guardado na memoria."
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao guardar fato: %s", erro)
        return "Nao consegui guardar isso agora."


def buscar_fatos(consulta: str = "", limite: int = 8) -> list[str]:
    """
    Busca fatos guardados por palavra-chave (igual ao core/conhecimento.py:
    busca simples por sobreposicao de palavras, sem precisar de embeddings).
    Sem consulta, devolve os mais recentes.
    """
    try:
        with closing(_conectar()) as conexao:
            conexao.row_factory = sqlite3.Row
            linhas = conexao.execute(
                "SELECT fato FROM fatos ORDER BY id DESC LIMIT 300"
            ).fetchall()
        fatos = [ln["fato"] for ln in linhas]
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao buscar fatos: %s", erro)
        return []

    consulta = (consulta or "").strip().lower()
    if not consulta:
        return fatos[:limite]

    palavras = set(consulta.split())
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
    """Fatos mais recentes, pra injetar como memoria 'ambiente' no prompt."""
    return buscar_fatos("", limite)


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
