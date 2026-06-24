"""
core/painel.py
--------------
Ponte entre a JARVIS e o painel da TV/PC (FASE 12 - integracao final).

A JARVIS ESCREVE o estado em data/dashboard_state.json conforme atende o
operador; o painel (dashboard/app.js) LE esse arquivo a cada 1s e se atualiza
sozinho. Assim o ciclo fecha: voz/chat -> sistema -> TV ao vivo.

Funcoes:
    set_status(status, listening, speaking) -> atualiza so o status (transicoes).
    atualizar(pergunta, resultado)          -> grava o estado completo apos a resposta.

Escrita ATOMICA (arquivo temporario + replace) para o painel nunca ler um JSON
pela metade. Nunca levanta excecao (nao pode derrubar o fluxo de voz).
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from company_system import database, machines, tickets
from core import mcp_client
from core.logger import get_logger

log = get_logger("jarvis.painel")

ESTADO_PATH = Path(__file__).resolve().parent.parent / "data" / "dashboard_state.json"
_MAX_HISTORICO = 8


def _ler() -> dict:
    """Le o estado atual; se faltar/quebrar, devolve um estado minimo."""
    try:
        return database.load_dashboard_state()
    except Exception:  # noqa: BLE001
        return {"status": "aguardando", "history": []}


def _escrever(estado: dict) -> None:
    """Grava o estado de forma atomica (tmp + replace)."""
    try:
        estado["last_update"] = datetime.now().isoformat(timespec="seconds")
        dir_ = ESTADO_PATH.parent
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ESTADO_PATH)
    except Exception as erro:  # noqa: BLE001 - painel nunca derruba o sistema
        log.warning("Falha ao escrever o estado do painel: %s", erro)


def set_status(status: str, listening: bool = False, speaking: bool = False) -> None:
    """
    Atualiza apenas o status atual (e os indicadores de audio).
    Use nas transicoes: ouvindo -> transcrevendo -> consultando -> respondendo.
    """
    estado = _ler()
    estado["status"] = status
    estado["listening"] = listening
    estado["speaking"] = speaking
    _escrever(estado)


def set_operador(nome: str | None, confianca: float = 0.0) -> None:
    """
    Registra QUEM esta falando (biometria de voz, Fase 13) para o painel exibir.
    `nome` None => operador desconhecido/nao identificado.
    """
    estado = _ler()
    estado["operator_name"] = nome or "Desconhecido"
    estado["operator_conf"] = round(float(confianca), 2)
    _escrever(estado)


# ---------------------------------------------------------------------------
# Montagem do estado completo a partir do resultado de gemini_brain.processar
# ---------------------------------------------------------------------------

# ===========================================================================
# Helpers: dados REAIS do sistema quando disponível, senão MOCK
# ===========================================================================

def _parse_lista_json(texto: str) -> list[dict]:
    """
    As ferramentas de LISTA do sistema devolvem varios objetos JSON concatenados
    (um por linha/registro), nao um array. Faz o parse robusto de todos eles.
    """
    objetos = []
    if not texto:
        return objetos
    decoder = json.JSONDecoder()
    i, n = 0, len(texto)
    while i < n:
        # pula espacos/quebras entre objetos
        while i < n and texto[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        try:
            obj, fim = decoder.raw_decode(texto, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            objetos.append(obj)
        i = fim
    return objetos


def _maquina_de_dados(dados: dict, codigo_fallback: str = "?") -> dict:
    """Converte um objeto de impressora do sistema no formato do painel."""
    extrusores_raw = []
    mat_ativo = dados.get("material_active", {})
    if isinstance(mat_ativo, dict):
        extrusores_raw = mat_ativo.get("extruders", []) or []
    return {
        "id": dados.get("name") or codigo_fallback,
        "model": dados.get("model", "?"),
        "status": dados.get("status", "?"),
        "extruders": [
            {
                "id": e.get("index", i),
                "material": e.get("label") or e.get("name") or e.get("guid", "?"),
                "color": e.get("color"),
                "nozzle_mm": e.get("nozzle", "?"),
            }
            for i, e in enumerate(extrusores_raw)
        ],
    }


def _overview_maquina() -> dict | None:
    """
    Visao geral ao vivo: quando a pergunta nao cita maquina, mostra uma
    impressora relevante (preferencia: a que esta IMPRIMINDO; senao a 1a online).
    """
    if not mcp_client.disponivel()[0]:
        return None
    try:
        lista = _parse_lista_json(mcp_client.chamar_ferramenta("listar_impressoras", {}))
        if not lista:
            return None
        # 1a escolha: imprimindo; 2a: online; 3a: a primeira que vier.
        imprimindo = [m for m in lista if str(m.get("status", "")).lower() == "printing"]
        online = [m for m in lista if m.get("online")]
        escolha = (imprimindo or online or lista)[0]
        return _maquina_de_dados(escolha)
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha no overview de maquina: %s", erro)
        return None


def _overview_ticket() -> dict | None:
    """
    Visao geral ao vivo: quando a pergunta nao cita ticket, mostra o ticket mais
    urgente (emergencia primeiro; depois o de maior producao pendente).
    """
    if not mcp_client.disponivel()[0]:
        return None
    try:
        lista = _parse_lista_json(mcp_client.chamar_ferramenta("listar_tickets_ativos", {}))
        if not lista:
            return None
        def _chave(t):
            return (1 if t.get("emergencia") else 0, t.get("pending_total") or 0)
        t = sorted(lista, key=_chave, reverse=True)[0]
        data_req = (t.get("data_requisicao") or t.get("created_at") or "")[:10]
        return {
            "id": t.get("ticket_codigo") or "?",
            "date": data_req or "?",
            "client": t.get("requisitante_nome", "?"),
            "priority": "emergência" if t.get("emergencia") else "normal",
            "status": t.get("status", "?"),
        }
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha no overview de ticket: %s", erro)
        return None


def _demanda_pendente_topo() -> dict | None:
    """
    'PECA RECOMENDADA' repurposada: componente com MAIS producao pendente
    (listar_demandas_pendentes do sistema). Mostra o que vale a pena produzir agora.
    """
    if not mcp_client.disponivel()[0]:
        return None
    try:
        lista = _parse_lista_json(mcp_client.chamar_ferramenta("listar_demandas_pendentes", {}))
        if not lista:
            return None
        d = max(lista, key=lambda x: x.get("quantidade_pendente") or 0)
        return {
            "name": d.get("name") or d.get("componente_codigo") or "?",
            "material": None,
            "color": None,
            "extruder": None,
            "qty_pending": d.get("quantidade_pendente"),
        }
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha ao puxar demanda pendente: %s", erro)
        return None


def _faltantes_sistema(ticket_id: str | None) -> list[dict] | None:
    """
    Pecas faltantes (producao pendente) a partir do sistema. Se vier um ticket_id,
    filtra so as demandas daquele ticket; senao, mostra todas as pendentes.
    Devolve None se o sistema nao estiver disponivel.
    """
    if not mcp_client.disponivel()[0]:
        return None
    try:
        dem = _parse_lista_json(
            mcp_client.chamar_ferramenta("listar_demandas_pendentes", {})
        )
        lista = []
        for d in dem:
            tickets_do_comp = d.get("tickets") or []
            qtd = d.get("quantidade_pendente")
            if ticket_id:
                # so as linhas deste ticket (e usa a qtd especifica do ticket)
                achou = next(
                    (t for t in tickets_do_comp if t.get("ticket_codigo") == ticket_id),
                    None,
                )
                if not achou:
                    continue
                qtd = achou.get("quantidade", qtd)
            lista.append({
                "name": d.get("name") or d.get("componente_codigo") or "?",
                "missing": qtd,
                "required": None,
                "done": None,
                "material": None,
                "color": None,
            })
        # do maior pendente pro menor (o que mais pesa primeiro)
        lista.sort(key=lambda x: x.get("missing") or 0, reverse=True)
        return lista
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha ao puxar faltantes do sistema: %s", erro)
        return None


def _alertas_sistema() -> list[str] | None:
    """
    Alertas a partir do estado REAL das impressoras (sistema): offline ou em
    estado problematico (erro/manutencao/pausada/abortada). Devolve None se o
    sistema nao estiver disponivel (ai o chamador usa o fallback mock).
    """
    if not mcp_client.disponivel()[0]:
        return None
    try:
        imp = _parse_lista_json(mcp_client.chamar_ferramenta("listar_impressoras", {}))
        ruins = {"error", "erro", "maintenance", "manutencao", "paused", "pausada",
                 "aborted", "abortada", "stopped", "parada", "offline"}
        alertas = []
        for m in imp:
            nome = m.get("name", "?")
            status = str(m.get("status", "")).lower()
            if m.get("online") is False:
                alertas.append(f"{nome} offline (sem conexão).")
            elif status in ruins:
                alertas.append(f"{nome} em {status} (indisponível).")
        return alertas
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha ao montar alertas do sistema: %s", erro)
        return None


def _info_maquina_sistema(codigo: str) -> dict | None:
    """Tenta obter info de máquina do sistema (dados reais). Se falhar, None."""
    if not codigo or not mcp_client.disponivel()[0]:
        return None
    try:
        import json
        resultado = mcp_client.chamar_ferramenta(
            "obter_status_impressora", {"printer": codigo}
        )
        dados = json.loads(resultado)
        # Estrutura do sistema: material_active.extruders[].{index, nozzle, ...}
        extrusores_raw = []
        mat_ativo = dados.get("material_active", {})
        if isinstance(mat_ativo, dict):
            extrusores_raw = mat_ativo.get("extruders", [])
        return {
            "id": dados.get("name") or codigo,
            "model": dados.get("model", "?"),
            "status": dados.get("status", "?"),
            "extruders": [
                {
                    "id": e.get("index", i),
                    # sistema manda 'label'/'name' amigaveis (ex.: "Tough PLA") e
                    # so o 'guid' como ultimo recurso.
                    "material": e.get("label") or e.get("name") or e.get("guid", "?"),
                    "color": e.get("color"),
                    "nozzle_mm": e.get("nozzle", "?"),
                }
                for i, e in enumerate(extrusores_raw)
            ],
        }
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha ao puxar máquina do sistema: %s", erro)
        return None


def _info_ticket_sistema(ticket_id: str) -> dict | None:
    """Tenta obter info de ticket do sistema. Se falhar, None."""
    if not ticket_id or not mcp_client.disponivel()[0]:
        return None
    try:
        import json
        resultado = mcp_client.chamar_ferramenta(
            "obter_ticket", {"ticket_codigo": ticket_id}
        )
        dados = json.loads(resultado)
        # sistema usa: ticket_codigo, requisitante_nome, data_requisicao, emergencia.
        data_req = (dados.get("data_requisicao") or dados.get("created_at") or "")[:10]
        return {
            "id": dados.get("ticket_codigo") or ticket_id,
            "date": data_req or "?",
            "client": dados.get("requisitante_nome", "?"),
            "priority": "emergência" if dados.get("emergencia") else "normal",
            "status": dados.get("status", "?"),
        }
    except Exception as erro:  # noqa: BLE001
        log.debug("Falha ao puxar ticket do sistema: %s", erro)
        return None


def _alertas_padrao() -> list[str]:
    """Alertas derivados dos dados (ex.: maquinas em manutencao)."""
    alertas = []
    try:
        for m in database.load_machines():
            if "manuten" in str(m.get("status", "")).lower():
                alertas.append(f"{m['id']} em manutenção (indisponível).")
    except Exception:  # noqa: BLE001
        pass
    return alertas


def _info_maquina(codigo) -> dict | None:
    if not codigo:
        return None
    # Tenta dados REAIS (sistema) primeiro
    real = _info_maquina_sistema(codigo)
    if real:
        return real
    # Fallback: mock (JSONs)
    m = machines.consultar_maquina(codigo)
    if isinstance(m, str):  # nao encontrada
        return None
    return {
        "id": m.get("id"),
        "model": m.get("model"),
        "status": m.get("status"),
        "extruders": [
            {"id": e.get("id"), "material": e.get("material"),
             "color": e.get("color"), "nozzle_mm": e.get("nozzle_mm")}
            for e in m.get("extruders", [])
        ],
    }


def _info_ticket_e_faltantes(entidades) -> tuple[dict | None, list]:
    """Devolve (info_basica_do_ticket, lista_de_pecas_faltantes)."""
    ticket_id = entidades.get("ticket")
    data = entidades.get("data")

    # Tenta dados REAIS (sistema) primeiro, se houver ticket_id especifico
    if ticket_id:
        t = _info_ticket_sistema(ticket_id)
        if t is None:
            # Fallback mock
            t_mock = tickets.consultar_ticket(ticket_id)
            t = (
                {
                    "id": t_mock.get("id"),
                    "date": t_mock.get("date"),
                    "client": t_mock.get("client"),
                    "priority": t_mock.get("priority"),
                    "status": t_mock.get("status"),
                }
                if isinstance(t_mock, dict)
                else None
            )
        faltantes = tickets.consultar_pecas_faltantes_por_id(ticket_id)
    elif data:
        faltantes = tickets.consultar_pecas_faltantes_por_data(data)
        t = None
        # pega o ticket bruto pela data (se houver um so) — só pelo mock
        candidatos = [x for x in database.load_tickets() if x.get("date") == data]
        if len(candidatos) == 1:
            t = {
                "id": candidatos[0].get("id"),
                "date": candidatos[0].get("date"),
                "client": candidatos[0].get("client"),
                "priority": candidatos[0].get("priority"),
                "status": candidatos[0].get("status"),
            }
    else:
        return None, []

    info = t  # agora t ja e um dict (ou None)

    lista = []
    if isinstance(faltantes, list):
        if info is None and faltantes:
            info = {"id": faltantes[0].get("ticket_id"), "date": faltantes[0].get("data"),
                    "client": faltantes[0].get("cliente"),
                    "status": faltantes[0].get("status")}
        for it in faltantes:
            lista.append({
                "name": it.get("nome"), "missing": it.get("quantidade_faltante"),
                "required": it.get("quantidade_necessaria"), "done": it.get("quantidade_pronta"),
                "material": it.get("material"), "color": it.get("cor"),
            })
    return info, lista


def _peca_recomendada(resultado) -> dict | None:
    """Extrai a peca recomendada do resultado do roteador, se houver."""
    sistema = resultado.get("sistema") or {}
    dados = sistema.get("dados")
    if not isinstance(dados, list) or not dados:
        return None
    p = dados[0]
    if not isinstance(p, dict) or "nome" not in p:
        return None
    return {
        "name": p.get("nome"),
        "material": p.get("material"),
        "color": p.get("cor"),
        "extruder": p.get("extrusor"),
    }


def refresh_dados() -> None:
    """
    Atualiza SO os paineis de dados ao vivo (maquina, ticket, faltantes, alertas,
    producao pendente) a partir do sistema, PRESERVANDO a conversa: resposta, operador,
    historico e status ficam intactos. Usado pelo refresh automatico em background
    do servidor — assim a TV mostra dados frescos sem apagar a ultima resposta.
    """
    estado = _ler()

    maquina = _overview_maquina()
    if maquina:
        estado["machine"] = maquina
    ticket = _overview_ticket()
    if ticket:
        estado["ticket"] = ticket
    recomendada = _demanda_pendente_topo()
    if recomendada:
        estado["recommended_part"] = recomendada
    faltantes = _faltantes_sistema(None)
    if faltantes is not None:
        estado["missing_parts"] = faltantes
    alertas = _alertas_sistema()
    if alertas is not None:
        estado["alerts"] = alertas

    _escrever(estado)


def atualizar(pergunta: str, resultado: dict,
              operador: str | None = None, operador_conf: float | None = None) -> None:
    """
    Grava o estado COMPLETO do painel a partir de uma interacao ja processada.

    `resultado` e o dict devolvido por gemini_brain.processar (tem entidades,
    intencao, sistema, resposta, erro, faltaram_dados).
    `operador` (opcional) = quem falou (biometria). Se None, mantem o que ja
    estava no painel (ex.: definido antes por set_operador).
    """
    entidades = resultado.get("entidades") or {}

    # status final
    if resultado.get("erro"):
        status = "erro"
    elif resultado.get("faltaram_dados"):
        status = "dados_insuficientes"
    else:
        status = "respondendo"

    estado = _ler()
    historico = estado.get("history") or []

    # quem falou: usa o que veio agora; senao mantem o que ja estava no painel
    if operador is not None:
        nome_operador = operador or "Desconhecido"
        conf_operador = round(float(operador_conf or 0.0), 2)
    else:
        nome_operador = estado.get("operator_name", "—")
        conf_operador = estado.get("operator_conf", 0.0)

    # monta blocos. Se a pergunta cita algo especifico, mostra ESSE contexto;
    # senao, cai pra uma VISAO GERAL ao vivo (sistema) pra TV nunca ficar vazia.
    maquina = _info_maquina(entidades.get("maquina")) or _overview_maquina()
    ticket, faltantes = _info_ticket_e_faltantes(entidades)
    if ticket is None:
        ticket = _overview_ticket()
    recomendada = _peca_recomendada(resultado) or _demanda_pendente_topo()

    # PECAS FALTANTES: filtra por ticket SÓ quando o operador citou um ticket
    # explicitamente. Em modo overview, mostra TODAS as demandas pendentes do
    # sistema — senao a TV fica vazia quando o ticket exibido nao tem demandas.
    ticket_query = entidades.get("ticket")
    faltantes_sistema = _faltantes_sistema(ticket_query or None)
    if faltantes_sistema is not None:
        faltantes = faltantes_sistema

    # ALERTAS: estado real das impressoras (offline/erro/manutencao). Fallback mock.
    alertas = _alertas_sistema()
    if alertas is None:
        alertas = _alertas_padrao()

    # historico (anexa a pergunta atual)
    if pergunta:
        historico.append({"time": datetime.now().strftime("%H:%M"), "text": pergunta})
        historico = historico[-_MAX_HISTORICO:]

    novo = {
        "status": status,
        "listening": False,
        "speaking": status == "respondendo",
        "operator_text": pergunta or "",
        "operator_name": nome_operador,
        "operator_conf": conf_operador,
        "main_response": resultado.get("resposta", ""),
        "confidence": "erro" if resultado.get("erro") else ("baixa" if resultado.get("faltaram_dados") else "ok"),
        "intencao": resultado.get("intencao"),
        "modo": resultado.get("modo"),
        "machine": maquina,
        "recommended_part": recomendada,
        "ticket": ticket,
        "missing_parts": faltantes,
        "alerts": alertas,
        "history": historico,
    }
    _escrever(novo)
