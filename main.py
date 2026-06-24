"""
main.py
-------
Ponto de entrada da JARVIS.

FASE 1 - Arquitetura: boot, logs e carga das bases simuladas.
FASE 2 - Base de maquinas: testa as consultas de maquinas (machines.py).
FASE 3 - Base de pecas: testa as consultas de pecas (parts.py).
FASE 4 - Compatibilidade maquina x peca: testa production_rules.py.
FASE 5 - Sistema de tickets: testa tickets.py.
FASE 6 - Cruzamento ticket x maquina: testa production_rules.recomendar_peca_do_ticket_para_maquina.
FASE 7 - Integracao com Gemini: interpreta a pergunta, roteia e responde (gemini_brain.py).
FASE 8 - Entrada de voz: ouve o microfone, detecta "Jarvis" e envia ao sistema.
FASE 9 - Saida de voz: fala a resposta final em audio (voice_output.py).
FASE 10 - Logs e seguranca: registra cada interacao e aplica as regras (security.py).
FASE 13 - Biometria de voz: identifica QUEM do lab esta falando (biometria.py),
          100% local, sem gastar tokens de API.

A JARVIS opera em modo SOMENTE LEITURA.

Uso:
    python main.py                 -> boot + demos das Fases 2 a 10 (simuladas).
    python main.py --chat          -> conversa DIGITANDO (sem microfone, sem falar).
    python main.py --chat --voz    -> conversa digitando E ela responde em audio.
    python main.py --mic           -> microfone ao vivo (+ biometria, se cadastrada).
    python main.py --cadastrar-voz -> cadastra a voz das pessoas do lab (Fase 13).
    python main.py mcp             -> conecta ao sistema e LISTA as ferramentas (sem acoes).

FASE 14 - Dados reais via MCP (sistema): quando MCP_URL/TOKEN estao no .env,
          a JARVIS usa os dados REAIS do sistema (tickets, impressoras, producao)
          em vez dos JSONs simulados. Ver core/mcp_client.py e core/agente_mcp.py.
"""

import os
import sys

