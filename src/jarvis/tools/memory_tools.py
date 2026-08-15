"""Herramientas de memoria.

Envoltorios sobre ``jarvis.memory``. El gestor es único para toda la sesión:
la memoria es un estado compartido, no algo que cada herramienta construya por
su cuenta.

Olvidar y borrar piden confirmación. Recordar no: añadir una anotación es
reversible, perderla no.
"""

from __future__ import annotations

from functools import lru_cache

from jarvis.core.registry import tool
from jarvis.memory.manager import MemoryManager
from jarvis.security.risk import Risk

__all__ = ["clear_memory", "forget", "list_memory", "remember", "manager"]


@lru_cache(maxsize=1)
def manager() -> MemoryManager:
    """Devuelve el gestor de memoria de la sesión.

    Se construye la primera vez que se usa, no al importar el módulo: así
    abrir la base de datos no retrasa el arranque de quien no use la memoria.
    """
    return MemoryManager()


@tool(risk=Risk.SAFE, category="memory")
def remember(subject: str, content: str, category: str = "general") -> str:
    """Anota algo que el usuario te pide recordar para futuras conversaciones.

    Úsala solo cuando el usuario lo pida o acepte, no por iniciativa propia.

    Args:
        subject: Sobre qué trata, en pocas palabras: «proyecto de python».
        content: Lo que hay que recordar: «está en D:/Proyectos/JARVIS».
        category: Agrupación: proyectos, preferencias, personal, general.
    """
    return manager().remember(subject, content, category)


@tool(risk=Risk.SAFE, category="memory")
def list_memory() -> str:
    """Enumera todo lo que tienes anotado sobre el usuario."""
    return manager().summary()


@tool(risk=Risk.CONFIRM, category="memory")
def forget(subject: str) -> str:
    """Olvida una anotación concreta.

    Args:
        subject: Sobre qué trata la anotación a borrar.
    """
    return manager().forget(subject)


@tool(risk=Risk.CONFIRM, category="memory")
def clear_memory() -> str:
    """Borra toda la memoria. Irreversible."""
    return manager().clear()
