"""Herramientas de gestión de ventanas.

Envoltorios sobre ``jarvis.computer.windows``. Su única lógica propia es
traducir las situaciones esperables —no encontrar la ventana, o encontrar
varias— en respuestas que el modelo pueda aprovechar.
"""

from __future__ import annotations

from jarvis.computer import matching, windows
from jarvis.core.registry import tool
from jarvis.security.risk import Risk

__all__ = [
    "close_window",
    "focus_window",
    "get_active_window",
    "list_windows",
    "minimise_window",
    "maximise_window",
]

#: Número de ventanas que se enumeran. Un escritorio cargado puede tener
#: muchas, y volcarlas todas desperdiciaría el contexto del modelo.
LISTING_LIMIT = 25


def _describe(window: windows.Window) -> str:
    """Redacta una línea legible para una ventana."""
    estado = " (minimizada)" if window.minimised else ""
    proceso = f" — {window.process}" if window.process else ""
    return f"{window.title}{proceso}{estado}"


def _resolve(query: str) -> windows.Window | str:
    """Localiza una ventana o devuelve el texto que explica por qué no pudo.

    Devolver el mensaje en lugar de lanzarlo permite que el modelo lo lea y
    reaccione —preguntando al usuario o probando otro nombre— en vez de recibir
    un error en bruto.
    """
    try:
        return windows.resolve_window(query)
    except matching.AmbiguousMatchError as exc:
        opciones = ", ".join(exc.names)
        return (
            f"«{query}» coincide con varias ventanas: {opciones}. "
            "Pregunta al usuario a cuál se refería."
        )
    except windows.WindowNotFoundError:
        return (
            f"No hay ninguna ventana abierta que corresponda a «{query}». "
            "Puedes usar list_windows para ver cuáles hay."
        )


@tool(risk=Risk.SAFE, category="windows")
def list_windows() -> str:
    """Enumera las ventanas abiertas en el escritorio."""
    abiertas = windows.list_windows()

    if not abiertas:
        return "No hay ninguna ventana abierta."

    lineas = [_describe(v) for v in abiertas[:LISTING_LIMIT]]
    resto = len(abiertas) - len(lineas)
    sufijo = f"\n(y {resto} más)" if resto > 0 else ""

    return "Ventanas abiertas:\n" + "\n".join(f"· {linea}" for linea in lineas) + sufijo


@tool(risk=Risk.SAFE, category="windows")
def get_active_window() -> str:
    """Consulta qué ventana tiene el foco en este momento."""
    activa = windows.get_active_window()
    if activa is None:
        return "No hay ninguna ventana en primer plano."
    return f"La ventana activa es: {_describe(activa)}"


@tool(risk=Risk.SAFE, category="windows")
def focus_window(name: str) -> str:
    """Trae una ventana al frente. La restaura si estaba minimizada.

    Args:
        name: Nombre de la ventana o de la aplicación.
    """
    ventana = _resolve(name)
    if isinstance(ventana, str):
        return ventana

    windows.focus_window(ventana)
    return f"{ventana.title} está ahora en primer plano."


@tool(risk=Risk.SAFE, category="windows")
def minimise_window(name: str) -> str:
    """Minimiza una ventana.

    Args:
        name: Nombre de la ventana o de la aplicación.
    """
    ventana = _resolve(name)
    if isinstance(ventana, str):
        return ventana

    windows.minimise_window(ventana)
    return f"{ventana.title} minimizada."


@tool(risk=Risk.SAFE, category="windows")
def maximise_window(name: str) -> str:
    """Maximiza una ventana.

    Args:
        name: Nombre de la ventana o de la aplicación.
    """
    ventana = _resolve(name)
    if isinstance(ventana, str):
        return ventana

    windows.maximise_window(ventana)
    return f"{ventana.title} maximizada."


@tool(risk=Risk.CONFIRM, category="windows")
def close_window(name: str) -> str:
    """Cierra una ventana. La aplicación puede pedir guardar los cambios.

    Args:
        name: Nombre de la ventana o de la aplicación.
    """
    ventana = _resolve(name)
    if isinstance(ventana, str):
        return ventana

    windows.close_window(ventana)
    return f"Solicitado el cierre de {ventana.title}."
