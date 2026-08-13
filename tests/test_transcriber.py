"""Pruebas de la transcripción de voz.

El servicio remoto se simula con un transporte de ``httpx`` y el motor local
con un modelo postizo, de modo que se ejercita la traducción completa sin red,
sin micrófono y sin descargar ningún modelo.
"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

from jarvis.config.settings import ConfigurationError, Provider, Settings, SttBackend
from jarvis.voice.audio import SAMPLE_RATE, AudioClip
from jarvis.voice.groq_transcriber import RemoteTranscriber
from jarvis.voice.local_transcriber import LocalTranscriber
from jarvis.voice.transcriber import Transcription, TranscriptionError


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "provider": Provider.OPENAI,
        "base_url": "groq",
        "model": "modelo-llm",
        "api_key": "clave-compartida",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _clip(segundos: float = 1.5) -> AudioClip:
    t = np.arange(int(SAMPLE_RATE * segundos)) / SAMPLE_RATE
    return AudioClip((np.sin(2 * np.pi * 180 * t) * 8000).astype(np.int16))


def _remoto(handler: Any, **overrides: Any) -> RemoteTranscriber:
    ajustes = _settings(**overrides)
    cliente = httpx.Client(
        base_url=ajustes.resolved_stt_base_url(),
        transport=httpx.MockTransport(handler),
    )
    return RemoteTranscriber(ajustes, client=cliente)


def _responde(**payload: Any) -> Any:
    registro: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        registro["url"] = str(request.url)
        registro["headers"] = dict(request.headers)
        registro["content"] = request.content
        return httpx.Response(200, json=payload)

    handler.registro = registro  # type: ignore[attr-defined]
    return handler


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


def test_the_language_model_endpoint_is_reused() -> None:
    """Quien usa Groq para pensar puede usarlo también para oír."""
    assert _settings().resolved_stt_base_url() == "https://api.groq.com/openai/v1"


def test_a_dedicated_endpoint_takes_precedence() -> None:
    ajustes = _settings(stt_base_url="openrouter")
    assert ajustes.resolved_stt_base_url() == "https://openrouter.ai/api/v1"


def test_a_native_language_model_requires_its_own_endpoint() -> None:
    """La API nativa de Anthropic no transcribe: hace falta indicar otra."""
    ajustes = _settings(provider=Provider.ANTHROPIC, base_url="")
    with pytest.raises(ConfigurationError, match="JARVIS_STT_BASE_URL"):
        ajustes.resolved_stt_base_url()


def test_the_language_model_key_is_reused() -> None:
    assert _settings().resolved_stt_api_key() == "clave-compartida"


def test_a_dedicated_key_takes_precedence() -> None:
    ajustes = _settings(stt_api_key="clave-de-voz")
    assert ajustes.resolved_stt_api_key() == "clave-de-voz"


def test_each_backend_has_its_own_default_model() -> None:
    """Los identificadores difieren entre el servicio y la biblioteca local."""
    remoto = _settings(stt_backend=SttBackend.REMOTE).resolved_stt_model()
    local = _settings(stt_backend=SttBackend.LOCAL).resolved_stt_model()
    assert remoto != local
    assert remoto and local


# --------------------------------------------------------------------------- #
# Transcripción remota
# --------------------------------------------------------------------------- #


def test_audio_is_transcribed() -> None:
    handler = _responde(text="  Abre el bloc de notas.  ", language="es")
    resultado = _remoto(handler).transcribe(_clip())

    assert resultado.text == "Abre el bloc de notas."
    assert resultado.language == "es"
    assert not resultado.is_empty


def test_the_audio_travels_as_a_wav_file() -> None:
    handler = _responde(text="hola")
    _remoto(handler).transcribe(_clip())

    assert handler.registro["url"].endswith("/audio/transcriptions")
    assert b"RIFF" in handler.registro["content"]
    assert b"audio.wav" in handler.registro["content"]


def test_the_language_is_pinned() -> None:
    """Deducir el idioma cuesta tiempo y provoca saltos en frases cortas."""
    handler = _responde(text="hola")
    _remoto(handler).transcribe(_clip())
    assert b'name="language"' in handler.registro["content"]
    assert b"es" in handler.registro["content"]


def test_the_vocabulary_is_sent_as_a_reference() -> None:
    """Sin él, Whisper escribe «eyjarvís» en lugar de «JARVIS»."""
    handler = _responde(text="hola")
    _remoto(handler).transcribe(_clip())

    contenido = handler.registro["content"]
    assert b'name="prompt"' in contenido
    assert "JARVIS".encode() in contenido


def test_an_empty_vocabulary_is_omitted() -> None:
    handler = _responde(text="hola")
    _remoto(handler, stt_vocabulary="   ").transcribe(_clip())
    assert b'name="prompt"' not in handler.registro["content"]


def test_the_local_model_receives_the_vocabulary_too() -> None:
    modelo = _ModeloPostizo(["hola"])
    LocalTranscriber(
        _settings(stt_backend=SttBackend.LOCAL), model=modelo
    ).transcribe(_clip())
    _, kwargs = modelo.recibido
    assert "JARVIS" in kwargs["initial_prompt"]


def test_the_key_travels_as_a_bearer_token() -> None:
    handler = _responde(text="hola")
    _remoto(handler).transcribe(_clip())
    assert handler.registro["headers"]["authorization"] == "Bearer clave-compartida"


def test_a_very_short_clip_is_not_sent() -> None:
    """Un roce del micrófono no merece una petición."""
    enviadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        enviadas.append(request)
        return httpx.Response(200, json={"text": "algo"})

    resultado = _remoto(handler).transcribe(_clip(segundos=0.1))

    assert resultado.text == ""
    assert not enviadas


def test_an_empty_clip_is_not_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debería enviarse nada")

    vacio = AudioClip(np.zeros(0, dtype=np.int16))
    assert _remoto(handler).transcribe(vacio).text == ""


# --------------------------------------------------------------------------- #
# Resultados vacíos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("texto", ["", "   ", ".", "¿?", "...", " ¡! "])
def test_meaningless_results_are_recognised_as_empty(texto: str) -> None:
    """El silencio produce puntuación suelta; responder a eso sería absurdo."""
    assert Transcription(text=texto).is_empty


def test_real_text_is_not_empty() -> None:
    assert not Transcription(text="sube el volumen").is_empty


# --------------------------------------------------------------------------- #
# Errores del servicio
# --------------------------------------------------------------------------- #


def _falla(status: int, payload: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return handler


def test_a_rejected_key_is_explained() -> None:
    handler = _falla(401, {"error": {"message": "Invalid API Key"}})
    with pytest.raises(TranscriptionError, match="rechazó la clave"):
        _remoto(handler).transcribe(_clip())


def test_an_unknown_model_is_explained() -> None:
    handler = _falla(404, {"error": {"message": "model not found"}})
    with pytest.raises(TranscriptionError, match="whisper-large-v3-turbo"):
        _remoto(handler).transcribe(_clip())


def test_a_rate_limit_is_explained() -> None:
    handler = _falla(429, {"error": {"message": "slow down"}})
    with pytest.raises(TranscriptionError, match="límite de peticiones"):
        _remoto(handler).transcribe(_clip())


def test_a_connection_failure_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    with pytest.raises(TranscriptionError, match="No se pudo contactar"):
        _remoto(handler).transcribe(_clip())


def test_a_timeout_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    with pytest.raises(TranscriptionError, match="no respondió"):
        _remoto(handler).transcribe(_clip())


# --------------------------------------------------------------------------- #
# Transcripción local
# --------------------------------------------------------------------------- #


class _Segmento:
    def __init__(self, text: str) -> None:
        self.text = text


class _ModeloPostizo:
    """Sustituto de faster-whisper que registra cómo se le llama."""

    def __init__(self, textos: list[str], falla: bool = False) -> None:
        self.textos = textos
        self.falla = falla
        self.recibido: Any = None

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[Any, Any]:
        if self.falla:
            raise RuntimeError("sin memoria de vídeo")
        self.recibido = (samples, kwargs)
        info = type("Info", (), {"language": "es"})()
        return ([_Segmento(t) for t in self.textos], info)


def test_the_local_model_receives_normalised_samples() -> None:
    """faster-whisper espera valores entre -1 y 1, no enteros de 16 bits."""
    modelo = _ModeloPostizo(["Sube ", "el volumen."])
    resultado = LocalTranscriber(
        _settings(stt_backend=SttBackend.LOCAL), model=modelo
    ).transcribe(_clip())

    muestras, kwargs = modelo.recibido
    assert muestras.dtype == np.float32
    assert np.abs(muestras).max() <= 1.0
    assert kwargs["language"] == "es"
    assert resultado.text == "Sube el volumen."


def test_a_local_failure_is_reported_clearly() -> None:
    modelo = _ModeloPostizo([], falla=True)
    with pytest.raises(TranscriptionError, match="sin memoria de vídeo"):
        LocalTranscriber(
            _settings(stt_backend=SttBackend.LOCAL), model=modelo
        ).transcribe(_clip())


def test_a_short_clip_skips_the_local_model_too() -> None:
    modelo = _ModeloPostizo(["algo"])
    resultado = LocalTranscriber(
        _settings(stt_backend=SttBackend.LOCAL), model=modelo
    ).transcribe(_clip(segundos=0.1))

    assert resultado.text == ""
    assert modelo.recibido is None


def test_the_loader_falls_back_to_the_cpu() -> None:
    """Sin CUDA bien instalado, es preferible ir lento a quedarse sin oído."""
    intentos: list[str] = []

    def fabrica(nombre: str, device: str, compute_type: str) -> Any:
        intentos.append(device)
        if device == "cuda":
            raise RuntimeError("no se encontró cudnn64_9.dll")
        return _ModeloPostizo(["ok"])

    transcriptor = LocalTranscriber.__new__(LocalTranscriber)
    transcriptor._language = "es"  # type: ignore[attr-defined]
    transcriptor._model_name = "large-v3-turbo"  # type: ignore[attr-defined]
    modelo, dispositivo = transcriptor._load(fabrica, "auto")

    assert intentos == ["cuda", "cpu"]
    assert dispositivo == "cpu"
    assert modelo is not None


def test_the_loader_reports_when_nothing_works() -> None:
    def fabrica(nombre: str, device: str, compute_type: str) -> Any:
        raise RuntimeError("modelo inexistente")

    transcriptor = LocalTranscriber.__new__(LocalTranscriber)
    transcriptor._language = "es"  # type: ignore[attr-defined]
    transcriptor._model_name = "inventado"  # type: ignore[attr-defined]

    with pytest.raises(TranscriptionError, match="inventado"):
        transcriptor._load(fabrica, "auto")
