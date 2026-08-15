"""Arranque de la interfaz gráfica.

Reúne las piezas y muestra la ventana. Se mantiene aparte de ``main_window``
para que ese módulo contenga solo la ventana y no la construcción del sistema.

Los fallos de configuración se muestran en un diálogo, no en la consola: quien
abra la aplicación desde un acceso directo no tendrá ninguna consola donde
leerlos.
"""

from __future__ import annotations

import sys

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la interfaz gráfica.

    Args:
        argv: Argumentos de línea de órdenes. Se admite ``--verbose``.

    Returns:
        El código de salida del proceso.
    """
    argumentos = argv if argv is not None else sys.argv[1:]
    verbose = "--verbose" in argumentos

    try:
        from PySide6 import QtWidgets
    except ImportError:
        print(
            "Falta el paquete «PySide6». Instala el proyecto con "
            '«pip install -e ".[gui]"».',
            file=sys.stderr,
        )
        return 1

    aplicacion = QtWidgets.QApplication(argumentos)
    aplicacion.setApplicationName("JARVIS")
    # Minimizar a la bandeja oculta la única ventana sin cerrarla de verdad;
    # sin esto Qt daría por terminada la aplicación en ese mismo instante.
    aplicacion.setQuitOnLastWindowClosed(False)

    import jarvis.tools  # noqa: F401  # el import registra las herramientas
    from jarvis.ai.factory import create_provider
    from jarvis.config.paths import ensure_directories
    from jarvis.config.settings import ConfigurationError, load_settings
    from jarvis.core.orchestrator import Orchestrator
    from jarvis.core.registry import registry
    from jarvis.security.guard import Guard
    from jarvis.tools.memory_tools import manager as memory_manager
    from jarvis.ui.gui.main_window import MainWindow

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        _error(QtWidgets, "Configuración incompleta", str(exc))
        return 1

    if not settings.has_api_key():
        # La aplicación instalada no trae ningún «.env»: sin esto, quien la
        # reciba se encontraría con un error y ningún sitio donde corregirlo.
        from jarvis.ui.gui.settings_dialog import SettingsDialog

        dialogo = SettingsDialog(settings)
        if dialogo.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return 1
        settings = load_settings()

    try:
        provider = create_provider(settings)
    except ConfigurationError as exc:
        _error(QtWidgets, "Configuración incompleta", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - frontera hacia el usuario
        _error(QtWidgets, "No se pudo iniciar", str(exc))
        return 1

    ensure_directories()

    ventana = MainWindow(
        orchestrator=Orchestrator(
            provider=provider,
            registry=registry,
            guard=Guard.with_default_policies(
                settings.resolved_allowed_paths(),
                blocked_patterns=settings.resolved_blocked_patterns(),
            ),
            max_iterations=settings.max_tool_iterations,
            categories=settings.enabled_categories(),
            context_provider=memory_manager().context_for,
        ),
        settings=settings,
        verbose=verbose,
    )
    ventana.show()
    ventana.show_welcome(len(registry), settings.resolved_model())

    return aplicacion.exec()


def _error(widgets: object, titulo: str, mensaje: str) -> None:
    """Muestra un error en un diálogo.

    Quien abra la aplicación desde un acceso directo no tiene consola donde
    leer un mensaje impreso.
    """
    dialogo = widgets.QMessageBox()  # type: ignore[attr-defined]
    dialogo.setWindowTitle("JARVIS")
    dialogo.setIcon(widgets.QMessageBox.Icon.Critical)  # type: ignore[attr-defined]
    dialogo.setText(titulo)
    dialogo.setInformativeText(mensaje)
    dialogo.exec()


if __name__ == "__main__":
    raise SystemExit(main())
