"""Herramientas de apertura de aplicaciones.

Envoltorios finos sobre ``jarvis.computer.apps``. La única lógica propia es la
traducción de las situaciones esperables —no encontrar la aplicación, o
encontrar varias— en respuestas que el modelo pueda aprovechar, en lugar de
errores en bruto.
"""

from __future__ import annotations

from jarvis.computer import apps
from jarvis.core.registry import tool
from jarvis.security.risk import Risk

__all__ = ["list_applications", "open_program"]

#: Número de aplicaciones que se enumeran al listarlas. El catálogo completo de
#: un equipo puede superar las doscientas entradas, y volcarlo entero
#: desperdiciaría el contexto del modelo sin aportar nada.
LISTING_LIMIT = 60


@tool(risk=Risk.SAFE, category="apps")
def open_program(name: str) -> str:
    """Abre una aplicación instalada. Admite nombres aproximados.

    Queda en primer plano: no hace falta abrirla otra vez para escribir en
    ella.

    Args:
        name: Nombre de la aplicación, como lo dijo el usuario.
    """
    try:
        return apps.open_application(name)
    except apps.AmbiguousApplicationError as exc:
        opciones = ", ".join(app.name for app in exc.candidates)
        return (
            f"«{name}» coincide con varias aplicaciones: {opciones}. "
            "Pregunta al usuario a cuál se refería."
        )
    except apps.ApplicationNotFoundError:
        return (
            f"No hay ninguna aplicación instalada que se corresponda con «{name}». "
            "Puedes usar list_applications para consultar qué hay disponible."
        )


@tool(risk=Risk.SAFE, category="apps")
def list_applications(filter_text: str = "") -> str:
    """Enumera las aplicaciones instaladas.

    Args:
        filter_text: Texto para acotar el listado.
    """
    indice = list(apps.index_applications())

    if not indice:
        return "No pude construir el índice de aplicaciones del equipo."

    if filter_text.strip():
        coincidencias = [m.application for m in apps.rank(filter_text, indice)]
        seleccion = coincidencias[:LISTING_LIMIT]
        if not seleccion:
            return f"Ninguna aplicación se corresponde con «{filter_text}»."
    else:
        seleccion = sorted(indice, key=lambda a: a.name)[:LISTING_LIMIT]

    nombres = ", ".join(app.name for app in seleccion)
    resto = len(indice) - len(seleccion)
    sufijo = f" (y {resto} más)" if resto > 0 and not filter_text.strip() else ""

    return f"Aplicaciones disponibles: {nombres}{sufijo}."
