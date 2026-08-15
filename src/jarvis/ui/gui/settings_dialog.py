"""Pantalla de configuración.

Único lugar donde el usuario final edita las preferencias del asistente sin
tocar un archivo. Sin esto, quien reciba el instalador no tendría forma de
indicar su clave de API ni el proveedor: la aplicación distribuida no trae
ningún «.env» ni consola donde escribir uno.

Los cambios se guardan al pulsar «Guardar» y se aplican en el siguiente
arranque. El proveedor de IA y el orquestador ya están construidos cuando esta
ventana se abre desde el menú, y reconstruirlos en caliente no compensa la
complejidad frente a pedir que se reinicie la aplicación.

La clave de transcripción (``stt_api_key``) queda fuera a propósito: solo la
clave principal pasa por el almacén de credenciales cifrado, y casi nadie
necesita una distinta para la voz. Quien sí la necesite puede seguir
definiéndola en variables de entorno.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6 import QtWidgets

from jarvis.config.settings import (
    ConfigurationError,
    Provider,
    Settings,
    SttBackend,
    delete_api_key,
    save_settings,
    store_api_key,
)

__all__ = ["SettingsDialog"]

_Kind = Literal["text", "int", "float", "bool", "choice"]


@dataclass(frozen=True)
class _Field:
    """Describe un campo del formulario: de dónde sale y cómo se edita."""

    name: str
    label: str
    kind: _Kind
    tooltip: str = ""
    choices: tuple[str, ...] = ()
    minimum: float = 0
    maximum: float = 100


_GENERAL_FIELDS = (
    _Field("provider", "Proveedor", "choice", choices=tuple(Provider)),
    _Field(
        "base_url",
        "Dirección del servicio",
        "text",
        tooltip="Atajo (groq, openrouter, ollama, cerebras, mistral, "
        "together, gemini) o URL completa. Se ignora con Anthropic.",
    ),
    _Field(
        "model",
        "Modelo",
        "text",
        tooltip="Vacío usa el modelo por omisión del proveedor.",
    ),
    _Field("max_tokens", "Máximo de tokens por respuesta", "int", minimum=64, maximum=32000),
    _Field(
        "max_tool_iterations",
        "Vueltas máximas del bucle de herramientas",
        "int",
        minimum=1,
        maximum=25,
    ),
    _Field(
        "log_level",
        "Nivel de registro",
        "choice",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    ),
    _Field(
        "allowed_paths",
        "Carpetas adicionales permitidas",
        "text",
        tooltip="Separadas por punto y coma. Se suman a las personales.",
    ),
    _Field(
        "tool_categories",
        "Categorías de herramientas",
        "text",
        tooltip="system, apps, windows, input, files. Vacío = todas.",
    ),
    _Field(
        "blocked_patterns",
        "Bloquear fragmentos de texto",
        "text",
        tooltip="Separados por punto y coma. Solo añade restricciones, "
        "nunca relaja las de fábrica.",
    ),
)

_VOICE_FIELDS = (
    _Field("stt_backend", "Motor de transcripción", "choice", choices=tuple(SttBackend)),
    _Field("stt_model", "Modelo de transcripción", "text", tooltip="Vacío = el propio del motor."),
    _Field("stt_language", "Idioma", "text"),
    _Field(
        "stt_base_url",
        "Servicio de transcripción",
        "text",
        tooltip="Vacío reutiliza el del modelo de lenguaje, si es compatible.",
    ),
    _Field("stt_device", "Dispositivo (motor local)", "choice", choices=("auto", "cuda", "cpu")),
    _Field(
        "mic_device",
        "Índice del micrófono",
        "text",
        tooltip="Vacío = predeterminado del sistema. "
        "«python scripts/probar_voz.py» para verlos.",
    ),
    _Field(
        "stt_vocabulary",
        "Vocabulario de referencia",
        "text",
        tooltip="Nombres propios que Whisper debe reconocer, separados por comas.",
    ),
    _Field("wake_word_enabled", "Escuchar la palabra de activación", "bool"),
    _Field("wake_word", "Palabra de activación", "text"),
    _Field(
        "wake_word_threshold",
        "Confianza mínima de activación",
        "float",
        minimum=0.05,
        maximum=0.99,
    ),
    _Field("hotkey", "Tecla para hablar", "text"),
    _Field("tts_enabled", "Responder en voz alta", "bool"),
    _Field("tts_voice", "Voz neuronal", "text"),
    _Field("tts_rate", "Velocidad del habla", "text", tooltip="Por ejemplo «+15%» o «-10%»."),
    _Field("tts_max_chars", "Longitud máxima al hablar", "int", minimum=0, maximum=5000),
)


class SettingsDialog(QtWidgets.QDialog):
    """Formulario para editar y guardar las preferencias del asistente."""

    def __init__(
        self, settings: Settings, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración de JARVIS")
        self.resize(480, 560)

        self._widgets: dict[str, QtWidgets.QWidget] = {}
        self._clear_api_key = False

        pestañas = QtWidgets.QTabWidget()
        pestañas.addTab(self._build_form_tab(_GENERAL_FIELDS, settings), "General")
        pestañas.addTab(self._build_api_key_tab(settings), "Clave de API")
        pestañas.addTab(self._build_form_tab(_VOICE_FIELDS, settings), "Voz")

        aviso = QtWidgets.QLabel("Los cambios se aplican la próxima vez que abras JARVIS.")
        aviso.setWordWrap(True)

        botones = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        botones.accepted.connect(self._on_save)
        botones.rejected.connect(self.reject)

        disposicion = QtWidgets.QVBoxLayout(self)
        disposicion.addWidget(pestañas)
        disposicion.addWidget(aviso)
        disposicion.addWidget(botones)

    # -- Construcción del formulario ----------------------------------------- #

    def _build_form_tab(
        self, campos: tuple[_Field, ...], settings: Settings
    ) -> QtWidgets.QWidget:
        contenedor = QtWidgets.QWidget()
        formulario = QtWidgets.QFormLayout(contenedor)
        for campo in campos:
            widget = self._create_widget(campo)
            self._load_value(widget, campo, getattr(settings, campo.name))
            if campo.tooltip:
                widget.setToolTip(campo.tooltip)
            self._widgets[campo.name] = widget
            formulario.addRow(campo.label, widget)
        return contenedor

    def _create_widget(self, campo: _Field) -> QtWidgets.QWidget:
        if campo.kind == "choice":
            widget = QtWidgets.QComboBox()
            widget.addItems(list(campo.choices))
            return widget
        if campo.kind == "bool":
            return QtWidgets.QCheckBox()
        if campo.kind == "int":
            entero = QtWidgets.QSpinBox()
            entero.setRange(int(campo.minimum), int(campo.maximum))
            return entero
        if campo.kind == "float":
            flotante = QtWidgets.QDoubleSpinBox()
            flotante.setRange(campo.minimum, campo.maximum)
            flotante.setSingleStep(0.05)
            flotante.setDecimals(2)
            return flotante
        return QtWidgets.QLineEdit()

    def _load_value(
        self, widget: QtWidgets.QWidget, campo: _Field, valor: object
    ) -> None:
        if campo.kind == "choice":
            widget.setCurrentText(str(valor))  # type: ignore[union-attr]
        elif campo.kind == "bool":
            widget.setChecked(bool(valor))  # type: ignore[union-attr]
        elif campo.kind in ("int", "float"):
            widget.setValue(valor)  # type: ignore[union-attr]
        else:
            widget.setText("" if valor is None else str(valor))  # type: ignore[union-attr]

    def _read_value(self, widget: QtWidgets.QWidget, campo: _Field) -> object:
        if campo.kind == "choice":
            return widget.currentText()  # type: ignore[union-attr]
        if campo.kind == "bool":
            return widget.isChecked()  # type: ignore[union-attr]
        if campo.kind in ("int", "float"):
            return widget.value()  # type: ignore[union-attr]

        texto = widget.text().strip()  # type: ignore[union-attr]
        if campo.name == "mic_device":
            return int(texto) if texto else None
        return texto

    # -- Clave de API ---------------------------------------------------------- #

    def _build_api_key_tab(self, settings: Settings) -> QtWidgets.QWidget:
        contenedor = QtWidgets.QWidget()
        disposicion = QtWidgets.QVBoxLayout(contenedor)

        estado = "hay una clave guardada." if settings.has_api_key() else "no hay ninguna clave guardada."
        disposicion.addWidget(QtWidgets.QLabel(f"Estado actual: {estado}"))

        self._api_key_input = QtWidgets.QLineEdit()
        self._api_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("Pega aquí la clave para guardarla o cambiarla…")
        disposicion.addWidget(self._api_key_input)

        borrar = QtWidgets.QPushButton("Borrar la clave guardada")
        borrar.clicked.connect(self._on_clear_api_key)
        disposicion.addWidget(borrar)

        ayuda = QtWidgets.QLabel(
            "La clave se guarda cifrada en el Administrador de Credenciales de "
            "Windows. Nunca se escribe en un archivo de texto, y esta pantalla "
            "no la vuelve a mostrar una vez guardada."
        )
        ayuda.setWordWrap(True)
        disposicion.addWidget(ayuda)
        disposicion.addStretch(1)
        return contenedor

    def _on_clear_api_key(self) -> None:
        self._clear_api_key = True
        self._api_key_input.clear()
        self._api_key_input.setPlaceholderText("Se borrará al guardar.")

    # -- Guardado -------------------------------------------------------------- #

    def _on_save(self) -> None:
        valores: dict[str, object] = {}
        for campo in (*_GENERAL_FIELDS, *_VOICE_FIELDS):
            try:
                valores[campo.name] = self._read_value(self._widgets[campo.name], campo)
            except ValueError:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Configuración",
                    "El índice del micrófono debe ser un número entero, o quedar vacío.",
                )
                return

        nueva_clave = self._api_key_input.text().strip()
        if nueva_clave:
            try:
                store_api_key(nueva_clave)
            except ConfigurationError as exc:
                QtWidgets.QMessageBox.critical(self, "Configuración", str(exc))
                return
        elif self._clear_api_key:
            delete_api_key()

        try:
            save_settings(valores)
        except ConfigurationError as exc:
            QtWidgets.QMessageBox.critical(self, "Configuración", str(exc))
            return

        self.accept()
