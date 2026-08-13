"""Contrato de la transcripción de voz a texto.

Mismo planteamiento que con los modelos de lenguaje: el resto del programa
depende de este protocolo y nunca de una implementación concreta. Así el
usuario puede alternar entre un servicio remoto y un modelo local editando una
variable, sin que el bucle de voz se entere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jarvis.voice.audio import AudioClip

__all__ = ["TranscriptionError", "Transcriber", "Transcription"]


class TranscriptionError(RuntimeError):
    """No se pudo transcribir el audio."""


@dataclass(frozen=True, slots=True)
class Transcription:
    """Resultado de transcribir un fragmento de audio."""

    text: str
    language: str = ""
    duration_seconds: float = 0.0

    @property
    def is_empty(self) -> bool:
        """Indica si no se reconoció nada aprovechable.

        Un fragmento de silencio o de ruido produce con frecuencia una cadena
        vacía o un residuo de puntuación. Tratarlo como una orden llevaría al
        asistente a responder a algo que el usuario nunca dijo.
        """
        return not self.text.strip(" .,¿?¡!…-\n\t")


@runtime_checkable
class Transcriber(Protocol):
    """Interfaz que debe cumplir todo motor de transcripción."""

    def transcribe(self, clip: AudioClip) -> Transcription:
        """Convierte un fragmento de audio en texto.

        Args:
            clip: Audio capturado del micrófono.

        Returns:
            La transcripción obtenida.

        Raises:
            TranscriptionError: Si el audio no pudo transcribirse.
        """
        ...
