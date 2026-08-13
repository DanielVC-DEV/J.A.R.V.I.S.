"""Herramientas de teclado y ratón.

Envoltorios sobre ``jarvis.computer.input``. Las descripciones importan más
que en otras herramientas: el modelo debe entender cuándo escribir texto y
cuándo pulsar una combinación, y que las coordenadas del ratón solo tienen
sentido si sabe dónde está lo que quiere pulsar.
"""

from __future__ import annotations

from jarvis.computer import input as entrada
from jarvis.core.registry import tool
from jarvis.security.risk import Risk

__all__ = ["click_at", "move_mouse", "press_keys", "scroll", "type_text"]


@tool(risk=Risk.SAFE, category="input")
def type_text(text: str) -> str:
    """Escribe texto en la ventana con el foco. Admite acentos y eñes.

    Args:
        text: Texto a escribir.
    """
    escritos = entrada.type_text(text)
    return f"Escritos {escritos} caracteres."


@tool(risk=Risk.SAFE, category="input")
def press_keys(combination: str) -> str:
    """Pulsa una tecla o combinación: «ctrl+s», «alt+tab», «intro».

    Args:
        combination: Combinación a pulsar. Admite nombres en español.
    """
    return f"Pulsado {entrada.press_keys(combination)}."


@tool(risk=Risk.SAFE, category="input")
def move_mouse(x: int, y: int) -> str:
    """Mueve el puntero. Píxeles desde la esquina superior izquierda.

    Args:
        x: Coordenada horizontal.
        y: Coordenada vertical.
    """
    destino = entrada.move_mouse(x, y)
    return f"Puntero en ({destino.x}, {destino.y})."


@tool(risk=Risk.SAFE, category="input")
def click_at(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    double: bool = False,
) -> str:
    """Hace clic con el ratón, en una posición o donde esté el puntero.

    Args:
        x: Coordenada horizontal. Omítela para no mover el puntero.
        y: Coordenada vertical.
        button: left, right o middle.
        double: Verdadero para doble clic.
    """
    destino = entrada.click(x, y, button=button, double=double)
    tipo = "Doble clic" if double else "Clic"
    return f"{tipo} con el botón {button} en ({destino.x}, {destino.y})."


@tool(risk=Risk.SAFE, category="input")
def scroll(amount: int) -> str:
    """Desplaza la rueda del ratón sobre la ventana activa.

    Args:
        amount: Muescas. Positivo sube, negativo baja; 3 es un paso cómodo.
    """
    return f"Desplazadas {entrada.scroll(amount)} muescas."
