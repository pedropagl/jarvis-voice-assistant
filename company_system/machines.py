"""
company_system/machines.py
--------------------------
Base de maquinas da producao (FASE 2).

Consulta as impressoras 3D a partir de data/machines.json (somente leitura).
Cada maquina tem: codigo, status, extrusores (com material e cor) e observacoes.

Funcoes principais:
  - listar_maquinas()
  - consultar_maquina(codigo_maquina)
  - consultar_materiais_da_maquina(codigo_maquina)
"""

from company_system import database

# Mensagem padrao quando a maquina nao existe (texto exato exigido na Fase 2).
MAQUINA_NAO_ENCONTRADA = "Nao encontrei essa maquina no sistema."


def _normalizar(codigo) -> str:
    """Padroniza o codigo digitado: tira espacos e deixa em maiusculas.
    Assim 'm04', ' M04 ' e 'M04' apontam para a mesma maquina.
    """
    return str(codigo).strip().upper()


def _extrusor(maquina: dict, numero: int) -> dict:
    """Devolve os dados do extrusor de numero informado (1 ou 2).
    Se a maquina nao tiver esse extrusor, devolve material e cor vazios.
    """
    for ext in maquina.get("extruders", []):
        if ext.get("id") == numero:
            return {
                "extrusor": numero,
                "material": ext.get("material"),
                "cor": ext.get("color"),
            }
    return {"extrusor": numero, "material": None, "cor": None}


def listar_maquinas() -> list:
    """Devolve a lista de todas as maquinas cadastradas (dados completos)."""
    return database.load_machines()


def consultar_maquina(codigo_maquina: str):
    """Busca uma maquina pelo codigo (ex.: 'M04').

    Retorna o dicionario da maquina, ou a mensagem padrao se nao existir.
    """
    codigo = _normalizar(codigo_maquina)
    for maquina in database.load_machines():
        if _normalizar(maquina.get("id", "")) == codigo:
            return maquina
    return MAQUINA_NAO_ENCONTRADA


def consultar_materiais_da_maquina(codigo_maquina: str):
    """Devolve os materiais carregados em cada extrusor da maquina.

    Retorna uma lista como:
      [{'extrusor': 1, 'material': 'APEX', 'cor': 'vermelho'},
       {'extrusor': 2, 'material': 'APEX', 'cor': 'preto'}]
    Ou a mensagem padrao se a maquina nao existir.
    """
    maquina = consultar_maquina(codigo_maquina)
    if maquina == MAQUINA_NAO_ENCONTRADA:
        return MAQUINA_NAO_ENCONTRADA

    materiais = []
    for ext in maquina.get("extruders", []):
        materiais.append(
            {
                "extrusor": ext.get("id"),
                "material": ext.get("material"),
                "cor": ext.get("color"),
            }
        )
    return materiais


def descrever_maquina(codigo_maquina: str) -> str:
    """Monta uma frase legivel sobre a maquina (util para exibir/testar).

    Ex.: "M04 esta ocioso. Extrusor 1: APEX vermelho. Extrusor 2: APEX preto.
          Obs.: ..."
    """
    maquina = consultar_maquina(codigo_maquina)
    if maquina == MAQUINA_NAO_ENCONTRADA:
        return MAQUINA_NAO_ENCONTRADA

    e1 = _extrusor(maquina, 1)
    e2 = _extrusor(maquina, 2)

    partes = [f"{maquina['id']} esta {maquina.get('status', 'sem status')}."]

    if e1["material"]:
        partes.append(f"Extrusor 1: {e1['material']} {e1['cor']}.")
    if e2["material"]:
        partes.append(f"Extrusor 2: {e2['material']} {e2['cor']}.")
    else:
        partes.append("Extrusor 2: nao instalado.")

    obs = maquina.get("notes")
    if obs:
        partes.append(f"Obs.: {obs}")

    return " ".join(partes)
