"""
core/relatorio_quinzenal.py
-----------------------------
A cada 14 dias, grava uma linha de resumo numa planilha do Google Sheets
(uso da JARVIS + panorama das maquinas) e manda um e-mail avisando que o
relatorio foi atualizado.

Configuracao no .env:
    GOOGLE_SERVICE_ACCOUNT_JSON -> caminho para o arquivo .json da conta de
                                   servico do Google Cloud (ver passo a passo
                                   abaixo). Ex.: C:\\jarvis\\google_service_account.json
    GOOGLE_SHEET_ID             -> ID da planilha (o trecho da URL entre
                                   /d/ e /edit em https://docs.google.com/spreadsheets/d/ESSE_ID/edit)
    RELATORIO_EMAIL_PARA        -> e-mail(s) que recebem o aviso, separados por virgula
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_SENHA -> conta usada para ENVIAR o e-mail
                                   (ex.: smtp.gmail.com, 587, seu_email@gmail.com,
                                   senha de app do Gmail — nao a senha normal)

Sem essas variaveis, o relatorio fica DESATIVADO e o resto do sistema
continua funcionando normalmente.

COMO CONSEGUIR AS CREDENCIAIS DO GOOGLE (feito uma vez so, manualmente):
    1. Ir em https://console.cloud.google.com/ e criar um projeto (ou usar um existente).
    2. Ativar a "Google Sheets API" em APIs e servicos > Biblioteca.
    3. Criar uma Conta de Servico em APIs e servicos > Credenciais > Criar
       credenciais > Conta de servico.
    4. Na conta de servico criada, gerar uma CHAVE tipo JSON e baixar o arquivo
       — esse e o GOOGLE_SERVICE_ACCOUNT_JSON.
    5. Criar (ou abrir) a planilha no Google Sheets, copiar o ID da URL.
    6. Compartilhar a planilha com o e-mail da conta de servico (algo como
       nome@projeto.iam.gserviceaccount.com, encontrado dentro do JSON) com
       permissao de Editor — senao a JARVIS nao consegue escrever nela.
"""

import json
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.relatorio")

INTERVALO_DIAS = 14
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
ESTADO_PATH = RAIZ_PROJETO / "data" / "relatorio_estado.json"
ABA_PLANILHA = "Relatorio JARVIS"


def _config() -> dict:
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if json_path and not os.path.isabs(json_path):
        # Caminho relativo (ex.: "credenciais/arquivo.json") e resolvido a
        # partir da raiz do projeto, nao do diretorio de onde o processo foi
        # iniciado — assim funciona igual seja chamado via jarvis.bat,
        # jarvis_autostart.bat ou direto pelo servidor.
        json_path = str(RAIZ_PROJETO / json_path)
    return {
        "service_account_json": json_path,
        "sheet_id": os.getenv("GOOGLE_SHEET_ID", "").strip(),
        "email_para": os.getenv("RELATORIO_EMAIL_PARA", "").strip(),
        "smtp_host": os.getenv("SMTP_HOST", "").strip(),
        "smtp_port": os.getenv("SMTP_PORT", "587").strip(),
        "smtp_user": os.getenv("SMTP_USER", "").strip(),
        "smtp_senha": os.getenv("SMTP_SENHA", "").strip(),
    }


def disponivel() -> bool:
    """True se ha configuracao suficiente para gerar E avisar por e-mail."""
    c = _config()
    return bool(
        c["service_account_json"] and c["sheet_id"] and c["email_para"]
        and c["smtp_host"] and c["smtp_user"] and c["smtp_senha"]
    )


def _ultimo_envio() -> datetime | None:
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return datetime.fromisoformat(dados["ultimo_envio"])
    except Exception:  # noqa: BLE001 - nunca teve relatorio ainda, ou arquivo corrompido
        return None


def _marcar_envio() -> None:
    try:
        ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ESTADO_PATH, "w", encoding="utf-8") as f:
            json.dump({"ultimo_envio": datetime.now().isoformat()}, f)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao registrar data do ultimo relatorio: %s", erro)


