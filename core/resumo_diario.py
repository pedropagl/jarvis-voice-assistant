"""
core/resumo_diario.py
-----------------------
Resumo proativo: uma vez por dia, a JARVIS monta um panorama do lab
(maquinas, alertas, ticket/producao pendente, fatos recentes da memoria) e
PUBLICA sozinha — sem esperar ninguem perguntar. Aparece:
    - no painel (dashboard_state.json -> campo "daily_summary");
    - falado em voz alta no PC do lab, se o motor de TTS estiver disponivel;
    - por WhatsApp, se essa integracao estiver configurada (core/whatsapp.py).

So gera UMA vez por dia (controla sozinho via data/resumo_estado.json).
Nunca levanta excecao: falha aqui nunca derruba o refresh do painel.
"""

import json
from datetime import date, datetime
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.resumo")

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
ESTADO_PATH = RAIZ_PROJETO / "data" / "resumo_estado.json"

# Quantos fatos recentes da memoria entram no resumo (so os mais relevantes,
# senao o resumo fica longo demais pra ler/ouvir).
_MAX_FATOS_NO_RESUMO = 3


def _ultima_data_gerada() -> date | None:
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return date.fromisoformat(dados["data"])
    except Exception:  # noqa: BLE001
        return None


def _marcar_gerado_hoje() -> None:
    try:
        ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ESTADO_PATH, "w", encoding="utf-8") as f:
            json.dump({"data": date.today().isoformat()}, f)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao registrar data do resumo diario: %s", erro)


def _ja_gerou_hoje() -> bool:
    return _ultima_data_gerada() == date.today()


def _montar_resumo() -> tuple[str, dict]:
    """
    Monta o texto do resumo + um dict com os numeros crus (pro painel exibir
    de forma estruturada, alem do texto corrido).
    """
    from core import memoria, painel

    maquinas = painel._overview_maquinas_todas()
    total = len(maquinas)
    imprimindo = sum(
        1 for m in maquinas
        if str(m.get("status", "")).lower() in ("printing", "imprimindo")
    )
    manutencao = sum(
        1 for m in maquinas
        if "manuten" in str(m.get("status", "")).lower()
        or str(m.get("status", "")).lower() in ("error", "erro", "offline")
    )
    ociosas = max(0, total - imprimindo - manutencao)

    alertas = painel._alertas_catos() or []
    recomendada = painel._demanda_pendente_topo()

    partes = [f"Bom dia! Panorama do lab: {imprimindo} de {total} máquinas imprimindo"]
    if ociosas:
        partes.append(f"{ociosas} ociosa{'s' if ociosas != 1 else ''}")
    if manutencao:
        partes.append(f"{manutencao} precisando de atenção")
    texto = ", ".join(partes) + "."

    if alertas:
        texto += f" {len(alertas)} alerta{'s' if len(alertas) != 1 else ''} ativo{'s' if len(alertas) != 1 else ''}: " + "; ".join(alertas[:3]) + "."

    if recomendada:
        nome = recomendada.get("name") or recomendada.get("nome")
        if nome:
            texto += f" Produção pendente prioritária: {nome}."

    fatos = memoria.listar_fatos_recentes(_MAX_FATOS_NO_RESUMO)
    if fatos:
        texto += " Da memória: " + " ".join(fatos) + "."

    dados = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "maquinas_total": total,
        "maquinas_imprimindo": imprimindo,
        "maquinas_ociosas": ociosas,
        "maquinas_manutencao": manutencao,
        "alertas": len(alertas),
    }
    return texto, dados


def gerar_e_publicar(falar: bool = True) -> str | None:
    """
    Ponto de entrada, chamado periodicamente pelo refresh em background do
    servidor (dashboard/serve.py). So gera de fato uma vez por dia.
    Devolve o texto do resumo (se gerou agora) ou None (se ja tinha gerado
    hoje, ou algo falhou).
    """
    if _ja_gerou_hoje():
        return None

    try:
        from core import painel, whatsapp

        texto, dados = _montar_resumo()

        estado = painel._ler()
        estado["daily_summary"] = {"texto": texto, **dados}
        painel._escrever(estado)

        if whatsapp.disponivel():
            whatsapp.enviar_alerta(texto)

        if falar:
            try:
                from core import voice_output
                disponivel, _ = voice_output.voz_disponivel()
                if disponivel:
                    voice_output.speak(texto, bloqueante=False)
            except Exception as erro:  # noqa: BLE001
                log.debug("Nao foi possivel falar o resumo diario: %s", erro)

        _marcar_gerado_hoje()
        log.info("Resumo diario gerado e publicado.")
        return texto
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao gerar resumo diario: %s", erro)
        return None
