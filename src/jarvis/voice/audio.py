"""Representación del audio capturado.

Define el formato con el que trabaja todo el subsistema de voz y su conversión
a WAV, necesaria para enviarlo a un servicio de transcripción.

La codificación usa el módulo ``wave`` de la biblioteca estándar en lugar de
una dependencia externa: el formato que necesitamos —PCM de 16 bits, un solo
canal— es justo el que ese módulo cubre sin esfuerzo.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

__all__ = ["SAMPLE_RATE", "AudioClip", "rms"]

#: Frecuencia de muestreo de todo el subsistema. Whisper trabaja internamente
#: a 16 kHz: enviar más resolución no mejora la transcripción y multiplica el
#: tamaño de lo que se transmite.
SAMPLE_RATE = 16_000

#: Valor máximo de una muestra de 16 bits con signo.
_FULL_SCALE = 32_768.0


def rms(samples: np.ndarray) -> float:
    """Calcula el valor eficaz de un bloque de muestras.

    Sirve como medida de la energía sonora, que es lo que distingue el
    silencio del habla.

    Args:
        samples: Muestras en formato entero de 16 bits.

    Returns:
        La energía normalizada entre 0 y 1. Un bloque vacío devuelve 0.
    """
    if samples.size == 0:
        return 0.0
    normalizadas = samples.astype(np.float64) / _FULL_SCALE
    return float(np.sqrt(np.mean(np.square(normalizadas))))


@dataclass(frozen=True, slots=True)
class AudioClip:
    """Un fragmento de audio monofónico."""

    samples: np.ndarray
    """Muestras enteras de 16 bits con signo."""

    sample_rate: int = SAMPLE_RATE

    @property
    def duration_seconds(self) -> float:
        """Duración del fragmento."""
        return self.samples.size / self.sample_rate if self.sample_rate else 0.0

    @property
    def is_empty(self) -> bool:
        return self.samples.size == 0

    def to_wav_bytes(self) -> bytes:
        """Codifica el fragmento como un archivo WAV en memoria.

        Returns:
            El contenido completo de un WAV monofónico de 16 bits, listo para
            enviarse a un servicio de transcripción o escribirse en disco.
        """
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as archivo:
            archivo.setnchannels(1)
            archivo.setsampwidth(2)
            archivo.setframerate(self.sample_rate)
            archivo.writeframes(np.ascontiguousarray(self.samples, dtype="<i2").tobytes())
        return buffer.getvalue()

    @classmethod
    def from_frames(
        cls, frames: list[np.ndarray], sample_rate: int = SAMPLE_RATE
    ) -> AudioClip:
        """Une varios bloques consecutivos en un solo fragmento.

        Args:
            frames: Bloques capturados, en orden.
            sample_rate: Frecuencia de muestreo de los bloques.

        Returns:
            El fragmento resultante, vacío si no se aportó ningún bloque.
        """
        if not frames:
            return cls(np.zeros(0, dtype=np.int16), sample_rate)
        return cls(np.concatenate(frames).astype(np.int16), sample_rate)
