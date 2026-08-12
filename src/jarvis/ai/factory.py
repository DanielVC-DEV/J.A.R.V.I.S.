"""Selección del proveedor de modelo según la configuración.

Único lugar del programa donde se decide con qué API se habla. El resto del
código depende del protocolo ``LLMProvider``, nunca de una implementación
concreta.
"""

from __future__ import annotations

from jarvis.ai.provider import LLMProvider
from jarvis.config.settings import Provider, Settings

__all__ = ["create_provider"]


def create_provider(settings: Settings) -> LLMProvider:
    """Construye el proveedor indicado en la configuración.

    Args:
        settings: Configuración de la aplicación.

    Returns:
        Un proveedor listo para usar.

    Raises:
        ConfigurationError: Si falta la clave, el modelo o la dirección.
        LLMError: Si falta alguna dependencia del proveedor elegido.
    """
    if settings.provider is Provider.ANTHROPIC:
        from jarvis.ai.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)

    from jarvis.ai.openai_provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(settings)
