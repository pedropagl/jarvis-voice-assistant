"""
core/biometria.py
-----------------
Reconhecimento biometrico de voz (FASE 13) — identifica QUEM do lab esta falando.

100% LOCAL: roda no proprio PC com o Resemblyzer (modelo de "impressao digital"
de voz). NAO envia audio para nenhum servidor, NAO consome tokens de API.
O custo de tokens (Kilo) continua sendo so o da resposta do LLM, como antes.

Como funciona:
    - Cadastro (uma vez por pessoa): gravamos algumas frases e tiramos a media
      dos "embeddings" de voz (vetores de 256 numeros). Isso vira a assinatura
      de voz da pessoa, salva em data/voiceprints.json.
    - Identificacao (a cada fala): geramos o embedding da fala e comparamos
      (similaridade do cosseno) com as assinaturas cadastradas. A mais parecida
      acima do limiar vence; abaixo do limiar => "desconhecido".

DEGRADACAO SEGURA:
    Se o Resemblyzer/torch nao estiver instalado, nada quebra: disponivel()
    devolve (False, motivo) e identificar() devolve (None, 0.0). O resto da
    JARVIS continua funcionando normalmente, so sem dizer quem falou.
"""

import json
import os
from pathlib import Path

from core.logger import get_logger

log = get_logger("jarvis.biometria")

VOICEPRINTS_PATH = Path(__file__).resolve().parent.parent / "data" / "voiceprints.json"

# Similaridade minima (cosseno) para aceitar uma identificacao. Vozes da mesma
# pessoa costumam ficar em ~0.75-0.90; pessoas diferentes em ~0.0-0.55.
LIMIAR = float(os.getenv("JARVIS_BIO_LIMIAR", "0.72"))

# Taxa de amostragem que o Resemblyzer espera (nao mexer).
_SR = 16000

# Cache do modelo (carregado uma unica vez — o primeiro uso demora ~2s).
_encoder = None
_indisponivel_motivo = None


# ---------------------------------------------------------------------------
# Disponibilidade
# ---------------------------------------------------------------------------
def disponivel() -> tuple[bool, str]:
    """(True, '') se da pra usar a biometria; senao (False, motivo)."""
    global _indisponivel_motivo
    if _indisponivel_motivo is not None:
        return (_indisponivel_motivo == ""), _indisponivel_motivo
    try:
        import numpy  # noqa: F401
        import resemblyzer  # noqa: F401
    except ImportError as erro:
        _indisponivel_motivo = (
            f"Resemblyzer/torch nao instalado ({erro}). "
            "Instale com: pip install resemblyzer webrtcvad-wheels"
        )
        return False, _indisponivel_motivo
    _indisponivel_motivo = ""
    return True, ""


def _get_encoder():
    """Carrega (uma vez) o VoiceEncoder do Resemblyzer."""
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        log.info("Carregando o modelo de voz (Resemblyzer)... (so na 1a vez)")
        _encoder = VoiceEncoder(verbose=False)
    return _encoder


# ---------------------------------------------------------------------------
# Conversao de audio -> embedding
# ---------------------------------------------------------------------------
def _audio_para_wav(audio):
    """
    Converte um AudioData (do SpeechRecognition) em um wav float32 16kHz mono,
    ja recortado/normalizado pelo Resemblyzer (remove silencios).
    Devolve None se a fala for curta demais para identificar.
    """
    import numpy as np
    from resemblyzer import preprocess_wav

    # PCM 16-bit mono a 16kHz, direto do microfone (sem arquivo, sem internet).
    bruto = audio.get_raw_data(convert_rate=_SR, convert_width=2)
    amostras = np.frombuffer(bruto, dtype=np.int16).astype(np.float32) / 32768.0
    if amostras.size < _SR // 2:  # menos de ~0,5s de audio: nao da
        return None

    # preprocess_wav normaliza o volume e recorta silencios (VAD). As vezes o
    # VAD corta demais (falante baixo); nesse caso caimos para um plano B que
    # so normaliza o volume, sem recorte, para nao perder a amostra.
    try:
        wav = preprocess_wav(amostras, source_sr=_SR)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao preparar o audio (usando audio sem recorte): %s", erro)
        wav = None

    if wav is None or len(wav) < _SR // 2:
        log.info("VAD recortou demais; usando o audio normalizado sem recorte.")
        pico = float(np.max(np.abs(amostras))) or 1.0
        wav = amostras / pico  # normaliza para nao perder a fala

    if wav is None or len(wav) < _SR // 2:
        return None
    return wav


