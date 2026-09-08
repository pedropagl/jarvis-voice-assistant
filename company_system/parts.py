"""
company_system/parts.py
-----------------------
Consultas ao catalogo de pecas cadastradas.
Os dados vem de database.load_parts() — somente leitura.

FASE 3 — Base de pecas.
"""

from company_system import database


def _normalizar(texto: str) -> str:
    """Minusculas e sem espacos extras para comparacoes flexiveis."""
    return texto.strip().lower()


def _todas() -> list[dict]:
    return database.load_all()["parts"]


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def listar_pecas() -> list[dict]:
    """
    Retorna todas as pecas cadastradas com os campos principais.

    Cada item:
        id, name, required_material, required_color, required_extrusor,
        est_print_time_min, quantity_available, notes
    """
    return [
        {
            "id": p["id"],
            "nome": p["name"],
            "material": p["required_material"],
            "cor": p["required_color"],
            "extrusor": p.get("required_extrusor"),
            "tempo_min": p["est_print_time_min"],
            "quantidade_disponivel": p.get("quantity_available"),
            "observacoes": p.get("notes", ""),
        }
        for p in _todas()
    ]


def consultar_peca(nome_ou_codigo: str) -> dict | str:
    """
    Busca uma peca pelo nome (parcial, sem case) ou pelo codigo (ex.: 'P001').

    Retorna o dict da peca ou a mensagem padrao se nao encontrar.
    """
    chave = _normalizar(nome_ou_codigo)

    for p in _todas():
        if _normalizar(p["id"]) == chave or chave in _normalizar(p["name"]):
            return {
                "id": p["id"],
                "nome": p["name"],
                "material": p["required_material"],
                "cor": p["required_color"],
                "extrusor": p.get("required_extrusor"),
                "tempo_min": p["est_print_time_min"],
                "quantidade_disponivel": p.get("quantity_available"),
                "observacoes": p.get("notes", ""),
            }

    return "Nao encontrei essa peca no sistema."


def buscar_pecas_por_material_e_cor(material: str, cor: str) -> list[dict] | str:
    """
    Retorna todas as pecas que exigem exatamente o material e a cor informados.
    Comparacao sem case e sem espacos extras.

    Retorna lista (pode ser vazia) ou mensagem se nenhuma peca for encontrada.
    """
    mat = _normalizar(material)
    cor_ = _normalizar(cor)

    resultado = [
        {
            "id": p["id"],
            "nome": p["name"],
            "material": p["required_material"],
            "cor": p["required_color"],
            "extrusor": p.get("required_extrusor"),
            "tempo_min": p["est_print_time_min"],
            "quantidade_disponivel": p.get("quantity_available"),
            "observacoes": p.get("notes", ""),
        }
        for p in _todas()
        if _normalizar(p["required_material"]) == mat
        and _normalizar(p["required_color"]) == cor_
    ]

    if not resultado:
        return "Nao encontrei pecas com esse material e cor no sistema."

    return resultado
