"""
core/metricas.py
----------------
Métricas de impacto da JARVIS — logs silenciosos em SQLite (FASE 14).

Objetivo: registrar, em segundo plano, TUDO o que a JARVIS faz, para depois
provar em NÚMEROS o ganho de produtividade e a economia de tempo/dinheiro.

Princípios deste módulo:
    - 100% LOCAL e gratuito (SQLite nativo do Python). Nada sai da máquina.
    - SILENCIOSO: nunca escreve na interface (HTML/painel). Só backend.
    - NÃO PODE DERRUBAR A JARVIS: todo acesso ao banco é protegido
      (try/except/finally) e o INSERT roda numa thread em background. Se o banco
      falhar, a JARVIS continua respondendo normalmente — só perde aquele registro.

Como encaixar no projeto (ver exemplos no fim do arquivo):
    - Forma recomendada: decore a função que processa o comando com
      @medir_comando (ela cronometra, classifica e salva sozinha).
    - Forma manual: use o bloco time.perf_counter() + registrar_metrica_background.
"""

import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from functools import wraps
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.metricas")

# Banco local, ao lado dos demais dados do projeto.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "metricas.db"

# Estimativa de quanto cada tipo de tarefa levaria MANUALMENTE (em segundos),
# sem a JARVIS. É a base do cálculo de "tempo economizado". Ajuste conforme a
# realidade do lab — são os números que sustentam o relatório pra diretoria.
TEMPO_MANUAL_ESTIMADO_S = {
    "acao_executada":     300,   # mandar imprimir/agendar na mão: achar peça, fatiamento, enviar
    "acao_preparada":      60,   # preparar uma ação (sem confirmar)
    "consulta":            90,   # consultar status/produção no sistema manualmente
    "ticket":             120,   # abrir e ler um ticket no sistema
    "maquina":             90,   # verificar o estado de uma impressora
    "peca":               120,   # descobrir que peça cabe numa máquina
    "pergunta_geral":      30,   # tirar uma dúvida rápida
    "bloqueada":            0,   # pedido recusado: não houve trabalho automatizado
    "outro":               60,
}
_TEMPO_MANUAL_PADRAO_S = 60

STATUS_SUCESSO = "Sucesso"
STATUS_ERRO = "Erro"


# ===========================================================================
# 1) Inicialização do banco
# ===========================================================================

def inicializar_db() -> None:
    """
    Cria o banco e a tabela `historico_jarvis` se ainda não existirem.
    Idempotente: pode ser chamada toda vez no boot, sem efeito colateral.

    Onde encaixar: chame uma vez no boot da JARVIS (ex.: em main.py, junto da
    carga inicial). Mesmo se você esquecer, registrar_metrica_background garante
    a tabela antes de inserir.
    """
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(DB_PATH)) as conexao:
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS historico_jarvis (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    comando_recebido        TEXT    NOT NULL,
                    categoria               TEXT    NOT NULL,
                    tempo_execucao_segundos REAL    NOT NULL,
                    status                  TEXT    NOT NULL,
                    data_hora               TEXT    NOT NULL
                )
                """
            )
            conexao.commit()
        log.info("Banco de métricas pronto em %s", DB_PATH)
    except Exception as erro:  # noqa: BLE001 - métricas nunca derrubam a JARVIS
        log.warning("Falha ao inicializar o banco de métricas: %s", erro)


# ===========================================================================
# 2) Registro silencioso (em background)
# ===========================================================================

def registrar_metrica_background(comando: str, categoria: str,
                                 tempo_gasto: float, status: str) -> None:
    """
    Registra UMA métrica de forma silenciosa e NÃO-BLOQUEANTE.

    Dispara uma thread em background para o INSERT, então o fluxo da JARVIS
    (voz/painel) segue na hora, sem esperar o disco. Qualquer erro é engolido:
    perder um registro nunca pode travar o atendimento.

    Parâmetros:
        comando      — o texto do comando recebido (ex.: "status da M04").
        categoria    — tipo do comando (ver classificar_categoria()).
        tempo_gasto  — segundos que a JARVIS levou (use time.perf_counter()).
        status       — STATUS_SUCESSO ("Sucesso") ou STATUS_ERRO ("Erro").
    """
    thread = threading.Thread(
        target=_inserir,
        args=(comando, categoria, float(tempo_gasto), status),
        daemon=True,  # não impede a JARVIS de encerrar
    )
    thread.start()


def _inserir(comando: str, categoria: str, tempo_gasto: float, status: str) -> None:
    """INSERT propriamente dito (roda na thread). Abre/fecha sua conexão."""
    conexao = None
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conexao = sqlite3.connect(DB_PATH)
        # Garante a tabela mesmo se inicializar_db() não tiver sido chamada.
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS historico_jarvis (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                comando_recebido        TEXT    NOT NULL,
                categoria               TEXT    NOT NULL,
                tempo_execucao_segundos REAL    NOT NULL,
                status                  TEXT    NOT NULL,
                data_hora               TEXT    NOT NULL
            )
            """
        )
        conexao.execute(
            """
            INSERT INTO historico_jarvis
                (comando_recebido, categoria, tempo_execucao_segundos, status, data_hora)
            VALUES (?, ?, ?, ?, ?)
            """,
            (comando, categoria, round(tempo_gasto, 3), status,
             datetime.now().isoformat(timespec="seconds")),
        )
        conexao.commit()
    except Exception as erro:  # noqa: BLE001 - silencioso por design
        log.debug("Não foi possível gravar a métrica: %s", erro)
    finally:
        if conexao is not None:
            conexao.close()


