"""Contrato con los modelos de lenguaje.

Define una representación de la conversación **neutral respecto al proveedor**.
El orquestador trabaja siempre con estos tipos; cada implementación concreta se
encarga de traducirlos al formato de su API.

El propósito es que añadir un modelo local más adelante consista en escribir
una clase que cumpla ``LLMProvider``, sin tocar el bucle del agente.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Block",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "TextBlock",
    "ToolResultBlock",
    "ToolUse",
]


class LLMError(RuntimeError):
    """El proveedor no pudo completar la petición."""


# --------------------------------------------------------------------------- #
# Bloques de contenido
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TextBlock:
    """Texto en lenguaje natural."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    """Petición del modelo para ejecutar una herramienta."""

    id: str
    """Identificador que enlaza esta petición con su resultado."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """Resultado de una herramienta, devuelto al modelo."""

    tool_use_id: str
    content: str
    is_error: bool = False


#: Contenido que puede aparecer en un mensaje.
Block = TextBlock | ToolUse | ToolResultBlock


# --------------------------------------------------------------------------- #
# Conversación
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Message:
    """Un turno de la conversación."""

    role: str
    """``user`` o ``assistant``."""

    blocks: tuple[Block, ...]

    @classmethod
    def user(cls, text: str) -> Message:
        """Construye un mensaje de usuario a partir de texto simple."""
        return cls(role="user", blocks=(TextBlock(text),))

    @classmethod
    def tool_results(cls, results: Sequence[ToolResultBlock]) -> Message:
        """Construye el mensaje que devuelve al modelo los resultados.

        Según el protocolo de uso de herramientas, los resultados se entregan
        en un mensaje con el rol del usuario, aunque no los haya escrito él.
        """
        return cls(role="user", blocks=tuple(results))

    @property
    def text(self) -> str:
        """Concatena el contenido textual del mensaje."""
        return "\n".join(b.text for b in self.blocks if isinstance(b, TextBlock))


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Respuesta del modelo a una petición."""

    blocks: tuple[Block, ...]
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def text(self) -> str:
        """Texto de la respuesta, sin las peticiones de herramienta."""
        return "\n".join(
            b.text.strip() for b in self.blocks if isinstance(b, TextBlock) and b.text.strip()
        )

    @property
    def tool_uses(self) -> tuple[ToolUse, ...]:
        """Herramientas que el modelo quiere ejecutar, en orden."""
        return tuple(b for b in self.blocks if isinstance(b, ToolUse))

    @property
    def wants_tools(self) -> bool:
        """Indica si la respuesta contiene peticiones de herramienta."""
        return bool(self.tool_uses)

    def as_message(self) -> Message:
        """Convierte la respuesta en un mensaje del historial."""
        return Message(role="assistant", blocks=self.blocks)


# --------------------------------------------------------------------------- #
# Proveedor
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMProvider(Protocol):
    """Interfaz que debe cumplir todo backend de modelo de lenguaje."""

    def chat(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> LLMResponse:
        """Solicita una respuesta al modelo.

        Args:
            system: Instrucciones de sistema, incluida la personalidad.
            messages: Historial de la conversación, del más antiguo al más
                reciente.
            tools: Catálogo de herramientas, en el formato del registro.

        Returns:
            La respuesta del modelo.

        Raises:
            LLMError: Si la petición no pudo completarse.
        """
        ...
