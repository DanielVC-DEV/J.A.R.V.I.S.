"""Puente entre el núcleo y la interfaz gráfica.

El orquestador puede tardar varios segundos en un turno: consulta al modelo,
ejecuta herramientas y vuelve a consultar. Ejecutarlo en el hilo de la
interfaz congelaría la ventana durante todo ese tiempo, y Windows la marcaría
como «no responde».

Por eso el núcleo corre en un hilo aparte y se comunica con la ventana
mediante señales de Qt, que es el mecanismo previsto para cruzar hilos con
seguridad. Los eventos que ya emitía el núcleo se traducen aquí a señales sin
cambiar nada del orquestador.

Las confirmaciones invierten el sentido de la comunicación y merecen atención:
el hilo del núcleo necesita una respuesta que solo puede dar el usuario, y el
usuario solo existe en el hilo de la interfaz. Se resuelve emitiendo la
pregunta y bloqueando el hilo del núcleo hasta que la ventana deposita la
respuesta.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from jarvis.core.events import (
    AssistantMessage,
    Event,
    ProviderFailed,
    ThinkingStarted,
    TurnCompleted,
)
from jarvis.core.orchestrator import ConfirmationRequest, Orchestrator
from jarvis.voice.tts import Speaker

__all__ = ["CONFIRMATION_TIMEOUT", "OrchestratorWorker"]

_logger = logging.getLogger(__name__)

#: Espera máxima a que el usuario conteste una confirmación. Sin este tope, un
#: usuario que se marcha dejaría el hilo del núcleo bloqueado para siempre y la
#: aplicación no podría cerrarse.
CONFIRMATION_TIMEOUT = 300.0


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


class OrchestratorWorker(QtCore.QObject):
    """Ejecuta turnos del núcleo fuera del hilo de la interfaz."""

    #: Se emite al empezar cada vuelta del bucle de herramientas.
    thinking = QtCore.Signal(int)

    #: Cada evento del núcleo, tal cual, para que la ventana lo represente.
    event = QtCore.Signal(object)

    #: El turno terminó. Lleva el texto final, para la voz.
    finished = QtCore.Signal(str)

    #: Hay que preguntar al usuario. Lleva la ``ConfirmationRequest``.
    confirmation_needed = QtCore.Signal(object)

    def __init__(self, orchestrator: Orchestrator, speaker: Speaker | None = None) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._orchestrator.confirmer = self._ask_user
        self._speaker = speaker

        self._answered = threading.Event()
        self._answer = False

    # -- Turnos -------------------------------------------------------------- #

    @QtCore.Slot(str)
    def submit(self, text: str) -> None:
        """Procesa una orden. Se invoca desde el hilo del núcleo.

        Args:
            text: Orden del usuario.
        """
        respuesta = ""

        try:
            for evento in self._orchestrator.submit(text):
                self._emit(evento)
                if isinstance(evento, TurnCompleted):
                    respuesta = evento.text
        except Exception as exc:  # noqa: BLE001 - frontera del hilo
            # Una excepción sin capturar aquí mataría el hilo en silencio y la
            # ventana quedaría esperando una respuesta que nunca llega.
            _logger.exception("Fallo inesperado durante el turno.")
            self.event.emit(ProviderFailed(message=f"Fallo inesperado: {exc}"))

        if respuesta and self._speaker is not None:
            self._speaker.speak(respuesta)

        self.finished.emit(respuesta)

    def _emit(self, event: Event) -> None:
        """Traslada un evento del núcleo a la interfaz."""
        if isinstance(event, ThinkingStarted):
            self.thinking.emit(event.iteration)
        elif isinstance(event, AssistantMessage | TurnCompleted):
            self.event.emit(event)
        else:
            self.event.emit(event)

    # -- Confirmaciones ------------------------------------------------------ #

    def _ask_user(self, request: ConfirmationRequest) -> bool:
        """Consulta al usuario y espera su respuesta.

        Se ejecuta en el hilo del núcleo. Emite la petición —que Qt entrega al
        hilo de la interfaz— y se queda esperando a que la ventana llame a
        ``provide_answer``.

        Args:
            request: Acción que requiere autorización.

        Returns:
            ``True`` si el usuario la autorizó. Si no contesta a tiempo se
            devuelve ``False``: ante la duda, no actuar.
        """
        self._answered.clear()
        self._answer = False

        self.confirmation_needed.emit(request)

        if not self._answered.wait(timeout=CONFIRMATION_TIMEOUT):
            _logger.info("La confirmación de «%s» expiró.", request.tool)
            return False

        return self._answer

    @QtCore.Slot(bool)
    def provide_answer(self, authorised: bool) -> None:
        """Deposita la respuesta del usuario. Se invoca desde la interfaz."""
        self._answer = authorised
        self._answered.set()

    def abort_pending(self) -> None:
        """Desbloquea una confirmación pendiente al cerrar la aplicación."""
        self._answer = False
        self._answered.set()
