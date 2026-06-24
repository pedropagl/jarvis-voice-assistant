"""
company_system/database.py
---------------------------
Camada de acesso a dados da JARVIS.

Nesta versao (Fase 1) os dados vem de arquivos JSON locais na pasta /data.
O acesso e SOMENTE LEITURA. A JARVIS nao pode alterar, apagar ou modificar
dados de producao nesta versao.

Conexao com o sistema real da empresa NAO acontece aqui ainda — sera um passo
futuro, substituindo a origem dos dados sem mudar o resto do projeto.
"""

import json
from pathlib import Path

# Pasta /data fica um nivel acima deste arquivo (company_system/ -> raiz -> data/)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_json(filename: str) -> dict:
    """Le um arquivo JSON da pasta /data e devolve o conteudo como dicionario.

    Levanta FileNotFoundError com mensagem clara caso o arquivo nao exista,
    para facilitar o diagnostico durante os testes.
    """
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo de dados nao encontrado: {path}. "
            f"Verifique se a pasta /data contem '{filename}'."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_machines() -> list:
    """Devolve a lista de maquinas (somente leitura)."""
    return _load_json("machines.json").get("machines", [])


def load_parts() -> list:
    """Devolve a lista de pecas do catalogo (somente leitura)."""
    return _load_json("parts.json").get("parts", [])


def load_tickets() -> list:
    """Devolve a lista de tickets (somente leitura)."""
    return _load_json("tickets.json").get("tickets", [])


def load_dashboard_state() -> dict:
    """Devolve o estado atual do painel da TV (somente leitura nesta fase)."""
    return _load_json("dashboard_state.json")


def load_all() -> dict:
    """Carrega todas as bases de uma vez. Util para o boot e para testes."""
    return {
        "machines": load_machines(),
        "parts": load_parts(),
        "tickets": load_tickets(),
        "dashboard_state": load_dashboard_state(),
    }