def _e_hora_de_enviar() -> bool:
    ultimo = _ultimo_envio()
    if ultimo is None:
        return True
    return datetime.now() - ultimo >= timedelta(days=INTERVALO_DIAS)


def _coletar_resumo() -> dict:
    """Junta metricas de uso da JARVIS + panorama atual das maquinas."""
    from core import metricas, painel

    uso = metricas.gerar_relatorio_diretoria()
    maquinas = painel._overview_maquinas_todas()
    total = len(maquinas)
    imprimindo = sum(
        1 for m in maquinas
        if str(m.get("status", "")).lower() in ("printing", "imprimindo")
    )

    return {
        "periodo_fim": datetime.now().strftime("%d/%m/%Y"),
        "comandos_total": uso["total"],
        "taxa_sucesso": uso["taxa_sucesso"],
        "tempo_economizado_min": round(uso["economia_s"] / 60, 1),
        "maquinas_total": total,
        "maquinas_imprimindo_agora": imprimindo,
    }


def _gravar_planilha(resumo: dict) -> bool:
    import gspread
    from google.oauth2.service_account import Credentials

    c = _config()
    escopos = ["https://www.googleapis.com/auth/spreadsheets"]
    credenciais = Credentials.from_service_account_file(c["service_account_json"], scopes=escopos)
    cliente = gspread.authorize(credenciais)
    planilha = cliente.open_by_key(c["sheet_id"])

    try:
        aba = planilha.worksheet(ABA_PLANILHA)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=ABA_PLANILHA, rows=200, cols=10)
        aba.append_row([
            "Data do relatorio", "Comandos JARVIS", "Taxa de sucesso (%)",
            "Tempo economizado (min)", "Maquinas cadastradas", "Imprimindo agora",
        ])

    aba.append_row([
        resumo["periodo_fim"], resumo["comandos_total"], resumo["taxa_sucesso"],
        resumo["tempo_economizado_min"], resumo["maquinas_total"],
        resumo["maquinas_imprimindo_agora"],
    ])
    return True


def _enviar_email_aviso(resumo: dict) -> bool:
    c = _config()
    corpo = (
        f"Relatorio quinzenal da JARVIS atualizado ({resumo['periodo_fim']}).\n\n"
        f"- Comandos processados: {resumo['comandos_total']}\n"
        f"- Taxa de sucesso: {resumo['taxa_sucesso']}%\n"
        f"- Tempo economizado estimado: {resumo['tempo_economizado_min']} minutos\n"
        f"- Maquinas cadastradas: {resumo['maquinas_total']}\n"
        f"- Imprimindo agora: {resumo['maquinas_imprimindo_agora']}\n\n"
        f"Planilha completa: https://docs.google.com/spreadsheets/d/{c['sheet_id']}/edit\n"
    )
    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = f"[JARVIS] Relatorio quinzenal - {resumo['periodo_fim']}"
    msg["From"] = c["smtp_user"]
    msg["To"] = c["email_para"]

    with smtplib.SMTP(c["smtp_host"], int(c["smtp_port"]), timeout=15) as servidor:
        servidor.starttls()
        servidor.login(c["smtp_user"], c["smtp_senha"])
        servidor.sendmail(c["smtp_user"], [e.strip() for e in c["email_para"].split(",")], msg.as_string())
    return True


def verificar_e_enviar() -> None:
    """
    Ponto de entrada chamado periodicamente (ex.: uma vez por dia, pelo
    refresh em background do servidor). So faz alguma coisa quando: (a) o
    relatorio esta configurado no .env, e (b) ja se passaram 14 dias desde
    o ultimo envio. Nunca levanta excecao.
    """
    if not disponivel():
        return
    if not _e_hora_de_enviar():
        return

    try:
        resumo = _coletar_resumo()
        _gravar_planilha(resumo)
        _enviar_email_aviso(resumo)
        _marcar_envio()
        log.info("Relatorio quinzenal enviado com sucesso (%s).", resumo["periodo_fim"])
    except Exception as erro:  # noqa: BLE001 - relatorio nunca derruba o sistema
        log.warning("Falha ao gerar/enviar o relatorio quinzenal: %s", erro)