# Garante UTF-8 na saida do terminal: as respostas do modelo trazem acentos,
# e o CMD do Windows (cp1252) poderia quebrar com UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Carrega o .env ANTES de importar os modulos core (eles leem as variaveis de
# ambiente, como o modelo do LLM). Se python-dotenv nao existir, segue sem.
def _carregar_env():
    """Carrega o .env do projeto. Tenta dotenv; se nao tiver, le manualmente."""
    from pathlib import Path
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    # Fallback: leitura manual sem dependencia externa.
    import os
    with open(env_path, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if chave and chave not in os.environ:
                os.environ[chave] = valor

_carregar_env()

from core.logger import get_logger
from company_system import database
from company_system import machines
from company_system import parts
from company_system import production_rules
from company_system import tickets
from core import biometria
from core import gemini_brain
from core import mcp_client
from core import metricas
from core import painel
from core import security
from core import voice_input
from core import voice_output
from core import wake_word
from core.logger import INTERACOES_FILE

log = get_logger("jarvis.boot")

# Biometria de voz (Fase 13): identifica QUEM fala. ATIVA — necessaria para
# auditar quem autoriza cada acao no sistema (quem mandou imprimir/agendar).
# A identificacao roda 100% local, nao gasta tokens e nao altera a resposta;
# se precisar desligar (ex.: depuracao), mude para False.
BIOMETRIA_ATIVA = True

BANNER = r"""
   ____.  _____ __________ ____   _________
  |    | /  _  \\______   \\   \ /   /\_   ___ \
  |    |/  /_\  \|       _/\   Y   / /    \  \/
/\__|  /    |    \    |   \ \     /  \     \____
\________\____|__  /____|_  /  \___/    \______  /
                 \/       \/                   \/
        Assistente de producao - modo SOMENTE LEITURA
"""


def boot() -> dict:
    """Sequencia de boot (Fase 1). Devolve o estado carregado."""
    print(BANNER)
    log.info("Iniciando JARVIS...")

    ambiente = os.getenv("JARVIS_ENV", "desenvolvimento")
    log.info("Ambiente: %s", ambiente)

    if not os.getenv("GEMINI_API_KEY"):
        log.info("GEMINI_API_KEY ainda nao configurada (normal ate a Fase 7).")

    try:
        dados = database.load_all()
    except FileNotFoundError as erro:
        log.error("Falha ao carregar dados: %s", erro)
        sys.exit(1)

    log.info("Maquinas carregadas: %d", len(dados["machines"]))
    log.info("Pecas carregadas: %d", len(dados["parts"]))
    log.info("Tickets carregados: %d", len(dados["tickets"]))

    # Banco de metricas de impacto (silencioso, em background). Idempotente.
    metricas.inicializar_db()

    print("\n--- RESUMO DO BOOT ---")
    from core import llm_client, voice_output
    llm_status = f"KILO ({llm_client.nome_modelo()})" if llm_client.disponivel() else f"LOCAL ({llm_client.motivo_local()})"
    _, tts_motivo = voice_output.voz_disponivel()
    tts_status = f"edge-tts ({os.getenv('JARVIS_TTS_VOICE','?')})" if not tts_motivo else f"pyttsx3 (edge falhou)"

    print(f"  Maquinas : {len(dados['machines'])}")
    print(f"  Pecas    : {len(dados['parts'])}")
    print(f"  Tickets  : {len(dados['tickets'])}")
    mcp_ok, mcp_motivo = mcp_client.disponivel()
    if mcp_ok:
        # Esquenta o cache de ferramentas (1 consulta) — primeira pergunta fica
        # rapida e ja validamos a conexao com o sistema no boot.
        try:
            n = len(mcp_client.ferramentas_de_leitura())
            mcp_status = f"sistema CONECTADO ({n} ferramentas de leitura)"
        except Exception as erro:  # noqa: BLE001
            mcp_status = f"sistema indisponivel agora ({erro}); usando MOCK"
    else:
        mcp_status = f"MOCK / JSON local ({mcp_motivo})"

    print(f"  LLM      : {llm_status}")
    print(f"  Voz      : {tts_status}")
    print(f"  Dados    : {mcp_status}")
    print(f"  Modo     : SOMENTE LEITURA")
    print("----------------------")

    log.info("JARVIS iniciada com sucesso.")
    return dados


def demo_maquinas() -> None:
    """Teste das funcoes da Fase 2 (base de maquinas)."""
    print("\n========== FASE 2 - BASE DE MAQUINAS ==========\n")

    print(">> listar_maquinas(): codigos cadastrados")
    for m in machines.listar_maquinas():
        print(f"   - {m['id']} ({m['status']})")

    print("\n>> consultar_maquina('m04'):")
    m04 = machines.consultar_maquina("m04")
    print(f"   Codigo : {m04['id']}")
    print(f"   Status : {m04['status']}")
    print(f"   Obs.   : {m04['notes']}")

    print("\n>> consultar_materiais_da_maquina('M04'):")
    for item in machines.consultar_materiais_da_maquina("M04"):
        print(f"   Extrusor {item['extrusor']}: {item['material']} {item['cor']}")

    print("\n>> descrever_maquina('M04'):")
    print(f"   {machines.descrever_maquina('M04')}")

    print("\n>> consultar_maquina('M99') (nao existe):")
    print(f"   {machines.consultar_maquina('M99')}")

    print("\n===============================================\n")


def demo_pecas() -> None:
    """Teste das funcoes da Fase 3 (base de pecas)."""
    print("\n========== FASE 3 - BASE DE PECAS ==========\n")

    print(">> listar_pecas(): todas as pecas cadastradas")
    for p in parts.listar_pecas():
        qtd = p["quantidade_disponivel"]
        qtd_str = str(qtd) if qtd is not None else "nao informado"
        print(f"   [{p['id']}] {p['nome']} | {p['material']} {p['cor']} | "
              f"extrusor {p['extrusor']} | {p['tempo_min']} min | estoque: {qtd_str}")

    print("\n>> consultar_peca('capa protetora'):")
    peca = parts.consultar_peca("capa protetora")
    if isinstance(peca, dict):
        print(f"   ID       : {peca['id']}")
        print(f"   Nome     : {peca['nome']}")
        print(f"   Material : {peca['material']} {peca['cor']}")
        print(f"   Extrusor : {peca['extrusor']}")
        print(f"   Tempo    : {peca['tempo_min']} min")
        print(f"   Estoque  : {peca['quantidade_disponivel']}")
        print(f"   Obs.     : {peca['observacoes']}")
    else:
        print(f"   {peca}")

    print("\n>> consultar_peca('P001') (pelo codigo):")
    peca_codigo = parts.consultar_peca("P001")
    if isinstance(peca_codigo, dict):
        print(f"   Encontrada: {peca_codigo['nome']} ({peca_codigo['id']})")
    else:
        print(f"   {peca_codigo}")

    print("\n>> buscar_pecas_por_material_e_cor('APEX', 'vermelho'):")
    resultado = parts.buscar_pecas_por_material_e_cor("APEX", "vermelho")
    if isinstance(resultado, list):
        for p in resultado:
            print(f"   [{p['id']}] {p['nome']} — {p['observacoes']}")
    else:
        print(f"   {resultado}")

    print("\n>> buscar_pecas_por_material_e_cor('APEX', 'preto') (tampa frontal):")
    resultado2 = parts.buscar_pecas_por_material_e_cor("APEX", "preto")
    if isinstance(resultado2, list):
        for p in resultado2:
            print(f"   [{p['id']}] {p['nome']}")
    else:
        print(f"   {resultado2}")

    print("\n>> consultar_peca('peca inexistente'):")
    print(f"   {parts.consultar_peca('peca inexistente')}")

    print("\n>> buscar_pecas_por_material_e_cor('APEX', 'verde') (nao existe):")
    print(f"   {parts.buscar_pecas_por_material_e_cor('APEX', 'verde')}")

    print("\n=============================================\n")


def demo_compatibilidade() -> None:
    """Teste das funcoes da Fase 4 (compatibilidade maquina x peca)."""
    print("\n========== FASE 4 - COMPATIBILIDADE MAQUINA x PECA ==========\n")

    # Cenario 1: M04 — dois extrusores (APEX vermelho e APEX preto)
    print(">> consultar_pecas_compativeis('M04'):")
    resultado = production_rules.consultar_pecas_compativeis("M04")
    if isinstance(resultado, list):
        for p in resultado:
            print(f"\n   [{p['id']}] {p['nome']}")
            print(f"   Extrusor : {p['extrusor']}")
            print(f"   Material : {p['material']} {p['cor']}")
            print(f"   Tempo    : {p['tempo_min']} min")
            print(f"   Estoque  : {p['quantidade_disponivel']}")
            print(f"   Motivo   : {p['motivo']}")
    else:
        print(f"   {resultado}")

    # Cenario 2: M01 — sem extrusores com material compativel a mais de uma peca
    print("\n>> consultar_pecas_compativeis('M01'):")
    resultado_m01 = production_rules.consultar_pecas_compativeis("M01")
    if isinstance(resultado_m01, list):
        for p in resultado_m01:
            print(f"   [{p['id']}] {p['nome']} — {p['motivo']}")
    else:
        print(f"   {resultado_m01}")

    # Cenario 3: M03 — em manutencao, mas a funcao ainda retorna compatibilidade
    print("\n>> consultar_pecas_compativeis('M03'):")
    resultado_m03 = production_rules.consultar_pecas_compativeis("M03")
    if isinstance(resultado_m03, list):
        for p in resultado_m03:
            print(f"   [{p['id']}] {p['nome']} — {p['motivo']}")
    else:
        print(f"   {resultado_m03}")

    # Cenario 4: maquina inexistente
    print("\n>> consultar_pecas_compativeis('M99') (nao existe):")
    print(f"   {production_rules.consultar_pecas_compativeis('M99')}")

    # Cenario 5: entrada com case e espacos variados
    print("\n>> consultar_pecas_compativeis(' m04 ') (entrada suja):")
    resultado_suja = production_rules.consultar_pecas_compativeis(" m04 ")
    if isinstance(resultado_suja, list):
        print(f"   Encontradas {len(resultado_suja)} peca(s): "
              + ", ".join(p["nome"] for p in resultado_suja))
    else:
        print(f"   {resultado_suja}")

    print("\n==============================================================\n")


def _exibir_faltantes(resultado) -> None:
    """Utilitario: imprime o resultado de consulta de pecas faltantes."""
    if isinstance(resultado, str):
        print(f"   {resultado}")
        return
    for item in resultado:
        falta = item["quantidade_faltante"]
        total = item["quantidade_necessaria"]
        pronta = item["quantidade_pronta"]
        print(f"   [{item['part_id']}] {item['nome']}")
        print(f"          Necessaria : {total}  |  Pronta : {pronta}  |  Faltante : {falta}")
        print(f"          Material   : {item['material']} {item['cor']}")


def demo_tickets() -> None:
    """Teste das funcoes da Fase 5 (sistema de tickets)."""
    print("\n========== FASE 5 - SISTEMA DE TICKETS ==========\n")

    # Listagem geral
    print(">> listar_tickets():")
    for t in tickets.listar_tickets():
        print(f"   [{t['id']}] {t['data']} | {t['cliente']} | {t['status']} | prioridade: {t['prioridade']}")

    # Cenario 1: data exata (ISO) — ticket TK-2026-0523-01
    print("\n>> consultar_pecas_faltantes_por_data('2026-05-23'):")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("2026-05-23"))

    # Cenario 2: mesmo ticket, formato BR sem ano (usa ano atual = 2026)
    print("\n>> consultar_pecas_faltantes_por_data('23/05')  [sem ano, usa 2026]:")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("23/05"))

    # Cenario 3: formato BR completo
    print("\n>> consultar_pecas_faltantes_por_data('23/05/2026'):")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("23/05/2026"))

    # Cenario 4: ticket do dia 28 (tem peca completa + peca faltante)
    print("\n>> consultar_pecas_faltantes_por_data('28/05/2026')  [base de fixacao completa]:")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("28/05/2026"))

    # Cenario 5: ticket ainda nao iniciado (02/06)
    print("\n>> consultar_pecas_faltantes_por_data('02/06/2026')  [aguardando, tudo faltando]:")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("02/06/2026"))

    # Cenario 6: data inexistente
    print("\n>> consultar_pecas_faltantes_por_data('10/06/2026')  [nao existe]:")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_data("10/06/2026"))

    # Cenario 7: busca direta por ID
    print("\n>> consultar_pecas_faltantes_por_id('TK-2026-0523-01'):")
    _exibir_faltantes(tickets.consultar_pecas_faltantes_por_id("TK-2026-0523-01"))

    print("\n==================================================\n")


