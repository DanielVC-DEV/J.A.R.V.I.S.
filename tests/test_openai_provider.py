"""Pruebas del proveedor compatible con el formato de OpenAI.

La traducción entre la representación neutral y el formato del servicio es
lógica pura y puede comprobarse por completo. Las peticiones HTTP se simulan
con un transporte de ``httpx``, de modo que no se sale a la red.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.ai.openai_provider import (
    OpenAICompatibleProvider,
    _decode_arguments,
    _decode_message,
    _encode_message,
    _encode_tools,
)
from jarvis.ai.provider import (
    LLMError,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUse,
)
from jarvis.config.settings import Provider, Settings


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "provider": Provider.OPENAI,
        "base_url": "groq",
        "model": "modelo-de-prueba",
        "api_key": "clave-de-prueba",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _provider(handler: Any, **overrides: Any) -> OpenAICompatibleProvider:
    """Construye un proveedor cuyo cliente HTTP responde con ``handler``."""
    settings = _settings(**overrides)
    cliente = httpx.Client(
        base_url=settings.resolved_base_url(),
        transport=httpx.MockTransport(handler),
    )
    return OpenAICompatibleProvider(settings, client=cliente)


def _respuesta(**payload: Any) -> Any:
    """Devuelve un manejador que responde siempre lo mismo, y guarda la petición."""
    registro: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        registro["url"] = str(request.url)
        registro["body"] = json.loads(request.content)
        registro["headers"] = dict(request.headers)
        return httpx.Response(200, json=payload)

    handler.registro = registro  # type: ignore[attr-defined]
    return handler


def _payload_texto(texto: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": texto}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 5},
    }


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


def test_known_shortcuts_expand_to_full_urls() -> None:
    assert _settings(base_url="groq").resolved_base_url() == (
        "https://api.groq.com/openai/v1"
    )
    assert _settings(base_url="ollama").resolved_base_url() == (
        "http://localhost:11434/v1"
    )


def test_a_full_url_is_respected() -> None:
    ajustes = _settings(base_url="https://mi-servicio.local/v1/")
    assert ajustes.resolved_base_url() == "https://mi-servicio.local/v1"


def test_a_missing_base_url_is_reported() -> None:
    from jarvis.config.settings import ConfigurationError

    with pytest.raises(ConfigurationError, match="JARVIS_BASE_URL"):
        _settings(base_url="").resolved_base_url()


def test_a_missing_model_is_reported() -> None:
    from jarvis.config.settings import ConfigurationError

    with pytest.raises(ConfigurationError, match="JARVIS_MODEL"):
        _settings(model="").resolved_model()


def test_anthropic_keeps_its_default_model() -> None:
    ajustes = Settings(_env_file=None, provider=Provider.ANTHROPIC)  # type: ignore[arg-type]
    assert ajustes.resolved_model() == "claude-sonnet-5"


def test_surrounding_whitespace_is_trimmed() -> None:
    """Copiar desde el navegador arrastra saltos de línea con frecuencia."""
    assert _settings(model="  modelo\n").resolved_model() == "modelo"


# --------------------------------------------------------------------------- #
# Traducción hacia el servicio
# --------------------------------------------------------------------------- #


def test_tools_are_wrapped_in_the_function_envelope() -> None:
    catalogo = [
        {
            "name": "set_volume",
            "description": "Ajusta el volumen.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    traducido = _encode_tools(catalogo)

    assert traducido[0]["type"] == "function"
    assert traducido[0]["function"]["name"] == "set_volume"
    assert traducido[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {},
    }


def test_a_plain_message_translates_directly() -> None:
    assert _encode_message(Message.user("hola")) == [
        {"role": "user", "content": "hola"}
    ]


def test_tool_arguments_travel_as_a_json_string() -> None:
    """Es la diferencia más traicionera respecto a la API de Anthropic."""
    mensaje = Message(
        "assistant", (ToolUse("c1", "set_volume", {"level": 40}),)
    )
    traducido = _encode_message(mensaje)[0]

    argumentos = traducido["tool_calls"][0]["function"]["arguments"]
    assert isinstance(argumentos, str)
    assert json.loads(argumentos) == {"level": 40}


def test_text_accompanying_a_tool_call_is_preserved() -> None:
    mensaje = Message(
        "assistant", (TextBlock("Voy."), ToolUse("c1", "set_volume", {"level": 40}))
    )
    assert _encode_message(mensaje)[0]["content"] == "Voy."


def test_each_tool_result_becomes_its_own_message() -> None:
    """La otra diferencia: no se agrupan en un único mensaje de usuario."""
    mensaje = Message.tool_results(
        [ToolResultBlock("c1", "Volumen al 40%."), ToolResultBlock("c2", "Listo.")]
    )
    traducido = _encode_message(mensaje)

    assert len(traducido) == 2
    assert traducido[0] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "Volumen al 40%.",
    }
    assert traducido[1]["tool_call_id"] == "c2"


def test_the_system_prompt_becomes_the_first_message() -> None:
    handler = _respuesta(**_payload_texto("Listo."))
    _provider(handler).chat("Eres JARVIS.", [Message.user("hola")], [])

    mensajes = handler.registro["body"]["messages"]
    assert mensajes[0] == {"role": "system", "content": "Eres JARVIS."}
    assert mensajes[1] == {"role": "user", "content": "hola"}


def test_the_key_travels_as_a_bearer_token() -> None:
    handler = _respuesta(**_payload_texto("Listo."))
    _provider(handler).chat("s", [Message.user("hola")], [])
    assert handler.registro["headers"]["authorization"] == "Bearer clave-de-prueba"


def test_tool_choice_is_only_sent_with_a_catalogue() -> None:
    handler = _respuesta(**_payload_texto("Listo."))
    _provider(handler).chat("s", [Message.user("hola")], [])
    assert "tools" not in handler.registro["body"]
    assert "tool_choice" not in handler.registro["body"]


# --------------------------------------------------------------------------- #
# Traducción desde el servicio
# --------------------------------------------------------------------------- #


def test_a_text_answer_is_decoded() -> None:
    handler = _respuesta(**_payload_texto("Buenos días."))
    respuesta = _provider(handler).chat("s", [Message.user("hola")], [])

    assert respuesta.text == "Buenos días."
    assert not respuesta.wants_tools
    assert respuesta.input_tokens == 30
    assert respuesta.output_tokens == 5
    assert respuesta.stop_reason == "stop"


def test_a_tool_call_is_decoded() -> None:
    bloques = _decode_message(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "set_volume",
                        "arguments": '{"level": 40}',
                    },
                }
            ],
        }
    )

    assert len(bloques) == 1
    assert isinstance(bloques[0], ToolUse)
    assert bloques[0].name == "set_volume"
    assert bloques[0].arguments == {"level": 40}


def test_malformed_arguments_do_not_break_the_turn() -> None:
    """Los modelos abiertos emiten JSON inválido con cierta frecuencia."""
    assert _decode_arguments('{"level": 40', "set_volume") == {}
    assert _decode_arguments("", "set_volume") == {}
    assert _decode_arguments(None, "set_volume") == {}


def test_arguments_already_decoded_are_accepted() -> None:
    """Algunos servicios compatibles devuelven ya un objeto en lugar de texto."""
    assert _decode_arguments({"level": 40}, "set_volume") == {"level": 40}


def test_an_empty_text_block_is_discarded() -> None:
    assert _decode_message({"content": "   ", "tool_calls": []}) == ()


def test_a_tool_call_without_a_name_is_discarded() -> None:
    bloques = _decode_message(
        {"content": None, "tool_calls": [{"id": "x", "function": {}}]}
    )
    assert bloques == ()


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #


def _falla(status: int, payload: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return handler


def test_a_rejected_key_is_explained() -> None:
    handler = _falla(401, {"error": {"message": "Invalid API Key"}})
    with pytest.raises(LLMError, match="clave de API fue rechazada"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_rate_limit_is_retried_and_then_explained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las capas gratuitas limitan por minuto: reintentar es lo razonable."""
    from jarvis.ai import openai_provider

    monkeypatch.setattr(openai_provider.time, "sleep", lambda s: None)
    intentos = []

    def handler(request: httpx.Request) -> httpx.Response:
        intentos.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})

    with pytest.raises(LLMError, match="límite de peticiones"):
        _provider(handler).chat("s", [Message.user("hola")], [])

    assert len(intentos) == openai_provider.MAX_RETRIES + 1


