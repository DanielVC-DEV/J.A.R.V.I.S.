"""Transcripción local mediante faster-whisper.

Ejecuta Whisper en el propio equipo. Es más rápido que un servicio remoto una
vez está en marcha, funciona sin conexión y no envía la voz del usuario a
ningún tercero.

El precio es la instalación. En GPU, CTranslate2 necesita encontrar las
bibliotecas de CUDA y cuDNN, y su ausencia produce errores poco descriptivos.
Por eso el módulo hace tres cosas antes de rendirse: añade al camino de
búsqueda las bibliotecas instaladas por pip dentro del entorno virtual,
diagnostica el fallo si aun así no las encuentra, y recurre a la CPU en lugar
de dejar al asistente sin oído.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from jarvis.config.settings import Settings
from jarvis.voice.audio import AudioClip
from jarvis.voice.transcriber import Transcription, TranscriptionError

__all__ = ["LocalTranscriber", "prepare_cuda_libraries"]

_logger = logging.getLogger(__name__)

#: Paquetes de pip que traen las bibliotecas nativas de NVIDIA. Instalarlas en
#: el entorno virtual evita tener que configurarlas en todo el sistema, pero
#: hay que añadir sus carpetas al camino de búsqueda a mano.
_NVIDIA_PACKAGES = ("nvidia/cudnn", "nvidia/cublas", "nvidia/cuda_runtime")

MIN_DURATION_SECONDS = 0.25


def prepare_cuda_libraries() -> list[Path]:
    """Añade al camino de búsqueda las bibliotecas de NVIDIA del entorno.

    Cuando ``nvidia-cudnn-cu12`` y compañía se instalan con pip, sus DLL
    quedan dentro de ``site-packages`` y el cargador del sistema no las
    encuentra. Registrarlas aquí evita el error más frecuente de esta fase.

    Returns:
        Las carpetas efectivamente añadidas.
    """
    if sys.platform != "win32":
        return []

    añadidas: list[Path] = []
    for raiz in {Path(p) for p in sys.path if p}:
        for paquete in _NVIDIA_PACKAGES:
            carpeta = raiz / paquete / "bin"
            if not carpeta.is_dir() or carpeta in añadidas:
                continue
            try:
                os.add_dll_directory(str(carpeta))
            except OSError:  # pragma: no cover - depende del sistema
                continue
            añadidas.append(carpeta)

    if añadidas:
        _logger.debug("Bibliotecas de NVIDIA registradas: %s", añadidas)
    return añadidas


class LocalTranscriber:
    """Motor de transcripción que corre en el propio equipo."""

    def __init__(self, settings: Settings, model: Any | None = None) -> None:
        """Carga el modelo de transcripción.

        La carga es costosa —descarga en el primer arranque y ocupa memoria de
        vídeo—, de modo que se realiza una sola vez y el objeto se reutiliza
        durante toda la sesión.

        Args:
            settings: Configuración de la aplicación.
            model: Modelo ya construido. Se admite para las pruebas.

        Raises:
            TranscriptionError: Si falta la dependencia o el modelo no carga.
        """
        self._language = settings.stt_language
        self._model_name = settings.resolved_stt_model()

        if model is not None:
            self._model = model
            self._device = "inyectado"
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "Falta «faster-whisper». Instálalo con "
                '«pip install -e ".[local-stt]"» o cambia JARVIS_STT_BACKEND '
                "a «remote»."
            ) from exc

        prepare_cuda_libraries()
        self._model, self._device = self._load(WhisperModel, settings.stt_device)

    def _load(self, factory: Any, preferred: str) -> tuple[Any, str]:
        """Carga el modelo en el dispositivo indicado, con retirada a la CPU.

        Args:
            factory: Constructor del modelo.
            preferred: ``cuda``, ``cpu`` o ``auto``.

        Returns:
            El modelo cargado y el dispositivo en el que quedó.

        Raises:
            TranscriptionError: Si tampoco pudo cargarse en la CPU.
        """
        intentos: list[tuple[str, str]] = []
        if preferred in ("auto", "cuda"):
            intentos.append(("cuda", "float16"))
        if preferred in ("auto", "cpu") or preferred == "cuda":
            intentos.append(("cpu", "int8"))

        ultimo: Exception | None = None
        for dispositivo, precision in intentos:
            try:
                modelo = factory(
                    self._model_name, device=dispositivo, compute_type=precision
                )
            except Exception as exc:  # noqa: BLE001 - la causa varía mucho
                ultimo = exc
                _logger.warning(
                    "No se pudo cargar Whisper en %s: %s", dispositivo, exc
                )
                continue
            if dispositivo == "cpu" and preferred != "cpu":
                _logger.warning(
                    "Whisper funcionará en CPU: será más lento. Revisa la "
                    "instalación de CUDA y cuDNN si esperabas usar la GPU."
                )
            return modelo, dispositivo

        raise TranscriptionError(
            f"No se pudo cargar el modelo de transcripción «{self._model_name}». "
            f"Último error: {ultimo}"
        )

    @property
    def device(self) -> str:
        """Dispositivo en el que quedó cargado el modelo."""
        return self._device

    def transcribe(self, clip: AudioClip) -> Transcription:
        """Convierte un fragmento de audio en texto.

        Args:
            clip: Audio capturado del micrófono.

        Returns:
            La transcripción obtenida.

        Raises:
            TranscriptionError: Si el modelo falla durante la inferencia.
        """
        if clip.is_empty or clip.duration_seconds < MIN_DURATION_SECONDS:
            return Transcription(text="", duration_seconds=clip.duration_seconds)

        # faster-whisper espera muestras normalizadas entre -1 y 1.
        muestras = clip.samples.astype("float32") / 32768.0

        try:
            segmentos, info = self._model.transcribe(
                muestras,
                language=self._language or None,
                beam_size=5,
                vad_filter=False,
            )
            texto = "".join(segmento.text for segmento in segmentos)
        except Exception as exc:  # noqa: BLE001 - frontera con la biblioteca
            raise TranscriptionError(
                f"La transcripción local falló: {type(exc).__name__}: {exc}"
            ) from exc

        return Transcription(
            text=texto.strip(),
            language=getattr(info, "language", self._language) or self._language,
            duration_seconds=clip.duration_seconds,
        )