# ===========================================================================
# 3) Classificação e envelopamento (decorator)
# ===========================================================================

def classificar_categoria(intencao: str | None) -> str:
    """
    Traduz a 'intencao' interna da JARVIS numa CATEGORIA de métrica legível.
    Centralizado aqui para o resto do código não precisar conhecer as regras.
    """
    mapa = {
        "acao_confirmada":          "acao_executada",
        "acao_pendente":            "acao_preparada",
        "acao_aguardando":          "acao_preparada",
        "acao_cancelada":           "acao_preparada",
        "mcp_agente":               "consulta",
        "status_maquina":           "maquina",
        "peca_compativel_maquina":  "peca",
        "status_ticket":            "ticket",
        "pecas_faltantes_ticket":   "ticket",
        "recomendar_peca_ticket_maquina": "ticket",
        "conversa_geral":           "pergunta_geral",
        "bloqueada_escrita":        "bloqueada",
    }
    return mapa.get(intencao or "", "outro")


def medir_comando(func):
    """
    Decorator que ENVELOPA uma função de processamento de comando para medir o
    tempo e registrar a métrica em background — sem poluir a lógica de negócio.

    Espera que o 1º argumento seja o texto do comando e que o retorno seja o
    dict da JARVIS (com 'intencao' e 'erro'); mesmo sem isso, ele não quebra.

    Uso (uma linha em cima da sua função atual):
        @medir_comando
        def processar(texto, ...):
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        comando = args[0] if args else kwargs.get("texto", "")
        inicio = time.perf_counter()
        resultado = None
        status = STATUS_SUCESSO
        try:
            resultado = func(*args, **kwargs)
            return resultado
        except Exception:
            status = STATUS_ERRO
            raise  # não engole o erro do negócio — só marca a métrica como Erro
        finally:
            tempo_gasto = time.perf_counter() - inicio
            intencao = None
            if isinstance(resultado, dict):
                intencao = resultado.get("intencao")
                if resultado.get("erro"):
                    status = STATUS_ERRO
            categoria = classificar_categoria(intencao)
            registrar_metrica_background(str(comando), categoria, tempo_gasto, status)

    return wrapper


# ===========================================================================
# 4) Relatório para a diretoria (rode no terminal)
# ===========================================================================

def gerar_relatorio_diretoria() -> dict:
    """
    Lê o banco e imprime no terminal um resumo estatístico do impacto da JARVIS:
    total de comandos automatizados, taxa de sucesso e tempo economizado.

    Devolve também um dict com os números (útil para testes ou exportação).
    Rode com:  python -m core.metricas   (ou  python core/metricas.py)
    """
    vazio = {"total": 0, "sucessos": 0, "taxa_sucesso": 0.0,
             "tempo_jarvis_s": 0.0, "tempo_manual_s": 0.0, "economia_s": 0.0,
             "por_categoria": {}}
    conexao = None
    try:
        conexao = sqlite3.connect(DB_PATH)
        conexao.row_factory = sqlite3.Row
        linhas = conexao.execute(
            "SELECT categoria, tempo_execucao_segundos, status FROM historico_jarvis"
        ).fetchall()
    except Exception as erro:  # noqa: BLE001
        print(f"Não foi possível ler o banco de métricas: {erro}")
        return vazio
    finally:
        if conexao is not None:
            conexao.close()

    if not linhas:
        print("Ainda não há métricas registradas.")
        return vazio

    total = len(linhas)
    sucessos = sum(1 for ln in linhas if ln["status"] == STATUS_SUCESSO)
    tempo_jarvis = sum(ln["tempo_execucao_segundos"] for ln in linhas)

    # Tempo manual estimado considera só os comandos que deram certo (um erro
    # não substituiu trabalho humano nenhum).
    tempo_manual = 0.0
    por_categoria: dict[str, int] = {}
    for ln in linhas:
        por_categoria[ln["categoria"]] = por_categoria.get(ln["categoria"], 0) + 1
        if ln["status"] == STATUS_SUCESSO:
            tempo_manual += TEMPO_MANUAL_ESTIMADO_S.get(
                ln["categoria"], _TEMPO_MANUAL_PADRAO_S
            )

    economia = max(0.0, tempo_manual - tempo_jarvis)
    taxa = (sucessos / total * 100.0) if total else 0.0

    _imprimir_relatorio(total, sucessos, taxa, tempo_jarvis, tempo_manual,
                        economia, por_categoria)

    return {"total": total, "sucessos": sucessos, "taxa_sucesso": round(taxa, 1),
            "tempo_jarvis_s": round(tempo_jarvis, 1),
            "tempo_manual_s": round(tempo_manual, 1),
            "economia_s": round(economia, 1), "por_categoria": por_categoria}


def _fmt_duracao(segundos: float) -> str:
    """Formata segundos como '1h 23min' / '45min' / '30s' (legível)."""
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos}min"
    if minutos:
        return f"{minutos}min {seg}s"
    return f"{seg}s"


def _imprimir_relatorio(total: int, sucessos: int, taxa: float,
                        tempo_jarvis: float, tempo_manual: float,
                        economia: float, por_categoria: dict) -> None:
    """Desenha o relatório no terminal (separado para manter a leitura limpa)."""
    print("=" * 56)
    print("  RELATÓRIO DE IMPACTO DA JARVIS — RESUMO DIRETORIA")
    print("=" * 56)
    print(f"  Comandos automatizados ....... {total}")
    print(f"  Sucessos ..................... {sucessos}")
    print(f"  Taxa de sucesso .............. {taxa:.1f}%")
    print("  " + "-" * 52)
    print(f"  Tempo que a JARVIS gastou .... {_fmt_duracao(tempo_jarvis)}")
    print(f"  Tempo manual equivalente ..... {_fmt_duracao(tempo_manual)}")
    print(f"  >> TEMPO ECONOMIZADO ......... {_fmt_duracao(economia)}")
    print("  " + "-" * 52)
    print("  Comandos por categoria:")
    for categoria, qtd in sorted(por_categoria.items(), key=lambda x: -x[1]):
        print(f"     {categoria:<18} {qtd}")
    print("=" * 56)


# ===========================================================================
# 5) EXEMPLO PRÁTICO — como envelopar suas funções atuais
# ===========================================================================
#
# Você tem hoje, em core/gemini_brain.py, a função que processa o comando:
#
#     def processar(texto, texto_falado=None, operador=None) -> dict:
#         ...
#         return { "intencao": ..., "erro": ..., "resposta": ... }
#
# --- FORMA 1 (recomendada): decorator, uma linha só ------------------------
#
#     from core import metricas
#
#     @metricas.medir_comando          # <-- ENCAIXE AQUI, em cima da função
#     def processar(texto, texto_falado=None, operador=None) -> dict:
#         ...                          # nada muda no corpo
#
# --- FORMA 2 (manual): com o módulo time, quando quiser controle fino ------
#
#     import time
#     from core import metricas
#
#     def atender_comando_de_voz(texto):
#         inicio = time.perf_counter()            # <-- começa o cronômetro
#         status = metricas.STATUS_SUCESSO
#         try:
#             resultado = gemini_brain.processar(texto)
#             if resultado.get("erro"):
#                 status = metricas.STATUS_ERRO
#             return resultado
#         except Exception:
#             status = metricas.STATUS_ERRO
#             raise
#         finally:
#             tempo = time.perf_counter() - inicio  # <-- para o cronômetro
#             categoria = metricas.classificar_categoria(
#                 resultado.get("intencao") if "resultado" in dir() else None
#             )
#             metricas.registrar_metrica_background(texto, categoria, tempo, status)
#
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # "Função secreta": rode no terminal para ver o impacto acumulado.
    #   python -m core.metricas
    gerar_relatorio_diretoria()
