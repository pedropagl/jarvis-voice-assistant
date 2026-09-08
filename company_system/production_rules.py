"""
company_system/production_rules.py
----------------------------------
Regras de producao e cruzamentos (FASES 4 e 6).

FASE 4 — consultar_pecas_compativeis:
    Dada uma maquina, retorna quais pecas podem ser feitas SEM trocar o material
    carregado nos extrusores.

FASE 6 — recomendar_peca_do_ticket_para_maquina:
    Cruza pecas faltantes de um ticket com o material atual de uma maquina e
    recomenda o que pode ser produzido sem troca.
"""

from company_system import database
from company_system.machines import consultar_maquina, MAQUINA_NAO_ENCONTRADA
from company_system.tickets import (
    consultar_pecas_faltantes_por_data,
    TICKET_NAO_ENCONTRADO,
    TICKET_COMPLETO,
)


def _normalizar(texto: str) -> str:
    return texto.strip().lower()


# ---------------------------------------------------------------------------
# FASE 4
# ---------------------------------------------------------------------------

def consultar_pecas_compativeis(codigo_maquina: str, sem_substituir_material: bool = True) -> list[dict] | str:
    """
    Retorna as pecas que podem ser produzidas na maquina informada sem
    necessidade de trocar o material carregado nos extrusores.

    Parametros:
        codigo_maquina          — ex.: "M04", "m04"
        sem_substituir_material — se True (padrao), filtra so as pecas cujo
                                  material+cor ja estao carregados em algum
                                  extrusor da maquina.

    Retorno:
        Lista de dicts com os campos abaixo, ou uma string de erro.

        {
            "id":        codigo da peca,
            "nome":      nome da peca,
            "material":  material exigido,
            "cor":       cor exigida,
            "extrusor":  numero do extrusor onde o material esta carregado,
            "tempo_min": tempo estimado de impressao,
            "quantidade_disponivel": estoque disponivel,
            "motivo":    frase explicando a recomendacao,
        }

    Mensagens de erro possiveis (strings):
        "Nao encontrei essa maquina no sistema."
        "Nenhuma peca cadastrada e compativel com o material atual da <ID> sem substituicao."
    """
    maquina = consultar_maquina(codigo_maquina)
    if maquina == MAQUINA_NAO_ENCONTRADA:
        return MAQUINA_NAO_ENCONTRADA

    maquina_id = maquina["id"]

    # Monta mapa de (material_norm, cor_norm) -> numero_extrusor
    extrusores_carregados: dict[tuple[str, str], int] = {}
    for ext in maquina.get("extruders", []):
        mat = ext.get("material") or ""
        cor = ext.get("color") or ""
        if mat and cor:
            chave = (_normalizar(mat), _normalizar(cor))
            extrusores_carregados[chave] = ext["id"]

    pecas = database.load_all()["parts"]
    compativeis = []

    for p in pecas:
        mat_peca = _normalizar(p.get("required_material", ""))
        cor_peca = _normalizar(p.get("required_color", ""))

        extrusor_num = extrusores_carregados.get((mat_peca, cor_peca))

        if extrusor_num is not None:
            motivo = (
                f"Podemos fazer {p['name']}, pois a {maquina_id} ja esta com "
                f"{p['required_material']} {p['required_color']} no extrusor {extrusor_num}."
            )
            compativeis.append({
                "id": p["id"],
                "nome": p["name"],
                "material": p["required_material"],
                "cor": p["required_color"],
                "extrusor": extrusor_num,
                "tempo_min": p.get("est_print_time_min"),
                "quantidade_disponivel": p.get("quantity_available"),
                "motivo": motivo,
            })

    if not compativeis:
        return (
            f"Nenhuma peca cadastrada e compativel com o material atual "
            f"da {maquina_id} sem substituicao."
        )

    return compativeis


# ---------------------------------------------------------------------------
# FASE 6
# ---------------------------------------------------------------------------

