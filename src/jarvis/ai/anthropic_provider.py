"""Implementación de ``LLMProvider`` sobre la API de Anthropic.

Traduce entre la representación neutral de la conversación y el formato
concreto de la API. Ninguna otra parte del programa conoce este formato: si
mañana se añade un backend local, solo habrá que escribir otra clase como
esta.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jarvis.ai.provider import (
    Block,
    LLMError,
    LLMResponse,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUse,
)
from jarvis.config.settings import Settings

__all__ = ["AnthropicProvider"]


def _describe_api_error(exc: Any) -> str:
    """Extrae el motivo que la API adjunta a un error de estado.

    El cuerpo de la respuesta contiene la explicación concreta —un campo mal
    formado, un modelo inexistente, un límite superado—. Descartarla y mostrar
    solo el código convierte un diagnóstico inmediato en una conjetura.

    Args:
        exc: Excepción de estado lanzada por el cliente.

    Returns:
        El mensaje de la API, o una descripción genérica si no se pudo extraer.
    """
    cuerpo = getattr(exc, "body", None)
    if isinstance(cuerpo, dict):
        error = cuerpo.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])

    for atributo in ("message", "response"):
        valor = getattr(exc, atributo, None)
        if atributo == "response" and valor is not None:
            texto = getattr(valor, "text", "")
            if texto:
                return texto[:500]
        elif valor:
            return str(valor)[:500]

    return "sin detalle disponible"


class AnthropicProvider:
    """Cliente del modelo de lenguaje alojado en la API de Anthropic."""

    def __init__(self, settings: Settings) -> None:
        """Prepara el proveedor.

        Args:
            settings: Configuración de la aplicación, de la que se toman la
                clave, el modelo y los límites.

        Raises:
            LLMError: Si el paquete cliente no está instalado.
            ConfigurationError: Si no hay ninguna clave de API configurada.
        """
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise LLMError(
                "Falta el paquete «anthropic». Instala el proyecto con "
                "«pip install -e .»."
            ) from exc

        self._settings = settings
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=settings.require_api_key())

    # -- Traducción hacia la API ------------------------------------------- #

    @staticmethod
    def _encode_block(block: Block) -> dict[str, Any]:
        """Convierte un bloque neutral al formato de la API."""
        if isinstance(block, TextBlock):
            return {"type": "text", "text": block.text}
        if isinstance(block, ToolUse):
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.arguments,
            }
        if isinstance(block, ToolResultBlock):
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        raise LLMError(f"Tipo de bloque no admitido: {type(block).__name__}.")

    @classmethod
    def _encode_messages(cls, messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Convierte el historial al formato de la API."""
        return [
            {
                "role": message.role,
                "content": [cls._encode_block(b) for b in message.blocks],
            }
            for message in messages
        ]

    # -- Traducción desde la API ------------------------------------------- #

    @staticmethod
    def _decode_blocks(content: Any) -> tuple[Block, ...]:
        """Convierte la respuesta de la API a bloques neutrales.

        Los tipos desconocidos se descartan en lugar de provocar un error: la
        API puede introducir bloques nuevos y el asistente debe seguir
        funcionando con los que sí entiende.
        """
        bloques: list[Block] = []

        for elemento in content:
            tipo = getattr(elemento, "type", None)
            if tipo == "text":
                bloques.append(TextBlock(text=elemento.text))
            elif tipo == "tool_use":
                bloques.append(
                    ToolUse(
                        id=elemento.id,
                        name=elemento.name,
                        arguments=dict(elemento.input or {}),
                    )
                )

        return tuple(bloques)

    # -- Contrato ----------------------------------------------------------- #

    def chat(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> LLMResponse:
        """Solicita una respuesta al modelo.

        Args:
            system: Instrucciones de sistema.
            messages: Historial de la conversación.
            tools: Catálogo de herramientas, tal como lo produce el registro.

        Returns:
            La respuesta del modelo, ya traducida.

        Raises:
            LLMError: Si la petición falla por red, autenticación, límite de
                uso o cualquier otro motivo. El mensaje se redacta para que el
                usuario entienda qué ocurrió.
        """
        try:
            respuesta = self._client.messages.create(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=system,
                messages=self._encode_messages(messages),
                tools=list(tools),
            )
        except self._anthropic.AuthenticationError as exc:
            raise LLMError(
                "La clave de API fue rechazada. Comprueba que sea correcta y "
                "que siga activa."
            ) from exc
        except self._anthropic.RateLimitError as exc:
            raise LLMError(
                "El proveedor está limitando las peticiones. Inténtalo de nuevo "
                "en unos segundos."
            ) from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(
                "No se pudo contactar con el proveedor. Revisa tu conexión a "
                "internet."
            ) from exc
        except self._anthropic.APIStatusError as exc:
            detalle = _describe_api_error(exc)
            if "credit balance" in detalle.lower():
                raise LLMError(
                    "La cuenta no tiene saldo suficiente. La API se factura "
                    "aparte de la suscripción de Claude: carga crédito en "
                    "platform.claude.com, apartado «Plans & Billing»."
                ) from exc
            raise LLMError(
                f"El proveedor respondió con un error {exc.status_code}: {detalle}"
            ) from exc

        uso = getattr(respuesta, "usage", None)

        return LLMResponse(
            blocks=self._decode_blocks(respuesta.content),
            stop_reason=getattr(respuesta, "stop_reason", "") or "",
            input_tokens=getattr(uso, "input_tokens", 0) or 0,
            output_tokens=getattr(uso, "output_tokens", 0) or 0,
        )
