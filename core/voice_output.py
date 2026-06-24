"""
core/voice_output.py
--------------------
Saida de voz / Text-to-Speech (FASE 9, melhorada na integracao).

Dois motores de voz, escolhidos pelo .env (JARVIS_TTS_ENGINE):
    - "edge"    : vozes NEURAIS da Microsoft (edge-tts). Naturais, gratuitas,
                  porem ONLINE (precisam de internet). Voz padrao: pt-BR-AntonioNeural.
    - "pyttsx3" : vozes do Windows (SAPI5). Offline, porem mais roboticas.
    - "auto"    : tenta edge (neural); se falhar/sem internet, cai no pyttsx3;
                  se nem isso, apenas mostra o texto no terminal. (PADRAO)

Funcoes principais:
    speak(texto, bloqueante=True) -> mostra no terminal e fala.
    stop_speaking()               -> interrompe a fala em andamento.
    falando()                     -> True se esta falando agora.
    voz_disponivel()              -> (ok, motivo) para diagnostico.
"""

import os
import sys
import tempfile
import threading

from core.logger import get_logger

# Evita o banner do pygame no terminal.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

log = get_logger("jarvis.fala")

# ---- Configuracao (via .env) ----
ENGINE = os.getenv("JARVIS_TTS_ENGINE", "auto").strip().lower()   # auto | edge | pyttsx3
VOZ_EDGE = os.getenv("JARVIS_TTS_VOICE", "pt-BR-AntonioNeural").strip()
TAXA_FALA = int(os.getenv("JARVIS_TTS_RATE", "180"))   # so para o pyttsx3 (palavras/min)
# Velocidade da voz neural: "+0%" normal, "-10%" mais devagar, "+15%" mais rapido.
EDGE_RATE = os.getenv("JARVIS_TTS_EDGE_RATE", "+0%").strip()

_ARQUIVO_TMP = os.path.join(tempfile.gettempdir(), "jarvis_tts.mp3")

# Estado interno
_pyttsx_engine = None
_pyttsx_motivo = None
_pygame_ok = None
_thread_fala: threading.Thread | None = None


# ===========================================================================
# Motor NEURAL (edge-tts + pygame para tocar)
# ===========================================================================

def _pygame_pronto() -> bool:
    """Inicializa o mixer do pygame uma vez. False se nao houver audio."""
    global _pygame_ok
    if _pygame_ok is not None:
        return _pygame_ok
    try:
        import pygame
        pygame.mixer.init()
        _pygame_ok = True
    except Exception as erro:  # noqa: BLE001
        log.warning("pygame/audio indisponivel: %s", erro)
        _pygame_ok = False
    return _pygame_ok


def _sintetizar_edge(texto: str) -> str | None:
    """Gera um mp3 da fala com edge-tts via subprocess (evita bugs asyncio Win32).
    Devolve o caminho do mp3 ou None se falhar."""
    import subprocess
    try:
        import edge_tts  # noqa: F401 — so verifica se esta instalado
    except ImportError:
        return None
    try:
        # Chama edge-tts como modulo Python separado: sem ProactorEventLoop,
        # sem ConnectionResetError no Windows/Python 3.13.
        resultado = subprocess.run(
            [sys.executable, "-m", "edge_tts",
             "--voice", VOZ_EDGE,
             "--rate", EDGE_RATE,
             "--text", texto,
             "--write-media", _ARQUIVO_TMP],
            capture_output=True,
            timeout=20,
        )
        if resultado.returncode == 0:
            return _ARQUIVO_TMP
        log.warning("edge-tts retornou codigo %d.", resultado.returncode)
        return None
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao sintetizar com edge-tts (%s).", erro)
        return None


def _tocar_mp3(caminho: str) -> bool:
    """Toca um mp3 com o pygame (bloqueante). False se nao conseguiu."""
    if not _pygame_pronto():
        return False
    try:
        import pygame
        pygame.mixer.music.load(caminho)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(80)
        # libera o arquivo (Windows trava o mp3 enquanto carregado)
        pygame.mixer.music.unload()
        return True
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao tocar audio (%s).", erro)
        return False


