"""Detección de la palabra de activación.

Permite que el asistente escuche continuamente sin transcribir nada hasta oír
su nombre. Se apoya en **openWakeWord**, que incluye un modelo preentrenado
llamado ``hey_jarvis``: no hay que grabar muestras ni entrenar nada.

El modelo corre en la CPU con un consumo mínimo, lo que importa porque este
detector está activo todo el tiempo mientras el asistente espera.

Dos particularidades que se resuelven aquí:

* El modelo espera bloques de 80 ms y el resto del subsistema trabaja con
  bloques de 30 ms. La clase acumula muestras hasta reunir un bloque completo.
* Una activación deja al modelo con la palabra aún en su ventana de análisis
  durante un instante, de modo que dispararía varias veces seguidas. Se aplica
  un tiempo de espera tras cada detección.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.voice.audio import SAMPLE_RATE

__all__ = ["DEFAULT_WAKE_WORD", "WakeWordDetector", "WakeWordError"]

_logger = logging.getLogger(__name__)

#: Modelo preentrenado que trae la biblioteca. Reconoce «hey JARVIS», no
#: «JARVIS» a secas; distinguir la palabra suelta exigiría entrenar un modelo
#: propio, y el prefijo reduce mucho los disparos accidentales.
DEFAULT_WAKE_WORD = "hey_jarvis"

#: Muestras que analiza el modelo de una vez: 80 ms a 16 kHz.
CHUNK_SAMPLES = 1280


class WakeWordError(RuntimeError):
    """No se pudo preparar la detección de la palabra de activación."""


@dataclass(slots=True)
class WakeWordDetector:
    """Vigila el audio entrante a la espera de la palabra de activación."""

    wake_word: str = DEFAULT_WAKE_WORD

    threshold: float = 0.5
    """Confianza mínima para dar por buena una activación. Subirlo reduce los
    disparos accidentales a costa de obligar a repetirse; bajarlo hace lo
    contrario."""

    cooldown_ms: int = 1500
    """Tiempo durante el cual se ignoran nuevas activaciones. Sin él, una sola
    invocación dispararía varias veces mientras la palabra sigue dentro de la
    ventana de análisis del modelo."""

    sample_rate: int = SAMPLE_RATE

    model: Any = None
    """Modelo ya construido. Se admite para poder probar sin la biblioteca."""

    # -- Estado interno ---------------------------------------------------- #
    _buffer: list[np.ndarray] = field(default_factory=list)
    _buffered_samples: int = 0
    _cooldown_samples: int = 0
    _last_score: float = 0.0

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = self._load_model()

    # -- Carga -------------------------------------------------------------- #

    def _load_model(self) -> Any:
        """Carga el modelo de activación.

        Admite tanto los modelos preentrenados que trae la biblioteca —
        indicados por su nombre— como uno propio, indicado por la ruta de un
        archivo ``.onnx`` o ``.tflite``. Esta segunda vía es la que permite
        usar un modelo entrenado para la pronunciación del usuario.

        Raises:
            WakeWordError: Si falta la biblioteca o el modelo no puede cargarse.
        """
        # La configuración se valida antes de importar nada: una ruta
        # equivocada debe señalarse como tal, no confundirse con una
        # dependencia ausente.
        ruta = Path(self.wake_word)
        es_propio = ruta.suffix.lower() in {".onnx", ".tflite"}

        if es_propio and not ruta.is_file():
            raise WakeWordError(
                f"No se encontró el modelo de activación en «{ruta}». "
                "Comprueba la ruta de JARVIS_WAKE_WORD en el archivo .env."
            )

        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError as exc:
            raise WakeWordError(
                "Falta el paquete «openwakeword». Instala el proyecto con "
                '«pip install -e ".[dev]"», o desactiva la palabra de '
                "activación con JARVIS_WAKE_WORD_ENABLED=false."
            ) from exc

        try:
            if not es_propio:
                # Descarga los modelos preentrenados la primera vez. Es
                # idempotente y no hace nada si ya están en su sitio.
                openwakeword.utils.download_models()

            marco = "tflite" if ruta.suffix.lower() == ".tflite" else "onnx"
            return Model(
                wakeword_models=[str(ruta) if es_propio else self.wake_word],
                inference_framework=marco,
            )
        except WakeWordError:
            raise
        except Exception as exc:  # noqa: BLE001 - la causa varía mucho
            raise WakeWordError(
                f"No se pudo cargar el modelo de activación «{self.wake_word}»: "
                f"{exc}"
            ) from exc

    # -- Consulta ----------------------------------------------------------- #

    @property
    def last_score(self) -> float:
        """Confianza de la última evaluación. Útil para ajustar el umbral."""
        return self._last_score

    @property
    def in_cooldown(self) -> bool:
        """Indica si se están ignorando activaciones tras una reciente."""
        return self._cooldown_samples > 0

    # -- Procesamiento ------------------------------------------------------ #

    def reset(self) -> None:
        """Vacía la reserva de muestras y el tiempo de espera.

        Conviene llamarlo tras atender una orden, para que el detector no
        arrastre restos de la conversación anterior.
        """
        self._buffer.clear()
        self._buffered_samples = 0
        self._cooldown_samples = 0
        self._last_score = 0.0

    def push(self, frame: np.ndarray) -> bool:
        """Incorpora un bloque de audio y comprueba si contiene la activación.

        Args:
            frame: Bloque de muestras enteras de 16 bits. Puede ser de
                cualquier tamaño; internamente se reagrupa en los bloques que
                el modelo espera.

        Returns:
            ``True`` si se detectó la palabra de activación.
        """
        if self._cooldown_samples > 0:
            self._cooldown_samples = max(0, self._cooldown_samples - frame.size)

        self._buffer.append(frame)
        self._buffered_samples += frame.size

        detectado = False
        while self._buffered_samples >= CHUNK_SAMPLES:
            bloque = self._take_chunk()
            if self._evaluate(bloque):
                detectado = True

        return detectado

    def _take_chunk(self) -> np.ndarray:
        """Extrae de la reserva un bloque del tamaño que espera el modelo."""
        unido = np.concatenate(self._buffer)
        bloque, resto = unido[:CHUNK_SAMPLES], unido[CHUNK_SAMPLES:]

        self._buffer = [resto] if resto.size else []
        self._buffered_samples = resto.size
        return bloque

    def _evaluate(self, chunk: np.ndarray) -> bool:
        """Evalúa un bloque y aplica umbral y tiempo de espera."""
        try:
            predicciones = self.model.predict(chunk)
        except Exception as exc:  # noqa: BLE001 - frontera con la biblioteca
            _logger.warning("La detección de activación falló: %s", exc)
            return False

        self._last_score = max(predicciones.values(), default=0.0) if predicciones else 0.0

        if self._last_score < self.threshold or self._cooldown_samples > 0:
            return False

        self._cooldown_samples = int(self.sample_rate * self.cooldown_ms / 1000)
        return True
