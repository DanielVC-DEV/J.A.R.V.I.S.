"""Transcripción mediante un servicio remoto compatible con OpenAI.

Groq sirve Whisper por la misma API que el modelo de lenguaje, de modo que
funciona con la clave que el usuario ya tiene y sin instalar nada. Evita el
punto de fricción más habitual del proyecto: hacer que las bibliotecas de CUDA
y cuDNN convivan correctamente en Windows.

A cambio exige conexión y añade la latencia de subir el audio. Para
intervenciones de unos segundos ronda las décimas, aceptable frente al
presupuesto total del asistente.
"""

from __future__ import annotations

import json

import httpx

from jarvis.config.settings import Settings
from jarvis.voice.audio import AudioClip
from jarvis.voice.transcriber import Transcription, TranscriptionError

__all__ = ["RemoteTranscriber"]

#: Espera máxima. Transcribir unos segundos de audio no debería acercarse a
#: este valor; si lo hace, algo va mal y conviene avisar antes que colgarse.
REQUEST_TIMEOUT_SECONDS = 45.0

#: Duración mínima que merece enviarse. Por debajo casi siempre es un roce del
#: micrófono, y la petición costaría más que el silencio que ahorra.
MIN_DURATION_SECONDS = 0.25


class RemoteTranscriber:
    """Cliente de un servicio de transcripción compatible con OpenAI."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        """Prepara el transcriptor.

        Args:
            settings: Configuración de la aplicación.
            client: Cliente HTTP alternativo, para poder sustituirlo en las
                pruebas por uno que no salga a la red.
        """
        self._base_url = settings.resolved_stt_base_url()
        self._model = settings.resolved_stt_model()
        self._language = settings.stt_language
        self._vocabulary = settings.stt_vocabulary

        self._headers: dict[str, str] = {}
        clave = settings.resolved_stt_api_key()
        if clave:
            self._headers["Authorization"] = f"Bearer {clave}"

        self._client = client or httpx.Client(
            base_url=self._base_url, timeout=REQUEST_TIMEOUT_SECONDS
        )

    def transcribe(self, clip: AudioClip) -> Transcription:
        """Convierte un fragmento de audio en texto.

        Args:
            clip: Audio capturado del micrófono.

        Returns:
            La transcripción obtenida. Un fragmento demasiado corto devuelve
            una transcripción vacía sin llegar a enviarse.

        Raises:
            TranscriptionError: Si el servicio falla o responde algo ilegible.
        """
        if clip.is_empty or clip.duration_seconds < MIN_DURATION_SECONDS:
            return Transcription(text="", duration_seconds=clip.duration_seconds)

        formulario = {
            "model": self._model,
            "response_format": "json",
            # Fijar el idioma evita que el modelo lo deduzca, lo que ahorra
            # tiempo y elimina los saltos a otra lengua en frases cortas.
            "language": self._language,
        }

        # Whisper transcribe a oído los nombres que no conoce: sin este
        # vocabulario de referencia, «JARVIS» se convierte en «eyjarvís».
        if self._vocabulary.strip():
            formulario["prompt"] = self._vocabulary
        archivos = {"file": ("audio.wav", clip.to_wav_bytes(), "audio/wav")}

        try:
            respuesta = self._client.post(
                "/audio/transcriptions",
                headers=self._headers,
                data=formulario,
                files=archivos,
            )
        except httpx.TimeoutException as exc:
            raise TranscriptionError(
                f"El servicio de transcripción no respondió en "
                f"{REQUEST_TIMEOUT_SECONDS:.0f} segundos."
            ) from exc
        except httpx.RequestError as exc:
            raise TranscriptionError(
                f"No se pudo contactar con el servicio de transcripción en "
                f"{self._base_url}."
            ) from exc

        if respuesta.status_code != httpx.codes.OK:
            raise TranscriptionError(self._describe_failure(respuesta))

        try:
            cuerpo = respuesta.json()
        except ValueError as exc:
            raise TranscriptionError(
                "La respuesta del servicio de transcripción no es legible."
            ) from exc

        return Transcription(
            text=str(cuerpo.get("text") or "").strip(),
            language=str(cuerpo.get("language") or self._language),
            duration_seconds=clip.duration_seconds,
        )

    def _describe_failure(self, response: httpx.Response) -> str:
        """Redacta un mensaje comprensible a partir de una respuesta de error."""
        try:
            cuerpo = response.json()
        except ValueError:
            detalle = response.text[:300]
        else:
            error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
            if isinstance(error, dict):
                detalle = str(error.get("message") or error)
            else:
                detalle = json.dumps(cuerpo, ensure_ascii=False)[:300]

        if response.status_code == httpx.codes.UNAUTHORIZED:
            return f"El servicio de transcripción rechazó la clave. {detalle}"
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            return f"Se alcanzó el límite de peticiones de transcripción. {detalle}"
        if response.status_code == httpx.codes.NOT_FOUND:
            return (
                f"El servicio no reconoce el modelo de transcripción "
                f"«{self._model}». {detalle}"
            )
        return (
            f"El servicio de transcripción respondió con un error "
            f"{response.status_code}: {detalle}"
        )