def demo_cruzamento() -> None:
    """Teste das funcoes da Fase 6 (cruzamento ticket x maquina)."""
    print("\n========== FASE 6 - CRUZAMENTO TICKET x MAQUINA ==========\n")

    def exibir(resultado) -> None:
        if isinstance(resultado, str):
            print(f"   {resultado}")
            return
        for r in resultado:
            print(f"\n   [{r['part_id']}] {r['nome']}")
            print(f"   Ticket   : {r['ticket_id']} ({r['cliente']})")
            print(f"   Material : {r['material']} {r['cor']}  |  Extrusor {r['extrusor']}")
            print(f"   Faltam   : {r['quantidade_faltante']} de {r['quantidade_necessaria']}  |  Tempo: {r['tempo_min']} min cada")
            print(f"   >>> {r['recomendacao']}")

    # Cenario 1 — caso exato do enunciado: ticket 23/05, M04
    print(">> recomendar_peca_do_ticket_para_maquina('23/05/2026', 'M04'):")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("23/05/2026", "M04"))

    # Cenario 2 — mesmo ticket, formato sem ano
    print("\n>> recomendar_peca_do_ticket_para_maquina('23/05', 'M04')  [sem ano]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("23/05", "M04"))

    # Cenario 3 — ticket 23/05, M02 (ABS cinza): suporte lateral bate
    print("\n>> recomendar_peca_do_ticket_para_maquina('23/05/2026', 'M02')  [ABS cinza]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("23/05/2026", "M02"))

    # Cenario 4 — ticket 28/05, M04 (tampa frontal falta, APEX preto bate)
    print("\n>> recomendar_peca_do_ticket_para_maquina('28/05/2026', 'M04')  [tampa frontal]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("28/05/2026", "M04"))

    # Cenario 5 — nenhuma peca compativel: ticket 23/05, M05 (PLA vermelho)
    print("\n>> recomendar_peca_do_ticket_para_maquina('23/05/2026', 'M05')  [sem compativel]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("23/05/2026", "M05"))

    # Cenario 6 — maquina inexistente
    print("\n>> recomendar_peca_do_ticket_para_maquina('23/05/2026', 'M99')  [maquina nao existe]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("23/05/2026", "M99"))

    # Cenario 7 — ticket inexistente
    print("\n>> recomendar_peca_do_ticket_para_maquina('10/06/2026', 'M04')  [ticket nao existe]:")
    exibir(production_rules.recomendar_peca_do_ticket_para_maquina("10/06/2026", "M04"))

    print("\n============================================================\n")


def demo_gemini() -> None:
    """Teste do pipeline da Fase 7 (interpretar -> rotear -> responder)."""
    print("\n========== FASE 7 - INTEGRACAO COM GEMINI ==========\n")

    from core import llm_client
    if llm_client.disponivel():
        modo = f"{llm_client.provedor_ativo().upper()} (modelo: {llm_client.nome_modelo()})"
    else:
        modo = f"LOCAL ({llm_client.motivo_local()})"
    print(f"Modo de interpretacao: {modo}")
    print("(Em qualquer modo, os DADOS vem somente do company_system.)\n")

    perguntas = [
        # 1) peca compativel com maquina
        "Jarvis, qual peca podemos fazer na M04 sem substituir o material?",
        # 2) pecas faltantes de um ticket (por data)
        "Jarvis, quais pecas faltam para o ticket do dia 23 de maio?",
        # 3) recomendar peca de um ticket para uma maquina
        "Jarvis, quais pecas faltam no ticket do dia 23 de maio e qual podemos fazer na M04?",
        # 4) status de maquina
        "Jarvis, qual o status da M04?",
        # 5) status de ticket
        "Jarvis, o ticket do dia 23 de maio esta completo?",
        # 6) ticket realmente completo (28/05 -> base de fixacao concluida, mas tampa falta)
        "Jarvis, quais pecas faltam no ticket do dia 28 de maio?",
        # 7) maquina inexistente -> mensagem do sistema
        "Jarvis, qual o status da M99?",
        # 8) sem dados suficientes -> mensagem de seguranca
        "Jarvis, bom dia, tudo bem?",
    ]

    for i, pergunta in enumerate(perguntas, 1):
        resultado = gemini_brain.processar(pergunta)
        ent = resultado["entidades"]
        print(f"[{i}] Operador : {pergunta}")
        print(f"    Intencao : {resultado['intencao']}")
        print(f"    Entidades: maquina={ent['maquina']} data={ent['data']} "
              f"ticket={ent['ticket']} peca={ent['peca']} material={ent['material']}")
        print(f"    JARVIS   : {resultado['resposta']}")
        print()

    print("====================================================\n")


def _tratar_fala(texto: str) -> None:
    """
    Aplica a regra de wake word a uma fala transcrita e, se for para a JARVIS,
    envia o comando ao sistema (gemini_brain.processar -> command_router).
    """
    print(f"\n  Transcrito: {texto!r}")

    if not wake_word.has_wake_word(texto):
        print("  [ignorado] A frase nao chama a JARVIS.")
        return

    comando = wake_word.remove_wake_word(texto)
    print(f"  [ativada]  Comando: {comando!r}")

    resultado = gemini_brain.processar(comando, texto_falado=texto)
    print(f"  JARVIS: {resultado['resposta']}")


def demo_voz_simulada() -> None:
    """
    Demonstra a Fase 8 SEM depender de microfone/internet.
    Usa falas ja transcritas para mostrar:
      - deteccao da palavra "Jarvis";
      - frases ignoradas (sem "Jarvis");
      - envio do comando para o sistema.
    """
    print("\n========== FASE 8 - ENTRADA DE VOZ (SIMULADA) ==========\n")

    ok, motivo = voice_input.microfone_disponivel()
    if ok:
        print("Microfone DISPONIVEL. Para falar de verdade: python main.py --mic")
    else:
        print(f"Microfone indisponivel ({motivo}).")
        print("Rodando com falas simuladas para testar wake word + roteamento.")

    falas_simuladas = [
        "Jarvis, qual peca podemos fazer na M04?",                       # valida
        "Jarvis, quais pecas faltam para o ticket do dia 23 de maio?",   # valida
        "qual peca podemos fazer na M04?",                               # IGNORADA
        "quais pecas faltam no ticket?",                                 # IGNORADA
        "ok jarvis, qual o status da M04?",                              # valida (wake no meio)
    ]

    for fala in falas_simuladas:
        _tratar_fala(fala)

    print("\n========================================================\n")


def demo_logs_seguranca() -> None:
    """Teste da Fase 10: regras de seguranca + registro de interacoes."""
    print("\n========== FASE 10 - LOGS E SEGURANCA ==========\n")

    casos = [
        # (descricao, comando)
        ("Consulta normal (OK)",                "qual o status da M04?"),
        ("Pedido de ALTERACAO (recusado)",      "apague o ticket do dia 23 de maio"),
        ("Outra alteracao (recusado)",          "mude o status da M04 para ocioso"),
        ("Dados insuficientes (esclarecer)",    "bom dia, tudo bem?"),
        ("Maquina inexistente",                 "qual o status da M99?"),
        ("Ticket inexistente",                  "quais pecas faltam no ticket do dia 10 de junho?"),
    ]

    for descricao, comando in casos:
        resultado = gemini_brain.processar(comando)
        print(f">> {descricao}")
        print(f"   Operador : {comando!r}")
        print(f"   Intencao : {resultado['intencao']}")
        flags = []
        if resultado.get("faltaram_dados"):
            flags.append("faltaram_dados")
        if resultado.get("erro"):
            flags.append(f"erro={resultado['erro']}")
        print(f"   Flags    : {flags or 'nenhuma'}")
        print(f"   JARVIS   : {resultado['resposta']}")
        print()

    print(f"Cada interacao acima foi registrada em:\n   {INTERACOES_FILE}")
    print("(1 linha JSON por interacao — abra com Bloco de Notas.)")
    print("\n================================================\n")


def demo_saida_voz() -> None:
    """Teste da Fase 9 (saida de voz): fala a frase de teste e uma resposta real."""
    print("\n========== FASE 9 - SAIDA DE VOZ ==========\n")

    ok, motivo = voice_output.voz_disponivel()
    if ok:
        print("Saida de voz DISPONIVEL. Voce deve ouvir a JARVIS falar agora.")
    else:
        print(f"Saida de voz indisponivel ({motivo}).")
        print("As respostas serao apenas exibidas no terminal.")

    # Frase de teste pedida no enunciado.
    voice_output.speak("Sistema de voz da JARVIS iniciado com sucesso.")

    # Fala uma resposta REAL do sistema (mostra a integracao Fase 7 -> Fase 9).
    resultado = gemini_brain.processar("qual o status da M04?")
    voice_output.speak(resultado["resposta"])

    print("\n===========================================\n")


def demo_voz_microfone() -> None:
    """
    Fase 8 AO VIVO: ouve o microfone em loop. Diga 'Jarvis, ...'.
    Encerra ao dizer 'Jarvis, encerrar' (ou Ctrl+C).
    """
    print("\n========== FASE 8 - ENTRADA DE VOZ (MICROFONE) ==========\n")

    ok, motivo = voice_input.microfone_disponivel()
    if not ok:
        print(f"Nao foi possivel ativar o microfone: {motivo}")
        print("Veja as instrucoes de instalacao no resumo da Fase 8.")
        return

    print("Microfones detectados:")
    for i, nome in enumerate(voice_input.listar_microfones()):
        print(f"   [{i}] {nome}")

    # Biometria de voz (Fase 13): identifica QUEM fala. Desligada por enquanto.
    cadastrados = []
    if BIOMETRIA_ATIVA:
        bio_ok, bio_motivo = biometria.disponivel()
        cadastrados = biometria.pessoas_cadastradas() if bio_ok else []
        if bio_ok and cadastrados:
            print(f"Biometria ATIVA. Reconheco: {', '.join(cadastrados)}")
            print("(roda local, sem gastar tokens)")
        elif bio_ok:
            print("Biometria pronta, mas ninguem cadastrado. Rode: python main.py --cadastrar-voz")
        else:
            print(f"Biometria desligada ({bio_motivo}).")

    print("\nFale quando quiser. Comece com 'Jarvis, ...'.")
    print("Para sair, diga 'Jarvis, encerrar' ou pressione Ctrl+C.\n")

    painel.set_status("aguardando")
    try:
        while True:
            painel.set_status("ouvindo", listening=True)
            texto, audio = voice_input.listen_completo()
            if texto is None:
                painel.set_status("aguardando")
                continue  # silencio / nao entendido -> ouve de novo

            painel.set_status("transcrevendo")
            print(f"\n  Transcrito: {texto!r}")

            if not wake_word.has_wake_word(texto):
                print("  [ignorado] A frase nao chama a JARVIS.")
                painel.set_status("aguardando")
                continue

            comando = wake_word.remove_wake_word(texto)

            if "encerrar" in comando.lower() or "desligar" in comando.lower():
                print("  JARVIS: Encerrando. Ate logo.")
                painel.set_status("aguardando")
                break

            # Quem falou? (biometria local — nao gasta token). Desligada por ora.
            operador, conf = (None, 0.0)
            if BIOMETRIA_ATIVA and cadastrados and audio is not None:
                operador, conf = biometria.identificar(audio)
                if operador:
                    print(f"  [voz] Identificado: {operador} (sim={conf:.2f})")
                else:
                    print(f"  [voz] Operador desconhecido (melhor sim={conf:.2f})")
                painel.set_operador(operador, conf)

            print(f"  [ativada]  Comando: {comando!r}")
            painel.set_status("consultando")
            resultado = gemini_brain.processar(comando, texto_falado=texto,
                                               operador=operador)
            # Atualiza a TV com a resposta e os dados.
            painel.atualizar(comando, resultado, operador=operador, operador_conf=conf)
            # Fala a resposta em voz alta (e tambem mostra no terminal).
            voice_output.speak(resultado["resposta"])
            painel.set_status("aguardando")
    except KeyboardInterrupt:
        print("\n  Encerrado pelo operador (Ctrl+C).")
        painel.set_status("aguardando")

    print("\n=========================================================\n")


def cadastrar_voz() -> None:
    """
    FASE 13 - Cadastro biometrico de voz (roda 100% local, ZERO tokens de API).

    Grava algumas frases de cada pessoa do lab e salva a "assinatura de voz"
    (media dos embeddings) em data/voiceprints.json. Depois disso, no modo
    microfone a JARVIS reconhece QUEM esta falando.
    """
    print("\n========== FASE 13 - CADASTRO DE VOZ (BIOMETRIA) ==========\n")

    ok, motivo = biometria.disponivel()
    if not ok:
        print(f"Biometria indisponivel: {motivo}")
        return

    ok_mic, motivo_mic = voice_input.microfone_disponivel()
    if not ok_mic:
        print(f"Microfone indisponivel: {motivo_mic}")
        return

    ja = biometria.pessoas_cadastradas()
    if ja:
        print("Ja cadastrados:", ", ".join(ja))
    print("Roda LOCAL no PC — nenhum audio sai daqui, nenhum token gasto.\n")

    N_AMOSTRAS = 4
    try:
        while True:
            nome = input("Nome da pessoa (Enter vazio p/ encerrar): ").strip()
            if not nome:
                break

            print(f"\nVou gravar {N_AMOSTRAS} frases de '{nome}'.")
            print("Fale uma frase normal (~3s) a cada vez que aparecer 'FALE AGORA'.")
            print("Dica: frases diferentes ajudam (ex.: nome, o que faz no lab, etc.)\n")

            audios = []
            tentativa = 0
            while len(audios) < N_AMOSTRAS and tentativa < N_AMOSTRAS * 2:
                tentativa += 1
                input(f"  [{len(audios)+1}/{N_AMOSTRAS}] Tecle Enter e depois FALE AGORA...")
                audio = voice_input.capturar_audio(timeout=6.0, phrase_time_limit=5.0)
                if audio is None:
                    print("       (nao captei, vamos de novo)")
                    continue
                audios.append(audio)
                print("       ok, capturado.")

            sucesso, msg = biometria.cadastrar(nome, audios)
            print(f"\n  >> {msg}\n" if sucesso else f"\n  >> FALHOU: {msg}\n")
    except KeyboardInterrupt:
        print("\nCadastro encerrado (Ctrl+C).")

    finais = biometria.pessoas_cadastradas()
    print(f"\nPessoas cadastradas agora: {', '.join(finais) if finais else 'nenhuma'}")
    print("\n===========================================================\n")


def diagnostico_mcp() -> None:
    """
    Conecta ao MCP do sistema e LISTA as ferramentas disponiveis (somente leitura
    marcadas). NAO executa nenhuma acao — so introspecciona o que o sistema oferece.
    Use isto para descobrir o que o sistema e capaz de fazer.
    """
    print("\n========== MCP sistema - FERRAMENTAS DISPONIVEIS ==========\n")

    ok, motivo = mcp_client.disponivel()
    if not ok:
        print(f"Nao da pra conectar ao sistema agora: {motivo}\n")
        print("Configure no .env: MCP_URL e MCP_TOKEN (token com o administrador).")
        print("E instale a lib do MCP:  pip install mcp")
        print("\n=========================================================\n")
        return

    print("Conectando ao sistema...")
    try:
        ferramentas = mcp_client.listar_ferramentas()
    except Exception as erro:  # noqa: BLE001
        print(f"Falha ao conectar/listar: {erro}")
        print("Verifique a URL, o token e se a rede alcanca o servidor.")
        print("\n=========================================================\n")
        return

    leitura = [f for f in ferramentas if f["somente_leitura"]]
    acao = [f for f in ferramentas if not f["somente_leitura"]]

    print(f"\nTotal: {len(ferramentas)} ferramenta(s). "
          f"{len(leitura)} de LEITURA (a JARVIS usa), "
          f"{len(acao)} de ACAO (bloqueadas).\n")

    print("--- LEITURA (a JARVIS pode usar) ---")
    for f in leitura:
        print(f"  [LER] {f['name']}: {f['description'][:90]}")

    print("\n--- ACAO (BLOQUEADAS — a JARVIS nunca chama) ---")
    for f in acao:
        print(f"  [ACAO] {f['name']}: {f['description'][:90]}")

    print("\n=========================================================\n")


def chat_por_texto(falar: bool = False) -> None:
    """
    Conversa com a JARVIS DIGITANDO (sem microfone, sem falar).
    Voce escreve a pergunta e ela responde em texto. Use --chat --voz para
    tambem ouvir a resposta em audio.

    Nao precisa dizer "Jarvis" aqui: tudo que voce digita ja vai direto pra ela.
    Para sair: digite 'sair', 'encerrar' ou pressione Ctrl+C.
    """
    print("\n========== JARVIS - CHAT POR TEXTO ==========\n")
    from core import llm_client
    if llm_client.disponivel():
        print(f"Conectada via {llm_client.provedor_ativo().upper()} (modelo: {llm_client.nome_modelo()}).")
    else:
        print(f"Modo LOCAL ({llm_client.motivo_local()}).")
    print("Digite sua pergunta e tecle Enter. Para sair: 'sair' ou Ctrl+C.\n")

    exemplos = [
        "qual o status da M04?",
        "quais pecas faltam no ticket do dia 23 de maio?",
        "o que da pra fazer na M04 sem trocar material?",
    ]
    print("Exemplos:")
    for e in exemplos:
        print(f"   - {e}")
    print()

    try:
        while True:
            try:
                pergunta = input("Voce> ").strip()
            except EOFError:
                break
            if not pergunta:
                continue
            if pergunta.lower() in ("sair", "encerrar", "desligar", "exit", "quit"):
                print("JARVIS> Encerrando. Ate logo.")
                painel.set_status("aguardando")
                break

            painel.set_status("pensando")
            resultado = gemini_brain.processar(pergunta)
            painel.atualizar(pergunta, resultado)   # atualiza a TV ao vivo
            print(f"JARVIS> {resultado['resposta']}\n")
            if falar:
                voice_output.speak(resultado["resposta"], bloqueante=True)
            painel.set_status("aguardando")
    except KeyboardInterrupt:
        print("\nEncerrado pelo operador (Ctrl+C).")

    print("\n=============================================\n")


if __name__ == "__main__":
    usar_microfone = "--mic" in sys.argv
    usar_chat = "--chat" in sys.argv
    falar = "--voz" in sys.argv
    cadastrar = "--cadastrar-voz" in sys.argv
    diag_mcp = "mcp" in sys.argv or "--mcp" in sys.argv

    boot()

    if diag_mcp:
        # Conecta ao sistema e lista as ferramentas (introspeccao, sem acoes).
        diagnostico_mcp()
    elif cadastrar:
        # Cadastro biometrico de voz das pessoas do lab (Fase 13).
        cadastrar_voz()
    elif usar_chat:
        # Conversa por texto (digitando), sem microfone.
        chat_por_texto(falar=falar)
    elif usar_microfone:
        # Modo ao vivo: pula as demos textuais e vai direto pro microfone.
        demo_voz_microfone()
    else:
        demo_maquinas()
        demo_pecas()
        demo_compatibilidade()
        demo_tickets()
        demo_cruzamento()
        demo_gemini()
        demo_voz_simulada()
        demo_logs_seguranca()
        demo_saida_voz()
