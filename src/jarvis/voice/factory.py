"""Selección del motor de transcripción según la configuración.

Único lugar donde se decide si la voz se transcribe en un servicio remoto o en
el propio equipo. El bucle de voz depende del protocolo ``Transcriber`` y
nunca de una implementación concreta.
"""

from __future__ import annotations

from jarvis.config.settings import Settings, SttBackend
from jarvis.voice.transcriber import Transcriber

__all__ = ["create_transcriber"]


def create_transcriber(settings: Settings) -> Transcriber:
    """Construye el motor de transcripción indicado en la configuración.

    Args:
        settings: Configuración de la aplicación.

    Returns:
        Un transcriptor listo para usar.

    Raises:
        ConfigurationError: Si falta la dirección del servicio.
        TranscriptionError: Si falta alguna dependencia del motor elegido.
    """
    if settings.stt_backend is SttBackend.LOCAL:
        from jarvis.voice.local_transcriber import LocalTranscriber

        return LocalTranscriber(settings)

    from jarvis.voice.groq_transcriber import RemoteTranscriber

    return RemoteTranscriber(settings)
