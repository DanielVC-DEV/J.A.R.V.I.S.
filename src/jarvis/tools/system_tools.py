"""Herramientas de consulta y control del sistema.

Envoltorios finos sobre ``jarvis.computer``. No contienen lógica propia: su
cometido es exponer al modelo una firma clara y un docstring que le permita
decidir cuándo usarlas.
"""

from __future__ import annotations

from jarvis.computer import audio, system
from jarvis.core.registry import tool
from jarvis.security.risk import Risk

__all__ = ["adjust_volume", "get_system_info", "get_volume", "set_mute", "set_volume"]


@tool(risk=Risk.SAFE, category="system")
def get_system_info() -> str:
    """Consulta el estado del equipo: CPU, memoria, discos y batería."""
    return system.describe_system()


@tool(risk=Risk.SAFE, category="system")
def get_volume() -> str:
    """Consulta el volumen actual del sistema y si está silenciado."""
    nivel = audio.get_volume()
    if audio.is_muted():
        return f"El volumen está al {nivel}%, pero el sonido está silenciado."
    return f"El volumen está al {nivel}%."


@tool(risk=Risk.SAFE, category="system")
def set_volume(level: int) -> str:
    """Fija el volumen a un valor concreto. Para «súbelo un poco», usa adjust_volume.

    Args:
        level: Nivel deseado, entre 0 y 100.
    """
    return f"Volumen ajustado al {audio.set_volume(level)}%."


@tool(risk=Risk.SAFE, category="system")
def adjust_volume(delta: int) -> str:
    """Sube o baja el volumen. Para «súbelo un poco» y similares.

    Args:
        delta: Puntos a sumar; 10 es discreto, 25 notable. Negativo baja.
    """
    return f"Volumen ajustado al {audio.adjust_volume(delta)}%."


@tool(risk=Risk.SAFE, category="system")
def set_mute(muted: bool) -> str:
    """Silencia o restablece el sonido del sistema.

    Args:
        muted: Verdadero para silenciar, falso para restablecer.
    """
    return "Sonido silenciado." if audio.set_muted(muted) else "Sonido restablecido."