def _falar_edge(texto: str) -> bool:
    """Sintetiza (neural) e toca. True se deu certo."""
    caminho = _sintetizar_edge(texto)
    if not caminho:
        return False
    return _tocar_mp3(caminho)


# ===========================================================================
# Motor OFFLINE (pyttsx3 / vozes do Windows)
# ===========================================================================

def _carregar_pyttsx3():
    """Inicializa o pyttsx3 uma vez. None se indisponivel."""
    global _pyttsx_engine, _pyttsx_motivo
    if _pyttsx_engine is not None:
        return _pyttsx_engine
    if _pyttsx_motivo is not None:
        return None
    try:
        import pyttsx3
    except ImportError:
        _pyttsx_motivo = "pyttsx3 nao instalado"
        return None
    try:
        eng = pyttsx3.init()
        eng.setProperty("rate", TAXA_FALA)
        _selecionar_voz_ptbr(eng)
        _pyttsx_engine = eng
        return eng
    except Exception as erro:  # noqa: BLE001
        _pyttsx_motivo = f"falha ao iniciar pyttsx3: {erro}"
        return None


def _selecionar_voz_ptbr(engine) -> None:
    try:
        vozes = engine.getProperty("voices")
    except Exception:  # noqa: BLE001
        return
    marcadores = ("brazil", "portugu", "pt-br", "pt_br", "ptb", "maria", "daniel")
    for voz in vozes:
        attrs = " ".join([str(getattr(voz, "id", "")), str(getattr(voz, "name", "")),
                          " ".join(getattr(voz, "languages", []) or [])]).lower()
        if any(m in attrs for m in marcadores):
            engine.setProperty("voice", voz.id)
            return


def _falar_pyttsx3(texto: str) -> bool:
    eng = _carregar_pyttsx3()
    if eng is None:
        return False
    try:
        eng.say(texto)
        eng.runAndWait()
        return True
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao falar com pyttsx3 (%s).", erro)
        return False


# ===========================================================================
# API publica
# ===========================================================================

def voz_disponivel() -> tuple[bool, str]:
    """Diz se ha algum motor de voz utilizavel."""
    if ENGINE in ("auto", "edge"):
        try:
            import edge_tts  # noqa: F401
            if _pygame_pronto():
                return True, ""
        except ImportError:
            pass
    if _carregar_pyttsx3() is not None:
        return True, ""
    return False, _pyttsx_motivo or "nenhum motor de voz disponivel"


def falando() -> bool:
    return _thread_fala is not None and _thread_fala.is_alive()


def speak(texto: str, bloqueante: bool = True) -> None:
    """
    Mostra a resposta no terminal e fala em voz alta.

    Cadeia de motores conforme JARVIS_TTS_ENGINE:
        auto    -> edge (neural) e, se falhar, pyttsx3.
        edge    -> so neural.
        pyttsx3 -> so offline.
    Se nada funcionar, o texto fica visivel no terminal (nao quebra).
    """
    texto = (texto or "").strip()
    if not texto:
        return

    print(f"JARVIS (voz): {texto}")

    if falando():
        stop_speaking()

    def _rotina():
        if ENGINE == "pyttsx3":
            _falar_pyttsx3(texto)
            return
        if ENGINE == "edge":
            if not _falar_edge(texto):
                log.info("edge-tts falhou; texto ja exibido no terminal.")
            return
        # auto: tenta neural, depois offline
        if not _falar_edge(texto):
            log.info("Voz neural indisponivel; tentando voz offline (pyttsx3).")
            _falar_pyttsx3(texto)

    if bloqueante:
        _rotina()
    else:
        global _thread_fala
        _thread_fala = threading.Thread(target=_rotina, daemon=True)
        _thread_fala.start()


def stop_speaking() -> None:
    """Interrompe a fala em andamento (neural e/ou offline)."""
    # para o audio neural
    if _pygame_ok:
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:  # noqa: BLE001
            pass
    # para o pyttsx3
    if _pyttsx_engine is not None:
        try:
            _pyttsx_engine.stop()
        except Exception:  # noqa: BLE001
            pass
    log.info("Fala interrompida.")
