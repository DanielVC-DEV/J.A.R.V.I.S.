"""Ventana principal del asistente.

Tercer cliente del núcleo, después de la consola y de la voz, y el que
confirma que la arquitectura cumplía lo que prometía: consume los mismos
eventos que los otros dos y no obliga a tocar ni una línea del orquestador.

La ventana no ejecuta nada por su cuenta. Envía la orden al hilo del núcleo y
se limita a representar lo que le llega de vuelta.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from jarvis.config.settings import Settings, load_settings
from jarvis.core.events import TurnCompleted
from jarvis.core.orchestrator import ConfirmationRequest, Orchestrator
from jarvis.security.audit import audit_log
from jarvis.ui.gui.rendering import (
    PALETTE,
    format_audit_entry,
    format_event,
    format_user_message,
    format_voice_error,
)
from jarvis.ui.gui.settings_dialog import SettingsDialog
from jarvis.ui.gui.voice_worker import VoiceCaptureWorker
from jarvis.ui.gui.worker import OrchestratorWorker
from jarvis.voice.tts import EdgeSpeaker

#: Entradas más recientes que muestra el visor del diario. Es un registro de
#: auditoría personal, no un panel de monitorización: con las últimas basta.
_AUDIT_VIEWER_LIMIT = 200

__all__ = ["MainWindow"]

#: Hoja de estilo. Se define aquí, junto a los elementos que la usan, en lugar
#: de en un archivo aparte: la ventana es pequeña y repartirla dificultaría
#: seguirla.
_STYLE = """
QMainWindow, QWidget { background: #1b1d21; }
QMenuBar { background: #1b1d21; color: #e8e8e8; }
QMenuBar::item:selected { background: #2a2d33; }
QMenu { background: #16181c; color: #e8e8e8; border: 1px solid #2a2d33; }
QMenu::item:selected { background: #2a2d33; }
QTextBrowser {
    background: #16181c; border: 1px solid #2a2d33; border-radius: 8px;
    padding: 10px; font-size: 13px;
}
QLineEdit {
    background: #16181c; border: 1px solid #2a2d33; border-radius: 8px;
    padding: 10px; color: #e8e8e8; font-size: 13px;
}
QLineEdit:focus { border-color: #4da3ff; }
QPushButton {
    background: #2a2d33; border: none; border-radius: 8px;
    padding: 10px 16px; color: #e8e8e8;
}
QPushButton:hover { background: #353941; }
QPushButton:disabled { color: #606060; }
QLabel { color: #808080; font-size: 11px; }
"""


def _build_icon() -> QtGui.QIcon:
    """Dibuja el emblema de la aplicación.

    Todavía no hay ningún archivo de icono en el repositorio —llegará con el
    instalador, en la etapa 10—, y tanto la ventana como la bandeja necesitan
    uno igualmente para no mostrarse en blanco. Se genera en memoria en vez
    de dejarlo pendiente de un archivo que aún no existe.
    """
    tamaño = 64
    lienzo = QtGui.QPixmap(tamaño, tamaño)
    lienzo.fill(QtCore.Qt.GlobalColor.transparent)

    pintor = QtGui.QPainter(lienzo)
    pintor.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pintor.setPen(QtCore.Qt.PenStyle.NoPen)
    pintor.setBrush(QtGui.QColor("#4da3ff"))
    pintor.drawEllipse(0, 0, tamaño, tamaño)

    pintor.setPen(QtGui.QColor("#16181c"))
    fuente = pintor.font()
    fuente.setBold(True)
    fuente.setPixelSize(int(tamaño * 0.6))
    pintor.setFont(fuente)
    pintor.drawText(lienzo.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "J")
    pintor.end()

    return QtGui.QIcon(lienzo)


class MainWindow(QtWidgets.QMainWindow):
    """Ventana de conversación con el asistente."""

    #: Se emite para pedirle al hilo del núcleo que procese una orden. Es una
    #: señal y no una llamada directa porque debe cruzar de hilo.
    submit_requested = QtCore.Signal(str)

    #: Se emite al pulsar el botón de micrófono, para arrancar la grabación
    #: en el hilo de voz. También es una señal por tener que cruzar de hilo.
    voice_capture_requested = QtCore.Signal()

    def __init__(
        self,
        orchestrator: Orchestrator,
        settings: Settings,
        verbose: bool = False,
    ) -> None:
        super().__init__()
        self._verbose = verbose
        self._minimised_notice_shown = False

        self.setWindowTitle("JARVIS")
        self.resize(760, 620)
        self.setStyleSheet(_STYLE)
        self.setWindowIcon(_build_icon())

        self._build_menu()
        self._build_ui()
        self._build_tray()
        self._start_worker(orchestrator, settings)
        self._start_voice_worker(settings)

    # -- Construcción -------------------------------------------------------- #

    def _build_menu(self) -> None:
        """Añade los puntos de la ventana que no son la conversación."""
        menu = self.menuBar().addMenu("&Archivo")
        accion = menu.addAction("Configuración…")
        accion.triggered.connect(self._on_open_settings)
        diario = menu.addAction("Diario de auditoría…")
        diario.triggered.connect(self._on_open_audit_log)

    def _build_tray(self) -> None:
        """Añade el icono de la bandeja del sistema.

        Cerrar con la X un asistente pensado para quedarse escuchando
        sorprendería si terminase el proceso entero. La bandeja deja elegir:
        seguir en segundo plano —el cierre por omisión— o salir de verdad
        desde su menú. Si el sistema no tiene bandeja, no hay nada que
        mostrar aquí y `closeEvent` cierra la aplicación como siempre.
        """
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray: QtWidgets.QSystemTrayIcon | None = None
            return

        self._tray = QtWidgets.QSystemTrayIcon(self.windowIcon(), self)
        self._tray.setToolTip("JARVIS")

        menu = QtWidgets.QMenu(self)
        mostrar = menu.addAction("Mostrar JARVIS")
        mostrar.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        salir = menu.addAction("Salir")
        salir.triggered.connect(self._quit)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _build_ui(self) -> None:
        """Compone los elementos de la ventana."""
        central = QtWidgets.QWidget()
        disposicion = QtWidgets.QVBoxLayout(central)
        disposicion.setContentsMargins(12, 12, 12, 12)
        disposicion.setSpacing(8)

        self._history = QtWidgets.QTextBrowser()
        self._history.setOpenExternalLinks(True)
        disposicion.addWidget(self._history, stretch=1)

        fila = QtWidgets.QHBoxLayout()
        fila.setSpacing(8)

        self._input = QtWidgets.QLineEdit()
        self._input.setPlaceholderText("Escribe una orden…")
        self._input.returnPressed.connect(self._on_submit)
        fila.addWidget(self._input, stretch=1)

        self._mic = QtWidgets.QPushButton("Hablar")
        self._mic.setToolTip("Mantén pulsado para hablar.")
        self._mic.pressed.connect(self._on_mic_pressed)
        self._mic.released.connect(self._on_mic_released)
        fila.addWidget(self._mic)

        self._send = QtWidgets.QPushButton("Enviar")
        self._send.clicked.connect(self._on_submit)
        fila.addWidget(self._send)

        disposicion.addLayout(fila)

        self._status = QtWidgets.QLabel("Listo.")
        disposicion.addWidget(self._status)

        self.setCentralWidget(central)
        self._input.setFocus()

    def _start_worker(self, orchestrator: Orchestrator, settings: Settings) -> None:
        """Pone el núcleo a correr en su propio hilo.

        El altavoz vive en este mismo hilo y no en uno propio: hablar bloquea
        hasta terminar, igual que ejecutar una herramienta, y es precisamente
        lo que se quiere aquí —que no se solape una orden con la respuesta de
        la anterior—.
        """
        self._thread = QtCore.QThread(self)
        self._worker = OrchestratorWorker(orchestrator, speaker=EdgeSpeaker(settings))
        self._worker.moveToThread(self._thread)

        self.submit_requested.connect(self._worker.submit)
        self._worker.thinking.connect(self._on_thinking)
        self._worker.event.connect(self._on_event)
        self._worker.finished.connect(self._on_finished)
        self._worker.confirmation_needed.connect(self._on_confirmation)

        self._thread.start()

    def _start_voice_worker(self, settings: Settings) -> None:
        """Pone la captura de voz a correr en su propio hilo.

        Separado del hilo del núcleo porque graba y transcribe mientras el
        turno anterior puede seguir en curso —o al revés—, y mezclarlos en un
        solo hilo obligaría a elegir cuál espera a cuál sin necesidad.
        """
        self._voice_thread = QtCore.QThread(self)
        self._voice_worker = VoiceCaptureWorker(settings)
        self._voice_worker.moveToThread(self._voice_thread)

        self.voice_capture_requested.connect(self._voice_worker.record_and_transcribe)
        self._voice_worker.transcribed.connect(self._on_voice_transcribed)
        self._voice_worker.heard_nothing.connect(self._on_voice_heard_nothing)
        self._voice_worker.failed.connect(self._on_voice_failed)

        self._voice_thread.start()

    # -- Interacción --------------------------------------------------------- #

    def _append(self, html: str) -> None:
        """Añade un bloque al historial y desplaza hasta el final."""
        if not html:
            return
        self._history.append(html)
        barra = self._history.verticalScrollBar()
        barra.setValue(barra.maximum())

    @QtCore.Slot()
    def _on_open_settings(self) -> None:
        """Abre la pantalla de configuración."""
        dialogo = SettingsDialog(load_settings(), self)
        dialogo.exec()

    @QtCore.Slot()
    def _on_open_audit_log(self) -> None:
        """Muestra el diario de auditoría: qué hizo el asistente y cuándo.

        Es una ventana propia y no una pestaña de la principal porque se
        consulta rara vez, y añadirla siempre en pantalla no compensaría el
        espacio que le quitaría a la conversación.
        """
        dialogo = QtWidgets.QDialog(self)
        dialogo.setWindowTitle("Diario de auditoría")
        dialogo.resize(640, 480)

        historial = QtWidgets.QTextBrowser()
        entradas = audit_log.read_all()[-_AUDIT_VIEWER_LIMIT:]
        if entradas:
            historial.setHtml("".join(format_audit_entry(e) for e in entradas))
        else:
            historial.setHtml(
                f'<p style="color:{PALETTE["muted"]}">Todavía no hay nada '
                "registrado.</p>"
            )

        vaciar = QtWidgets.QPushButton("Vaciar diario")
        vaciar.clicked.connect(lambda: (audit_log.clear(), dialogo.accept()))

        disposicion = QtWidgets.QVBoxLayout(dialogo)
        disposicion.addWidget(historial, stretch=1)
        disposicion.addWidget(vaciar)
        dialogo.exec()

    @QtCore.Slot()
    def _on_submit(self) -> None:
        """Envía la orden escrita por el usuario."""
        texto = self._input.text().strip()
        if not texto:
            return

        self._input.clear()
        self._set_busy(True)
        self._append(format_user_message(texto))
        self.submit_requested.emit(texto)

    def _set_busy(self, busy: bool) -> None:
        """Bloquea la entrada mientras el asistente trabaja.

        Permitir una segunda orden antes de terminar la primera mezclaría dos
        turnos en el mismo historial de conversación.
        """
        self._input.setEnabled(not busy)
        self._mic.setEnabled(not busy)
        self._send.setEnabled(not busy)
        if not busy:
            self._input.setFocus()

    @QtCore.Slot()
    def _on_mic_pressed(self) -> None:
        """Empieza a grabar mientras el botón siga pulsado.

        No pasa por `_set_busy`: deshabilitar el propio botón en mitad de su
        pulsación le impediría recibir el `released` que la termina, y la
        grabación se quedaría abierta para siempre.
        """
        self._input.setEnabled(False)
        self._send.setEnabled(False)
        self._status.setText("Escuchando…")
        self._voice_worker.start_recording()
        self.voice_capture_requested.emit()

    @QtCore.Slot()
    def _on_mic_released(self) -> None:
        """Marca el final de la grabación y deja transcribir."""
        self._mic.setEnabled(False)
        self._status.setText("Transcribiendo…")
        self._voice_worker.stop_recording()

    @QtCore.Slot(str)
    def _on_voice_transcribed(self, text: str) -> None:
        """Envía lo entendido como si se hubiera escrito.

        La ocupación sigue en pie: `_on_finished` la levanta cuando el turno
        que arranca aquí termine, igual que con una orden escrita.
        """
        self._append(format_user_message(text))
        self.submit_requested.emit(text)

    @QtCore.Slot()
    def _on_voice_heard_nothing(self) -> None:
        """No hubo nada que transcribir: silencio, o una pulsación breve."""
        self._set_busy(False)
        self._status.setText("· no te oí")

    @QtCore.Slot(str)
    def _on_voice_failed(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText("Listo.")
        self._append(format_voice_error(message))

    @QtCore.Slot(int)
    def _on_thinking(self, iteration: int) -> None:
        self._status.setText(
            "Pensando…" if iteration == 1 else f"Pensando… (vuelta {iteration})"
        )

    @QtCore.Slot(object)
    def _on_event(self, event: object) -> None:
        self._append(format_event(event, verbose=self._verbose))  # type: ignore[arg-type]
        if isinstance(event, TurnCompleted):
            self._status.setText("Listo.")

    @QtCore.Slot(str)
    def _on_finished(self, text: str) -> None:
        self._set_busy(False)
        self._status.setText("Listo.")

    @QtCore.Slot(object)
    def _on_confirmation(self, request: ConfirmationRequest) -> None:
        """Pregunta al usuario y devuelve la respuesta al hilo del núcleo."""
        argumentos = ", ".join(f"{k}={v!r}" for k, v in request.arguments.items())

        dialogo = QtWidgets.QMessageBox(self)
        dialogo.setWindowTitle("Confirmación necesaria")
        dialogo.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialogo.setText(request.reason)
        dialogo.setInformativeText(f"{request.tool}({argumentos})")
        dialogo.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        )
        # El botón seguro es el que tiene el foco: pulsar Intro sin leer no
        # debe autorizar una acción delicada.
        dialogo.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)

        autorizado = dialogo.exec() == QtWidgets.QMessageBox.StandardButton.Yes
        self._worker.provide_answer(autorizado)

    # -- Bandeja del sistema --------------------------------------------------- #

    @QtCore.Slot(QtWidgets.QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(
        self, reason: QtWidgets.QSystemTrayIcon.ActivationReason
    ) -> None:
        """Un clic —simple o doble, según el sistema— vuelve a mostrar la ventana."""
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    @QtCore.Slot()
    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    @QtCore.Slot()
    def _quit(self) -> None:
        """Sale de verdad, a diferencia de cerrar la ventana."""
        self._shutdown_workers()
        QtWidgets.QApplication.quit()

    # -- Cierre -------------------------------------------------------------- #

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - API de Qt
        """Minimiza a la bandeja en vez de cerrar, si hay una disponible.

        Solo se cierra de verdad cuando no hay bandeja a la que retirarse: en
        ese caso no habría ninguna forma de volver a abrir la ventana.
        """
        if self._tray is not None:
            event.ignore()
            self.hide()
            if not self._minimised_notice_shown:
                # Una sola vez por sesión: repetirlo en cada minimizado sería
                # un aviso que deja de decir nada la segunda vez.
                self._tray.showMessage(
                    "JARVIS sigue activo",
                    "Se está ejecutando en la bandeja del sistema. Haz clic "
                    "en el icono para volver a abrirlo.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
                self._minimised_notice_shown = True
            return

        self._shutdown_workers()
        super().closeEvent(event)

    def _shutdown_workers(self) -> None:
        """Detiene los hilos del núcleo y de la voz antes de terminar.

        Sin esto, una confirmación pendiente dejaría el hilo del núcleo
        bloqueado y el proceso no llegaría a terminar.
        """
        self._voice_worker.stop_recording()
        self._worker.abort_pending()
        self._thread.quit()
        self._thread.wait(3000)
        self._voice_thread.quit()
        self._voice_thread.wait(3000)

    def show_welcome(self, tools: int, model: str) -> None:
        """Escribe el saludo inicial en el historial."""
        self._append(
            f'<p style="color:{PALETTE["muted"]}">'
            f"JARVIS listo · {tools} herramientas · {model}</p>"
        )
