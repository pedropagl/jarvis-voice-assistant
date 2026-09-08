"""
core/cadastro_voz.py
--------------------
Cadastro guiado de voz (enrollment) das pessoas do lab — FASE 13 (painel).

O painel (dashboard) dispara o cadastro por um botao; ESTE modulo conduz a
gravacao pelo microfone DA MAQUINA do JARVIS (onde o mic esta), mostrando o
progresso na tela via status(). Cada pessoa repete algumas frases; tiramos a
media dos embeddings de voz (ver core/biometria.py) e salvamos a assinatura.

Por que o backend grava (e nao o browser):
    O microfone vive na maquina do JARVIS. O browser so dispara e acompanha o
    progresso (polling em /api/enroll/status). 100% local, sem enviar audio.

Roda numa THREAD em background para nao travar o servidor do painel: iniciar()
retorna na hora e o progresso aparece em status().
"""

import threading
from datetime import datetime

from core import biometria, voice_input, voice_output
from core.logger import get_logger

log = get_logger("jarvis.cadastro_voz")

# Frases variadas (fonemas diversos + contexto do lab). A pessoa LE da tela.
# Mais frases que o minimo (>=2) para dar margem se alguma sair ruim.
_FRASES = [
    "A impressora M04 está imprimindo a peça agora.",
    "Quais peças estão pendentes no ticket de hoje?",
    "Manda imprimir a placa na máquina cinco, por favor.",
    "Bom dia, aqui é do laboratório de impressão três dê.",
    "Confirma o envio do fatiamento para a impressora.",
]

# Quantas falas validas bastam (biometria exige >=2; pedimos um pouco mais).
_MIN_AMOSTRAS = 3

_estado: dict = {
    "ativo": False,         # ha um cadastro em andamento?
    "nome": None,           # nome sendo cadastrado
    "fase": "idle",         # idle | preparando | ouvindo | processando | concluido | erro
    "frase_idx": 0,         # frase atual (1-based)
    "total": len(_FRASES),  # total de frases
    "frase_atual": "",      # texto da frase a ler agora
    "capturadas": 0,        # quantas falas validas ja entraram
    "mensagem": "",         # mensagem amigavel pro painel
    "ok": None,             # resultado final (True/False) quando concluido
    "erro": None,           # motivo do erro, se houver
    "pessoas": [],          # lista de pessoas ja cadastradas
}
_lock = threading.Lock()


def _set(**kw) -> None:
    with _lock:
        _estado.update(kw)


def status() -> dict:
    """Estado atual do cadastro (para o painel exibir/poll)."""
    with _lock:
        snap = dict(_estado)
    # pessoas cadastradas sempre atualizadas (barato — le um JSON pequeno)
    try:
        snap["pessoas"] = biometria.pessoas_cadastradas()
    except Exception:  # noqa: BLE001
        snap["pessoas"] = []
    return snap


def em_andamento() -> bool:
    with _lock:
        return _estado["ativo"]


def iniciar(nome: str, device_index: int | None = None) -> tuple[bool, str]:
    """
    Dispara o cadastro de voz de `nome` numa thread. Retorna na hora
    (True, msg) se conseguiu iniciar; (False, motivo) se nao.
    """
    nome = (nome or "").strip()
    if not nome:
        return False, "informe o nome da pessoa"

    ok, motivo = biometria.disponivel()
    if not ok:
        return False, f"biometria indisponivel: {motivo}"

    mic_ok, mic_motivo = voice_input.microfone_disponivel()
    if not mic_ok:
        return False, f"microfone indisponivel: {mic_motivo}"

    with _lock:
        if _estado["ativo"]:
            return False, "ja ha um cadastro em andamento"
        _estado.update(
            ativo=True, nome=nome, fase="preparando", frase_idx=0,
            total=len(_FRASES), frase_atual="", capturadas=0,
            mensagem="Preparando o microfone...", ok=None, erro=None,
        )

    t = threading.Thread(target=_rodar, args=(nome, device_index), daemon=True)
    t.start()
    log.info("Cadastro de voz iniciado para %r.", nome)
    return True, "cadastro iniciado"


def cancelar() -> None:
    """Marca o estado como ocioso (a thread em si termina sozinha)."""
    _set(ativo=False, fase="idle", mensagem="Cadastro cancelado.")


def _rodar(nome: str, device_index: int | None) -> None:
    """Loop de gravacao (roda na thread). Captura as frases e salva a assinatura."""
    try:
        voice_output.speak(
            f"Vou cadastrar a voz de {nome}. Leia em voz alta cada frase que "
            f"aparecer na tela.", bloqueante=True,
        )

        audios = []
        for i, frase in enumerate(_FRASES, start=1):
            _set(fase="ouvindo", frase_idx=i, frase_atual=frase,
                 mensagem=f"Frase {i} de {len(_FRASES)} — leia a frase na tela.")
            # Cue curto (a JARVIS NAO le a frase toda, pra nao gravar a propria voz).
            voice_output.speak(f"Frase {i}. Pode falar.", bloqueante=True)

            audio = voice_input.capturar_audio(
                timeout=8.0, phrase_time_limit=10.0, device_index=device_index,
            )
            if audio is not None:
                audios.append(audio)
                _set(capturadas=len(audios),
                     mensagem=f"Frase {i} capturada ({len(audios)} ok).")
            else:
                _set(mensagem=f"Não ouvi a frase {i}, seguindo para a próxima.")

        _set(fase="processando", frase_atual="",
             mensagem="Gerando a assinatura de voz...")

        if len(audios) < 2:
            _set(fase="erro", ativo=False, ok=False,
                 erro="poucas amostras",
                 mensagem="Não consegui capturar falas suficientes. Tente de novo num lugar mais silencioso.")
            voice_output.speak("Não consegui capturar o suficiente. Vamos tentar de novo depois.",
                               bloqueante=False)
            return

        sucesso, msg = biometria.cadastrar(nome, audios)
        if sucesso:
            _set(fase="concluido", ativo=False, ok=True, mensagem=msg)
            log.info("Cadastro concluido: %s", msg)
            voice_output.speak(f"Pronto! Voz de {nome} cadastrada.", bloqueante=False)
        else:
            _set(fase="erro", ativo=False, ok=False, erro=msg,
                 mensagem=f"Não consegui cadastrar: {msg}")
            voice_output.speak("Não consegui cadastrar a voz. Tente de novo.",
                               bloqueante=False)

    except Exception as erro:  # noqa: BLE001 - thread nunca derruba o servidor
        log.error("Falha no cadastro de voz de %r: %s", nome, erro)
        _set(fase="erro", ativo=False, ok=False, erro=str(erro),
             mensagem=f"Erro inesperado no cadastro: {erro}")


def remover(nome: str) -> tuple[bool, str]:
    """Remove o cadastro de uma pessoa (para o painel)."""
    if biometria.remover(nome):
        return True, f"{nome} removido(a)."
    return False, f"{nome} não estava cadastrado(a)."