def test_a_rate_limit_that_clears_is_transparent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el segundo intento pasa, el usuario solo percibe una pausa."""
    from jarvis.ai import openai_provider

    monkeypatch.setattr(openai_provider.time, "sleep", lambda s: None)
    intentos = []

    def handler(request: httpx.Request) -> httpx.Response:
        intentos.append(1)
        if len(intentos) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=_payload_texto("Listo."))

    respuesta = _provider(handler).chat("s", [Message.user("hola")], [])
    assert respuesta.text == "Listo."
    assert len(intentos) == 2


def test_the_service_reported_wait_is_respected() -> None:
    """Reintentar antes de tiempo solo consume otro intento."""
    from jarvis.ai.openai_provider import _retry_delay

    respuesta = httpx.Response(
        429, json={"error": {"message": "Please try again in 1.8975s."}}
    )
    assert 2.0 < _retry_delay(respuesta, 0) < 2.9

    en_ms = httpx.Response(429, json={"error": {"message": "try again in 250ms"}})
    assert _retry_delay(en_ms, 0) < 1.0


def test_the_retry_after_header_takes_precedence() -> None:
    respuesta = httpx.Response(429, headers={"retry-after": "3"}, text="")
    assert _retry_delay_of(respuesta) == 3.0


def _retry_delay_of(response: httpx.Response) -> float:
    from jarvis.ai.openai_provider import _retry_delay

    return _retry_delay(response, 0)


def test_the_wait_is_capped() -> None:
    from jarvis.ai.openai_provider import MAX_RETRY_WAIT_SECONDS

    respuesta = httpx.Response(429, headers={"retry-after": "9999"}, text="")
    assert _retry_delay_of(respuesta) == MAX_RETRY_WAIT_SECONDS


def test_an_unknown_model_is_explained() -> None:
    handler = _falla(404, {"error": {"message": "model not found"}})
    with pytest.raises(LLMError, match="modelo-de-prueba"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_the_service_message_is_included() -> None:
    """Ocultar el motivo convierte un diagnóstico inmediato en una conjetura."""
    handler = _falla(400, {"error": {"message": "tools[0].name is invalid"}})
    with pytest.raises(LLMError, match="tools\\[0\\].name is invalid"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_non_json_error_is_still_reported() -> None:
    handler = _falla(502, "<html>Bad Gateway</html>")
    with pytest.raises(LLMError, match="502"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_an_unexpected_payload_is_reported() -> None:
    handler = _respuesta(sin_choices=True)
    with pytest.raises(LLMError, match="formato esperado"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_connection_failure_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(LLMError, match="cortafuegos"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_dns_failure_is_distinguished() -> None:
    """No resolver el nombre y ser cortado por un cortafuegos se arreglan distinto."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    with pytest.raises(LLMError, match="no se pudo resolver"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_refused_connection_points_at_a_local_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMError, match="Ollama"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_certificate_failure_is_explained() -> None:
    """Las redes que inspeccionan el tráfico cifrado rompen la validación."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate verify failed")

    with pytest.raises(LLMError, match="certificado"):
        _provider(handler).chat("s", [Message.user("hola")], [])


def test_a_timeout_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("demasiado lento")

    with pytest.raises(LLMError, match="no respondió"):
        _provider(handler).chat("s", [Message.user("hola")], [])
