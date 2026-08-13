"""Gestión de las ventanas abiertas.

Permite enumerar lo que hay en pantalla y actuar sobre una ventana concreta:
traerla al frente, minimizarla, maximizarla o cerrarla.

Se apoya en las API nativas de Windows a través de ``pywin32`` en lugar de una
biblioteca intermedia. La razón es el control: hace falta filtrar las ventanas
que el usuario no considera tales —las auxiliares, las invisibles, las de
herramienta— y decidir qué es «traer al frente» cuando la ventana está
minimizada, y esas decisiones conviene tenerlas a la vista.

La identificación de la ventana por su título reutiliza la coincidencia difusa
de ``jarvis.computer.matching``, la misma que encuentra las aplicaciones: el
usuario dirá «pásate a Chrome» y el título real será «Documento sin título -
Google Chrome».
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from jarvis.computer import matching

__all__ = [
    "Window",
    "WindowError",
    "WindowNotFoundError",
    "close_window",
    "focus_window",
    "get_active_window",
    "list_windows",
    "minimise_window",
    "maximise_window",
    "resolve_window",
]

#: Longitud máxima del título que se lee. Algunas aplicaciones ponen el
#: documento entero en la barra de título.
_MAX_TITLE = 200


class WindowError(RuntimeError):
    """No se pudo consultar o manipular una ventana."""


class WindowNotFoundError(WindowError):
    """Ninguna ventana abierta corresponde al término buscado."""


@dataclass(frozen=True, slots=True)
class Window:
    """Una ventana visible en el escritorio."""

    handle: int
    """Identificador que el sistema asigna a la ventana."""

    title: str
    process: str = ""
    """Nombre del ejecutable que la creó, cuando puede averiguarse."""

    minimised: bool = False

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Denominaciones con las que el usuario puede referirse a ella.

        Se incluye el nombre del proceso porque el usuario suele nombrar la
        aplicación y no el documento: dirá «Chrome», mientras que el título
        real empieza por el nombre de la página que esté abierta.
        """
        if self.process:
            return (self.title, self.process, self.process.removesuffix(".exe"))
        return (self.title,)


def _win32() -> tuple[Any, Any, Any]:
    """Importa los módulos de Windows con un mensaje útil si faltan."""
    if sys.platform != "win32":
        raise WindowError("La gestión de ventanas solo está disponible en Windows.")

    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:
        raise WindowError(
            "Falta el paquete «pywin32». Instala el proyecto con "
            '«pip install -e .» en Windows.'
        ) from exc

    return win32gui, win32con, win32process


def _process_name(handle: int, win32process: Any) -> str:
    """Averigua el ejecutable que creó una ventana.

    Devuelve una cadena vacía si no puede consultarse: ocurre con las ventanas
    de procesos con más privilegios, y no es motivo para descartarlas.
    """
    try:
        import psutil

        _, pid = win32process.GetWindowThreadProcessId(handle)
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001 - la consulta falla de muchas formas
        return ""


def list_windows(include_minimised: bool = True) -> list[Window]:
    """Enumera las ventanas que el usuario reconocería como tales.

    Se descartan las ventanas sin título, las invisibles y las auxiliares que
    las aplicaciones crean para su uso interno. Sin ese filtro, un escritorio
    corriente devuelve más de cien entradas de las que apenas una decena son
    ventanas reales.

    Args:
        include_minimised: Si se incluyen las ventanas minimizadas.

    Returns:
        Las ventanas encontradas, de la más recientemente activa a la menos.

    Raises:
        WindowError: Si el sistema no admite la consulta.
    """
    win32gui, win32con, win32process = _win32()

    ventanas: list[Window] = []

    def visitar(handle: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(handle):
            return

        titulo = (win32gui.GetWindowText(handle) or "").strip()
        if not titulo:
            return

        # Las ventanas de herramienta son auxiliares de la aplicación y no
        # aparecen en la barra de tareas: el usuario no las considera ventanas.
        estilo_extendido = win32gui.GetWindowLong(handle, win32con.GWL_EXSTYLE)
        if estilo_extendido & win32con.WS_EX_TOOLWINDOW:
            return

        minimizada = bool(win32gui.IsIconic(handle))
        if minimizada and not include_minimised:
            return

        ventanas.append(
            Window(
                handle=handle,
                title=titulo[:_MAX_TITLE],
                process=_process_name(handle, win32process),
                minimised=minimizada,
            )
        )

    try:
        win32gui.EnumWindows(visitar, None)
    except Exception as exc:  # noqa: BLE001 - frontera con la API del sistema
        raise WindowError(f"No se pudieron enumerar las ventanas: {exc}") from exc

    return ventanas


def get_active_window() -> Window | None:
    """Devuelve la ventana que tiene el foco, si hay alguna."""
    win32gui, _, win32process = _win32()

    handle = win32gui.GetForegroundWindow()
    if not handle:
        return None

    titulo = (win32gui.GetWindowText(handle) or "").strip()
    if not titulo:
        return None

    return Window(
        handle=handle,
        title=titulo[:_MAX_TITLE],
        process=_process_name(handle, win32process),
        minimised=bool(win32gui.IsIconic(handle)),
    )


def resolve_window(query: str, windows: list[Window] | None = None) -> Window:
    """Determina a qué ventana se refiere un término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        windows: Ventanas entre las que buscar. Si se omite, se consultan las
            abiertas en ese momento.

    Returns:
        La ventana identificada.

    Raises:
        WindowNotFoundError: Si ninguna corresponde al término.
        AmbiguousMatchError: Si varias corresponden por igual.
    """
    candidatas = windows if windows is not None else list_windows()

    try:
        return matching.resolve(
            query, candidatas, lambda w: w.search_terms, what="ventana"
        )
    except matching.NoMatchError as exc:
        raise WindowNotFoundError(str(exc)) from None


def focus_window(window: Window) -> None:
    """Trae una ventana al frente y le da el foco.

    Si estaba minimizada se restaura primero: traer al frente algo que sigue
    minimizado no produce ningún efecto visible, y el usuario creería que la
    orden falló.

    Args:
        window: Ventana a enfocar.

    Raises:
        WindowError: Si el sistema impide dar el foco.
    """
    win32gui, win32con, _ = _win32()

    try:
        if win32gui.IsIconic(window.handle):
            win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(window.handle)
    except Exception as exc:  # noqa: BLE001 - frontera con la API del sistema
        raise WindowError(
            f"No se pudo enfocar «{window.title}». Windows restringe el cambio "
            "de foco cuando la petición no procede de la ventana activa."
        ) from exc


def minimise_window(window: Window) -> None:
    """Minimiza una ventana."""
    win32gui, win32con, _ = _win32()
    win32gui.ShowWindow(window.handle, win32con.SW_MINIMIZE)


def maximise_window(window: Window) -> None:
    """Maximiza una ventana."""
    win32gui, win32con, _ = _win32()
    win32gui.ShowWindow(window.handle, win32con.SW_MAXIMIZE)


def close_window(window: Window) -> None:
    """Solicita el cierre de una ventana.

    Se envía la misma petición que produce el botón de cerrar, de modo que la
    aplicación puede ofrecer guardar los cambios. No se fuerza la terminación
    del proceso: perder el trabajo del usuario sin preguntar sería inadmisible.

    Args:
        window: Ventana a cerrar.
    """
    win32gui, win32con, _ = _win32()
    win32gui.PostMessage(window.handle, win32con.WM_CLOSE, 0, 0)