def _embedding(audio):
    """Gera o embedding (vetor de 256 numeros) de uma fala. None se nao der."""
    wav = _audio_para_wav(audio)
    if wav is None:
        return None
    try:
        return _get_encoder().embed_utterance(wav)
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao gerar o embedding de voz: %s", erro)
        return None


# ---------------------------------------------------------------------------
# Persistencia das assinaturas de voz
# ---------------------------------------------------------------------------
def _carregar() -> dict:
    """Le data/voiceprints.json -> {nome: [256 floats]}. {} se nao existir."""
    try:
        with open(VOICEPRINTS_PATH, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao ler voiceprints.json: %s", erro)
        return {}


def _salvar(assinaturas: dict) -> None:
    """Grava as assinaturas de voz de forma atomica (tmp + replace)."""
    import os
    import tempfile

    VOICEPRINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=VOICEPRINTS_PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(assinaturas, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VOICEPRINTS_PATH)


def pessoas_cadastradas() -> list[str]:
    """Nomes ja cadastrados (para diagnostico/menu)."""
    return sorted(_carregar().keys())


def remover(nome: str) -> bool:
    """Remove o cadastro de uma pessoa. True se removeu."""
    assinaturas = _carregar()
    if nome in assinaturas:
        del assinaturas[nome]
        _salvar(assinaturas)
        log.info("Cadastro de voz removido: %s", nome)
        return True
    return False


# ---------------------------------------------------------------------------
# Cadastro e identificacao
# ---------------------------------------------------------------------------
def cadastrar(nome: str, audios: list) -> tuple[bool, str]:
    """
    Cadastra (ou re-cadastra) a voz de uma pessoa a partir de varias falas.

    `audios` = lista de objetos AudioData (capturados pelo voice_input).
    A assinatura final e a MEDIA dos embeddings das falas validas.
    """
    ok, motivo = disponivel()
    if not ok:
        return False, motivo

    import numpy as np

    nome = (nome or "").strip()
    if not nome:
        return False, "nome vazio"

    embeddings = []
    for audio in audios:
        emb = _embedding(audio)
        if emb is not None:
            embeddings.append(emb)

    if len(embeddings) < 2:
        return False, (
            f"capturei so {len(embeddings)} amostra(s) util(eis); "
            "preciso de pelo menos 2 falas claras"
        )

    media = np.mean(np.stack(embeddings), axis=0)
    # normaliza para vetor unitario (deixa a comparacao por cosseno estavel)
    media = media / (np.linalg.norm(media) + 1e-9)

    assinaturas = _carregar()
    assinaturas[nome] = media.astype(float).tolist()
    _salvar(assinaturas)
    log.info("Voz cadastrada: %s (%d amostras)", nome, len(embeddings))
    return True, f"{nome} cadastrado(a) com {len(embeddings)} amostras."


def identificar(audio) -> tuple[str | None, float]:
    """
    Identifica quem falou. Devolve (nome, similaridade).

    - (nome, sim) se a melhor similaridade >= LIMIAR;
    - (None, sim) se ficou abaixo do limiar (desconhecido) — `sim` ajuda no ajuste;
    - (None, 0.0) se a biometria nao esta disponivel ou nao ha cadastros.
    """
    ok, _ = disponivel()
    if not ok:
        return None, 0.0

    assinaturas = _carregar()
    if not assinaturas:
        return None, 0.0

    import numpy as np

    emb = _embedding(audio)
    if emb is None:
        return None, 0.0
    emb = emb / (np.linalg.norm(emb) + 1e-9)

    melhor_nome, melhor_sim = None, -1.0
    for nome, vetor in assinaturas.items():
        ref = np.asarray(vetor, dtype=np.float32)
        sim = float(np.dot(emb, ref))  # ambos unitarios => cosseno
        if sim > melhor_sim:
            melhor_nome, melhor_sim = nome, sim

    if melhor_sim >= LIMIAR:
        log.info("Voz identificada: %s (sim=%.2f)", melhor_nome, melhor_sim)
        return melhor_nome, melhor_sim

    log.info("Voz nao reconhecida (melhor=%s sim=%.2f < %.2f)",
             melhor_nome, melhor_sim, LIMIAR)
    return None, max(melhor_sim, 0.0)
