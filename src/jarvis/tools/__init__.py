"""Envoltorios expuestos al modelo. La implementación vive en otros paquetes.

Importar este paquete registra todas las herramientas en el registro global:
el decorador ``@tool`` actúa en el momento de la importación. Por eso el
orquestador importa este paquete antes de consultar el catálogo.
"""

from jarvis.tools import (
    app_tools,
    file_tools,
    input_tools,
    memory_tools,
    system_tools,
    web_tools,
    window_tools,
)

__all__ = [
    "app_tools",
    "file_tools",
    "input_tools",
    "memory_tools",
    "system_tools",
    "web_tools",
    "window_tools",
]
