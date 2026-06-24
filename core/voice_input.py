"""
core/voice_input.py
-------------------
Entrada de voz / Speech-to-Text (FASE 8).

Capta o audio do microfone e transcreve a fala do operador em portugues do
Brasil, para depois ser interpretada pela JARVIS (Fase 7).

Biblioteca usada: SpeechRecognition + PyAudio (microfone).
Engine de transcricao: Google Web Speech (online, gratuito, sem chave) por
padrao. Idioma vindo de JARVIS_LANG (default pt-BR).

DEGRADACAO SEGURA:
    Se a lib de voz ou o microfone nao estiverem disponiveis, listen() nao
    quebra: registra o motivo e devolve None. Assim o resto do sistema (wake
    word, roteamento) continua testavel em modo simulado (ver main.py).
"""

import os

from core.logger import get_logger

log = get_logger("jarvis.voz")

IDIOMA = os.getenv("JARVIS_LANG", "pt-BR")

# Cache do motivo de indisponibilidade (evita repetir checagens caras).
_indisponivel_motivo = None


def microfone_disponivel() -> tuple[bool, str]:
    """
    Verifica se da pra usar o microfone.

    Retorna (True, "") se ok, ou (False, motivo) explicando o que falta.
    """
    global _indisponivel_motivo

    try:
        import speech_recognition as sr  # noqa: F401
    except ImportError:
        _indisponivel_motivo = (
            "biblioteca SpeechRecognition nao instalada "
            "(pip install SpeechRecognition)"
        )
        return False, _indisponivel_motivo

    try:
        import pyaudio  # noqa: F401
    except ImportError:
        _indisponivel_motivo = (
            "PyAudio nao instalado (necessario para o microfone). "
            "No Windows (Python 3.13): pip install PyAudio  "
            "(NAO use pipwin no Python 3.13 — esta quebrado pelo js2py)"
        )
        return False, _indisponivel_motivo

    try:
        import speech_recognition as sr
        nomes = sr.Microphone.list_microphone_names()
        if not nomes:
            _indisponivel_motivo = "nenhum microfone encontrado no sistema"
            return False, _indisponivel_motivo
    except Exception as erro:  # noqa: BLE001
        _indisponivel_motivo = f"falha ao listar microfones: {erro}"
        return False, _indisponivel_motivo

    return True, ""


def listar_microfones() -> list[str]:
    """Lista os microfones detectados (util para diagnostico no Windows)."""
    try:
        import speech_recognition as sr
        return list(sr.Microphone.list_microphone_names())
    except Exception as erro:  # noqa: BLE001
        log.warning("Nao foi possivel listar microfones: %s", erro)
        return []


def capturar_audio(timeout: float = 6.0, phrase_time_limit: float = 12.0,
                   device_index: int | None = None,
                   calibrar: bool = True):
    """
    Capta UMA fala do microfone e devolve o AUDIO BRUTO (objeto AudioData do
    SpeechRecognition), sem transcrever. Util para a biometria de voz (Fase 13)
    e para o cadastro de assinaturas de voz.

    Retorna None se nao captou nada ou se o microfone/lib faltou.
    """
    ok, motivo = microfone_disponivel()
    if not ok:
        log.warning("Microfone indisponivel: %s", motivo)
        return None

    import speech_recognition as sr

    reconhecedor = sr.Recognizer()
    try:
        with sr.Microphone(device_index=device_index) as fonte:
            if calibrar:
                reconhecedor.adjust_for_ambient_noise(fonte, duration=0.5)
            log.info("Ouvindo... (fale agora)")
            return reconhecedor.listen(
                fonte, timeout=timeout, phrase_time_limit=phrase_time_limit
            )
    except sr.WaitTimeoutError:
        log.info("Tempo esgotado: nenhuma fala detectada.")
        return None
    except OSError as erro:
        log.warning("Erro ao acessar o microfone: %s", erro)
        return None


def transcrever(audio) -> str | None:
    """Transcreve um AudioData ja capturado (Google Web Speech, pt-BR)."""
    if audio is None:
        return None
    import speech_recognition as sr
    reconhecedor = sr.Recognizer()
    try:
        texto = reconhecedor.recognize_google(audio, language=IDIOMA).strip()
        log.info("Transcrito: %r", texto)
        return texto or None
    except sr.UnknownValueError:
        log.info("Nao entendi o audio (fala nao reconhecida).")
        return None
    except sr.RequestError as erro:
        log.warning("Falha no servico de transcricao (sem internet?): %s", erro)
        return None


def listen_completo(timeout: float = 6.0, phrase_time_limit: float = 12.0,
                    device_index: int | None = None):
    """
    Ouve UMA fala e devolve a tupla (texto_transcrito, audio_bruto).

    O `audio_bruto` (AudioData) permite identificar QUEM falou pela biometria
    sem precisar captar de novo. Qualquer um dos dois pode vir None.
    """
    audio = capturar_audio(timeout, phrase_time_limit, device_index)
    if audio is None:
        return None, None
    return transcrever(audio), audio


def listen(timeout: float = 6.0, phrase_time_limit: float = 12.0,
           device_index: int | None = None) -> str | None:
    """
    Ouve UMA fala do microfone e devolve o texto transcrito (pt-BR).

    Parametros:
        timeout           — segundos esperando a fala COMECAR antes de desistir.
        phrase_time_limit — duracao maxima da fala capturada.
        device_index      — indice do microfone (ver listar_microfones()).
                            None = microfone padrao do sistema.

    Retorno:
        - str com o texto transcrito, ou
        - None se nao captou nada, nao entendeu, ou o microfone/lib faltou.
    """
    texto, _ = listen_completo(timeout, phrase_time_limit, device_index)
    return texto
