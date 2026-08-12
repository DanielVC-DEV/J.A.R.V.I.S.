"""Eventos que el núcleo emite hacia sus clientes.

El núcleo no imprime nada ni conoce la existencia de una consola o de una
ventana. Comunica su progreso mediante estos eventos, y cada cliente decide
cómo representarlos: la consola los convierte en texto, la interfaz gráfica en
señales de Qt y el motor de voz en audio.

Gracias a esta indirección, añadir la interfaz gráfica en la Fase 7 no exige
tocar el bucle del agente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AssistantMessage",
    "Event",
    "IterationLimitReached",
    "ProviderFailed",
    "ThinkingStarted",
    "ToolDenied",
    "ToolExecuted",
    "ToolRequested",
    "TurnCompleted",
]


@dataclass(frozen=True, slots=True)
class Event:
    """Raíz de la jerarquía de eventos."""


@dataclass(frozen=True, slots=True)
class ThinkingStarted(Event):
    """El núcleo ha consultado al modelo y espera su respuesta."""

    iteration: int = 1


@dataclass(frozen=True, slots=True)
class AssistantMessage(Event):
    """El modelo ha producido texto para el usuario."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolRequested(Event):
    """El modelo quiere ejecutar una herramienta y el guardia la ha admitido."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    needed_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class ToolDenied(Event):
    """La ejecución no se llevó a cabo.

    Attributes:
        by_user: ``True`` si fue el usuario quien la rechazó; ``False`` si la
            impidió una política de seguridad.
    """

    name: str
    reason: str
    by_user: bool = False


@dataclass(frozen=True, slots=True)
class ToolExecuted(Event):
    """Una herramienta terminó de ejecutarse."""

    name: str
    succeeded: bool
    content: str
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class IterationLimitReached(Event):
    """Se agotó el número de vueltas permitidas en un mismo turno."""

    limit: int


@dataclass(frozen=True, slots=True)
class ProviderFailed(Event):
    """El proveedor del modelo no pudo responder."""

    message: str


@dataclass(frozen=True, slots=True)
class TurnCompleted(Event):
    """El turno terminó.

    Attributes:
        text: Respuesta final para el usuario.
        iterations: Vueltas del bucle consumidas.
        input_tokens: Tokens de entrada acumulados en el turno.
        output_tokens: Tokens de salida acumulados en el turno.
    """

    text: str
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
