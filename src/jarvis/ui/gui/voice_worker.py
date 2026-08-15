"""Captura de voz para el botón de micrófono.

Pulsar para hablar: se graba mientras el botón permanece pulsado y se
transcribe al soltarlo. No hay palabra de activación ni detección de silencio
que involucrar —el propio usuario marca el principio y el final—, así que
basta con ``Microphone.record_while``, el mismo método que usa el atajo de
teclado del modo de voz completo.

Corre en su propio hilo, aparte del hilo del núcleo: grabar y transcribir
tardan segundos, y bloquear el hilo de la interfaz la dejaría «sin
responder» según Windows.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from jarvis.config.settings import ConfigurationError, Settings
from jarvis.voice.factory import create_transcriber
from jarvis.voice.recorder import Microphone, MicrophoneError
from jarvis.voice.transcriber import Transcriber, TranscriptionError

__all__ = ["VoiceCaptureWorker"]

_logger = logging.getLogger(__name__)


def _qt() -> Any:
    """Importa Qt con un mensaje útil si falta."""
    try:
        from PySide6 import QtCore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            "Falta el paquete «PySide6». Instala el proyecto con "
            '«pip install -e ".[gui]"».'
        ) from exc
    return QtCore


QtCore = _qt()


class VoiceCaptureWorker(QtCore.QObject):
    """Graba mientras el botón de micrófono está pulsado y transcribe al soltar."""

    #: Orden reconocida, lista para enviarse como si se hubiera escrito.
    transcribed = QtCore.Signal(str)

    #: Se soltó el botón sin que hubiera nada que transcribir —silencio, o una
    #: pulsación demasiado breve.
    heard_nothing = QtCore.Signal()

    #: Fallo de micrófono, de configuración o de transcripción, con un
    #: mensaje ya redactado para mostrarse tal cual.
    failed = QtCore.Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._held = threading.Event()

        try:
            self._microphone: Microphone | None = Microphone(device=settings.mic_device)
            self._transcriber: Transcriber | None = create_transcriber(settings)
            self._setup_error: str | None = None
        except ConfigurationError as exc:
            # Se aplaza el fallo al primer uso en vez de impedir construir la
            # ventana: la voz es opcional, y el resto de la aplicación no debe
            # depender de que esté bien configurada.
            self._microphone = None
            self._transcriber = None
            self._setup_error = str(exc)

    # -- Señalización del botón ------------------------------------------------ #

    def start_recording(self) -> None:
        """Marca el comienzo de la grabación. Se invoca desde la interfaz."""
        self._held.set()

    def stop_recording(self) -> None:
        """Marca el final de la grabación. Se invoca desde la interfaz."""
        self._held.clear()

    # -- Captura --------------------------------------------------------------- #

    @QtCore.Slot()
    def record_and_transcribe(self) -> None:
        """Graba mientras el botón siga pulsado y transcribe lo grabado.

        Se dispara al pulsar el botón; para entonces ``start_recording`` ya
        se ha llamado, así que ``record_while`` deja de grabar en cuanto
        ``stop_recording`` lo marque.
        """
        if self._setup_error is not None:
            self.failed.emit(self._setup_error)
            return

        assert self._microphone is not None
        assert self._transcriber is not None

        try:
            clip = self._microphone.record_while(self._held.is_set)
        except MicrophoneError as exc:
            self.failed.emit(str(exc))
            return

        if clip.is_empty:
            self.heard_nothing.emit()
            return

        try:
            transcripcion = self._transcriber.transcribe(clip)
        except TranscriptionError as exc:
            self.failed.emit(str(exc))
            return

        if transcripcion.is_empty:
            self.heard_nothing.emit()
        else:
            self.transcribed.emit(transcripcion.text)
