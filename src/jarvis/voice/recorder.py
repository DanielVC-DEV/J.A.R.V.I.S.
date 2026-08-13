"""Captura de audio del micrófono.

Capa delgada sobre ``sounddevice``. Toda la inteligencia —cuándo empieza y
termina una intervención— vive en ``jarvis.voice.vad``, que no depende del
sistema de audio y puede comprobarse por completo. Aquí solo queda mover
bloques de muestras.

La biblioteca se importa bajo demanda para que el módulo siga siendo
importable en equipos sin dispositivo de audio, como los de integración
continua.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np

from jarvis.voice.audio import SAMPLE_RATE, AudioClip
from jarvis.voice.vad import UtteranceDetector, UtteranceState

__all__ = ["Microphone", "MicrophoneError", "list_input_devices"]

_logger = logging.getLogger(__name__)

#: Duración de la muestra de ruido con la que se calibra el detector.
CALIBRATION_SECONDS = 0.6


class MicrophoneError(RuntimeError):
    """No se pudo acceder al micrófono."""


def _sounddevice() -> Any:
    """Importa ``sounddevice`` con un mensaje útil si falta."""
    try:
        import sounddevice
    except ImportError as exc:
        raise MicrophoneError(
            "Falta el paquete «sounddevice». Instala el proyecto con "
            '«pip install -e ".[dev]"».'
        ) from exc
    except OSError as exc:
        raise MicrophoneError(
            "No se encontró la biblioteca de audio del sistema (PortAudio)."
        ) from exc
    return sounddevice


def list_input_devices() -> list[tuple[int, str, int]]:
    """Enumera los dispositivos de entrada disponibles.

    Returns:
        Tríos ``(índice, nombre, canales de entrada)``. Útil para el
        diagnóstico cuando el micrófono correcto no es el predeterminado.
    """
    sd = _sounddevice()
    dispositivos = []
    for indice, info in enumerate(sd.query_devices()):
        canales = int(info.get("max_input_channels") or 0)
        if canales > 0:
            dispositivos.append((indice, str(info.get("name") or ""), canales))
    return dispositivos


@dataclass(slots=True)
class Microphone:
    """Fuente de audio en bloques de duración constante."""

    sample_rate: int = SAMPLE_RATE
    frame_ms: int = 30
    device: int | None = None
    """Índice del dispositivo. ``None`` usa el predeterminado del sistema."""

    @property
    def frame_size(self) -> int:
        """Número de muestras de cada bloque."""
        return int(self.sample_rate * self.frame_ms / 1000)

    @contextmanager
    def open_stream(self) -> Iterator[Any]:
        """Abre el flujo de entrada y garantiza su cierre."""
        sd = _sounddevice()
        try:
            flujo = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.frame_size,
                device=self.device,
            )
        except Exception as exc:  # noqa: BLE001 - la causa varía según el sistema
            raise MicrophoneError(
                f"No se pudo abrir el micrófono: {exc}. Comprueba que haya uno "
                "conectado y que Windows permita el acceso a las aplicaciones."
            ) from exc

        with flujo:
            yield flujo

    def read(self, stream: Any) -> np.ndarray:
        """Lee un bloque del flujo y lo devuelve como señal monofónica."""
        datos, desbordado = stream.read(self.frame_size)
        if desbordado:
            # Ocurre cuando el proceso no consume los bloques a tiempo. No
            # invalida la grabación, pero conviene dejar constancia.
            _logger.debug("Se perdieron muestras de audio por desbordamiento.")
        return np.asarray(datos, dtype=np.int16).reshape(-1)

    # -- Modos de captura --------------------------------------------------- #

    def calibrate(self, detector: UtteranceDetector) -> float:
        """Mide el ruido de fondo y ajusta el umbral del detector.

        Debe ejecutarse con el usuario en silencio. Basta hacerlo una vez al
        arrancar: el ruido de una habitación no cambia entre una frase y la
        siguiente.

        Args:
            detector: Detector a calibrar.

        Returns:
            El umbral resultante.
        """
        bloques: list[np.ndarray] = []
        necesarios = int(CALIBRATION_SECONDS * 1000 / self.frame_ms)

        with self.open_stream() as flujo:
            for _ in range(necesarios):
                bloques.append(self.read(flujo))

        return detector.calibrate(AudioClip.from_frames(bloques, self.sample_rate))

    def record_while(self, should_continue: Callable[[], bool]) -> AudioClip:
        """Graba mientras una condición se mantenga cierta.

        Es el modo de pulsar para hablar: la condición consulta si la tecla
        sigue pulsada. No interviene la detección de voz, porque el usuario
        indica de forma explícita cuándo empieza y termina.

        Args:
            should_continue: Función consultada antes de cada bloque.

        Returns:
            El audio capturado.
        """
        bloques: list[np.ndarray] = []
        maximo = int(60_000 / self.frame_ms)  # tope de un minuto

        with self.open_stream() as flujo:
            while should_continue() and len(bloques) < maximo:
                bloques.append(self.read(flujo))

        return AudioClip.from_frames(bloques, self.sample_rate)

    def record_utterance(self, detector: UtteranceDetector) -> AudioClip:
        """Graba hasta que el usuario termine de hablar.

        Es el modo de palabra de activación: el asistente ya está escuchando y
        debe deducir por sí mismo cuándo concluye la orden.

        Args:
            detector: Detector calibrado que delimita la intervención.

        Returns:
            El audio de la intervención, vacío si nadie llegó a hablar.
        """
        detector.reset()

        with self.open_stream() as flujo:
            while True:
                estado = detector.push(self.read(flujo))
                if estado is UtteranceState.FINISHED:
                    return detector.clip
                if estado is UtteranceState.TIMED_OUT:
                    return AudioClip(np.zeros(0, dtype=np.int16), self.sample_rate)
