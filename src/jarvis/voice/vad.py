"""Detección del principio y el final de una intervención hablada.

Cuando el asistente escucha por palabra de activación necesita saber cuándo el
usuario ha terminado de hablar. Transcribir sin esta segmentación implicaría
enviar silencio al servicio, pagar por él y esperar de más.

El criterio es la energía sonora: el habla tiene un valor eficaz claramente
superior al del ruido ambiente. El umbral no es fijo sino que se **calibra**
al arrancar, porque el ruido de fondo de una habitación silenciosa y el de una
con un ventilador encendido difieren en un orden de magnitud, y un umbral
constante fallaría en una de las dos.

Es un detector deliberadamente sencillo. Modelos como Silero VAD distinguen
mejor la voz de un portazo, pero exigen otra dependencia pesada. Como toda la
lógica vive detrás de esta clase, sustituirlo más adelante no afectará al
resto del programa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto

import numpy as np

from jarvis.voice.audio import SAMPLE_RATE, AudioClip, rms

__all__ = ["UtteranceDetector", "UtteranceState"]


class UtteranceState(StrEnum):
    """Situación del detector tras procesar un bloque."""

    WAITING = auto()
    """Aún no se ha detectado habla."""

    SPEAKING = auto()
    """El usuario está hablando."""

    FINISHED = auto()
    """La intervención terminó y puede transcribirse."""

    TIMED_OUT = auto()
    """Se agotó la espera sin que llegara a hablarse."""


@dataclass(slots=True)
class UtteranceDetector:
    """Máquina de estados que delimita una intervención hablada.

    Se le entregan bloques de audio consecutivos mediante ``push`` y responde
    en qué punto se encuentra. Al terminar, ``clip`` contiene la intervención
    recortada, con un pequeño margen antes y después para no cercenar el
    primer ni el último fonema.
    """

    sample_rate: int = SAMPLE_RATE

    frame_ms: int = 30
    """Duración de cada bloque. Treinta milisegundos es el valor habitual en
    telefonía: suficiente para medir energía con estabilidad y bastante corto
    para reaccionar sin retraso perceptible."""

    silence_ms_to_finish: int = 700
    """Silencio necesario para dar por terminada la intervención. Por debajo de
    medio segundo, una pausa natural entre frases cortaría al usuario."""

    min_speech_ms: int = 250
    """Duración mínima para considerar que hubo habla. Descarta toses, chasquidos
    del teclado y golpes en la mesa."""

    max_utterance_ms: int = 30_000
    """Tope absoluto. Evita que un micrófono en un ambiente ruidoso grabe
    indefinidamente."""

    start_timeout_ms: int = 6_000
    """Espera máxima a que el usuario empiece a hablar tras la activación."""

    energy_margin: float = 3.5
    """Cuántas veces debe superar la energía al ruido de fondo para contar como
    habla. Un valor bajo dispara con el ventilador; uno alto obliga a gritar."""

    floor_threshold: float = 0.004
    """Umbral mínimo absoluto. Protege el caso de un micrófono silenciado, en el
    que el ruido calibrado sería casi cero y cualquier soplo pasaría por voz."""

    # -- Estado interno ---------------------------------------------------- #
    _threshold: float = 0.0
    _frames: list[np.ndarray] = field(default_factory=list)
    _speech_frames: int = 0
    _silence_frames: int = 0
    _started: bool = False
    _elapsed_frames: int = 0
    _state: UtteranceState = UtteranceState.WAITING

    # -- Configuración ------------------------------------------------------ #

    @property
    def frame_size(self) -> int:
        """Número de muestras que compone un bloque."""
        return int(self.sample_rate * self.frame_ms / 1000)

    @property
    def threshold(self) -> float:
        """Umbral de energía en uso."""
        return self._threshold

    @property
    def state(self) -> UtteranceState:
        return self._state

    def calibrate(self, noise: AudioClip) -> float:
        """Fija el umbral a partir de una muestra de ruido ambiente.

        Args:
            noise: Fragmento grabado en silencio, de medio segundo en adelante.

        Returns:
            El umbral resultante.
        """
        self._threshold = max(rms(noise.samples) * self.energy_margin, self.floor_threshold)
        return self._threshold

    def reset(self) -> None:
        """Prepara el detector para una intervención nueva.

        Conserva el umbral calibrado: el ruido ambiente no cambia entre una
        frase y la siguiente, y recalibrar en cada turno sería malgastar
        medio segundo cada vez.
        """
        self._frames.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._started = False
        self._elapsed_frames = 0
        self._state = UtteranceState.WAITING

    # -- Procesamiento ------------------------------------------------------ #

    def push(self, frame: np.ndarray) -> UtteranceState:
        """Incorpora un bloque de audio y actualiza la situación.

        Args:
            frame: Bloque de muestras enteras de 16 bits.

        Returns:
            El estado tras procesarlo. Una vez alcanzado ``FINISHED`` o
            ``TIMED_OUT``, los bloques posteriores se ignoran.
        """
        if self._state in (UtteranceState.FINISHED, UtteranceState.TIMED_OUT):
            return self._state

        if self._threshold <= 0:
            self._threshold = self.floor_threshold

        self._frames.append(frame)
        self._elapsed_frames += 1

        if rms(frame) >= self._threshold:
            self._speech_frames += 1
            self._silence_frames = 0
            self._started = True
            self._state = UtteranceState.SPEAKING
        elif self._started:
            self._silence_frames += 1

        return self._evaluate()

    def _evaluate(self) -> UtteranceState:
        """Decide si la intervención ha concluido."""
        transcurrido_ms = self._elapsed_frames * self.frame_ms

        if not self._started:
            if transcurrido_ms >= self.start_timeout_ms:
                self._state = UtteranceState.TIMED_OUT
            return self._state

        if transcurrido_ms >= self.max_utterance_ms:
            self._state = UtteranceState.FINISHED
            return self._state

        silencio_ms = self._silence_frames * self.frame_ms
        if silencio_ms >= self.silence_ms_to_finish:
            habla_ms = self._speech_frames * self.frame_ms
            # Una ráfaga demasiado breve fue un ruido, no una orden: se
            # descarta y se sigue esperando en lugar de transcribir un golpe.
            if habla_ms >= self.min_speech_ms:
                self._state = UtteranceState.FINISHED
            else:
                self.reset()

        return self._state

    # -- Resultado ---------------------------------------------------------- #

    @property
    def clip(self) -> AudioClip:
        """Audio capturado hasta el momento.

        Se recorta el silencio sobrante del final, dejando un margen para no
        cercenar el último fonema.
        """
        if not self._frames:
            return AudioClip(np.zeros(0, dtype=np.int16), self.sample_rate)

        margen_frames = max(1, int(200 / self.frame_ms))
        sobrantes = max(0, self._silence_frames - margen_frames)
        utiles = self._frames[: len(self._frames) - sobrantes] or self._frames

        return AudioClip.from_frames(utiles, self.sample_rate)