def _extrusores_da_maquina(maquina: dict) -> dict[tuple[str, str], int]:
    """Devolve {(material_norm, cor_norm): num_extrusor} para uma maquina."""
    resultado = {}
    for ext in maquina.get("extruders", []):
        mat = ext.get("material") or ""
        cor = ext.get("color") or ""
        if mat and cor:
            resultado[(_normalizar(mat), _normalizar(cor))] = ext["id"]
    return resultado


def recomendar_peca_do_ticket_para_maquina(
    data: str,
    codigo_maquina: str,
    sem_substituir_material: bool = True,
) -> list[dict] | str:
    """
    Cruza as pecas faltantes de um ticket com o material carregado numa maquina
    e recomenda o que pode ser produzido sem troca de material.

    Parametros:
        data                    — data do ticket (qualquer formato aceito por tickets.py)
        codigo_maquina          — ex.: "M04", "m04"
        sem_substituir_material — se True (padrao), considera apenas o material ja carregado

    Retorno (em ordem de prioridade):
        str — MAQUINA_NAO_ENCONTRADA
        str — TICKET_NAO_ENCONTRADO
        str — TICKET_COMPLETO
        str — mensagem de ambiguidade de data (mais de um ticket)
        str — "Nenhuma peca pendente desse ticket e compativel com o material
               atual da <ID> sem substituicao."
        list de dicts:
            {
                part_id, nome, material, cor, extrusor,
                quantidade_faltante, tempo_min, recomendacao
            }
    """
    # 1. Valida a maquina primeiro (falha rapida)
    maquina = consultar_maquina(codigo_maquina)
    if maquina == MAQUINA_NAO_ENCONTRADA:
        return MAQUINA_NAO_ENCONTRADA

    maquina_id = maquina["id"]

    # 2. Busca as pecas faltantes do ticket
    faltantes = consultar_pecas_faltantes_por_data(data)

    # Propaga strings de erro do modulo de tickets sem alteracao
    if isinstance(faltantes, str):
        return faltantes

    # 3. Mapa de extrusores da maquina
    extrusores = _extrusores_da_maquina(maquina)

    # 4. Cruzamento: peca faltante cujo material+cor bate com algum extrusor
    recomendacoes = []
    for item in faltantes:
        chave = (_normalizar(item["material"]), _normalizar(item["cor"]))
        extrusor_num = extrusores.get(chave)

        if extrusor_num is not None:
            falta = item["quantidade_faltante"]
            recomendacao = (
                f"Para o ticket do dia {item['data']}, recomendo fazer "
                f"{item['nome']} na {maquina_id}, pois ainda faltam {falta} "
                f"unidade(s) e a maquina ja esta com {item['material']} "
                f"{item['cor']} no extrusor {extrusor_num}."
            )
            recomendacoes.append({
                "part_id":             item["part_id"],
                "nome":                item["nome"],
                "material":            item["material"],
                "cor":                 item["cor"],
                "extrusor":            extrusor_num,
                "quantidade_faltante": item["quantidade_faltante"],
                "quantidade_necessaria": item["quantidade_necessaria"],
                "quantidade_pronta":   item["quantidade_pronta"],
                "tempo_min":           None,   # sera enriquecido abaixo
                "ticket_id":           item["ticket_id"],
                "data":                item["data"],
                "cliente":             item["cliente"],
                "recomendacao":        recomendacao,
            })

    # 5. Enriquece tempo_min via parts.json (evita duplicar dado no ticket)
    mapa_pecas = {p["id"]: p for p in database.load_all()["parts"]}
    for rec in recomendacoes:
        peca = mapa_pecas.get(rec["part_id"], {})
        rec["tempo_min"] = peca.get("est_print_time_min")

    if not recomendacoes:
        return (
            f"Nenhuma peca pendente desse ticket e compativel com o material "
            f"atual da {maquina_id} sem substituicao."
        )

    return recomendacoes
