"""Implementación de ``LLMProvider`` sobre el formato de API de OpenAI.

Ese formato se ha convertido en un estándar de hecho: lo exponen Groq,
OpenRouter, Ollama, Cerebras, Mistral, Together y Gemini, entre otros. Una
sola implementación, parametrizada por la dirección del servicio, los cubre
todos.

Se habla HTTP directamente en lugar de usar un cliente específico. Así no se
añade ninguna dependencia —``httpx`` ya viene con el otro proveedor— y los
mensajes de error pueden redactarse con el detalle que el usuario necesita.

La traducción desde la representación neutral tiene tres diferencias
importantes respecto a la API de Anthropic:

* Las instrucciones de sistema son un mensaje más, no un parámetro aparte.
* Los argumentos de una herramienta viajan como **cadena JSON**, no como
  objeto, y el modelo puede emitirla mal formada.
* Cada resultado de herramienta es un mensaje independiente con rol ``tool``,
  en lugar de agruparse todos en un mensaje de usuario.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx

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

__all__ = ["OpenAICompatibleProvider"]

_logger = logging.getLogger(__name__)

#: Margen de espera. Un asistente de voz que tarde más de esto ya ha fallado
#: desde el punto de vista del usuario, por mucho que la respuesta llegue.
REQUEST_TIMEOUT_SECONDS = 60.0

#: Reintentos ante un límite de peticiones. Dos bastan para superar los cortes
#: por tokens por minuto sin que el usuario perciba que el asistente se colgó.
MAX_RETRIES = 2

#: Tope de espera entre reintentos. Por encima, es preferible informar del
#: límite a dejar al usuario esperando sin saber qué ocurre.
MAX_RETRY_WAIT_SECONDS = 20.0

#: El servicio indica el tiempo restante dentro del mensaje de error.
_RETRY_IN = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b", re.IGNORECASE)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Determina cuánto esperar antes de reintentar.

    Se consulta primero la cabecera estándar, después el tiempo que el propio
    mensaje de error indica, y solo en último término se recurre a una espera
    creciente. Respetar lo que dice el servicio evita tanto reintentar
    demasiado pronto como esperar de más.

    Args:
        response: Respuesta que informó del límite.
        attempt: Número de intento ya realizado, empezando en cero.

    Returns:
        Los segundos que conviene esperar.
    """
    cabecera = response.headers.get("retry-after")
    if cabecera:
        try:
            return min(float(cabecera), MAX_RETRY_WAIT_SECONDS)
        except ValueError:
            pass

    coincidencia = _RETRY_IN.search(response.text or "")
    if coincidencia:
        valor = float(coincidencia.group(1))
        segundos = valor / 1000 if coincidencia.group(2).lower() == "ms" else valor
        # Un margen sobre el tiempo indicado evita reintentar justo en el
        # límite y volver a ser rechazado.
        return min(segundos + 0.5, MAX_RETRY_WAIT_SECONDS)

    return min(2.0 * (attempt + 1), MAX_RETRY_WAIT_SECONDS)


def _describe_connection_failure(exc: httpx.RequestError, base_url: str) -> str:
    """Redacta un mensaje útil a partir de un fallo de conexión.

    Las causas posibles llevan a soluciones muy distintas —no hay red, el
    nombre no resuelve, un cortafuegos corta la conexión, un proxy intercepta
    el cifrado— y el tipo de excepción las distingue. Presentarlas todas como
    «no se pudo contactar» obliga al usuario a adivinar.

    Args:
        exc: Excepción de transporte lanzada por el cliente.
        base_url: Dirección a la que se intentaba llegar.

    Returns:
        Un mensaje con la causa probable y qué comprobar.
    """
    causa = str(exc).lower()
    encabezado = f"No se pudo contactar con el servicio en {base_url}."

    # El contenido del mensaje se examina antes que el tipo: un fallo de
    # certificado llega envuelto en ConnectError y, de otro modo, quedaría
    # confundido con un corte de red.
    if "certificate" in causa or "ssl" in causa:
        return (
            f"{encabezado} Falló la validación del certificado. Es habitual en "
            "redes que inspeccionan el tráfico cifrado con su propio "
            "certificado."
        )

    if isinstance(exc, httpx.ConnectError):
        if "getaddrinfo" in causa or "name or service" in causa or "nodename" in causa:
            return (
                f"{encabezado} El nombre del servidor no se pudo resolver: no "
                "hay conexión a internet, o el DNS de esta red bloquea el "
                "dominio."
            )
        if "refused" in causa:
            return (
                f"{encabezado} La conexión fue rechazada. Si apuntas a un "
                "servicio local como Ollama, comprueba que esté en marcha."
            )
        return (
            f"{encabezado} No se pudo establecer la conexión, lo que en redes "
            "corporativas o educativas suele deberse a un cortafuegos. Si tu "
            "red usa proxy, define HTTPS_PROXY antes de arrancar."
        )

    if isinstance(exc, httpx.ProxyError):
        return f"{encabezado} El proxy configurado rechazó la conexión."

    return f"{encabezado} {type(exc).__name__}: {exc}"


def _encode_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Traduce el catálogo de herramientas al formato de OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": herramienta["name"],
                "description": herramienta["description"],
                "parameters": herramienta["input_schema"],
            },
        }
        for herramienta in tools
    ]


def _encode_message(message: Message) -> list[dict[str, Any]]:
    """Traduce un mensaje neutral a uno o varios mensajes del formato OpenAI.

    Devuelve una lista porque un único mensaje neutral con varios resultados
    de herramienta se convierte en varios mensajes con rol ``tool``.

    Args:
        message: Mensaje en la representación neutral.

    Returns:
        Los mensajes equivalentes, en orden.
    """
    resultados = [b for b in message.blocks if isinstance(b, ToolResultBlock)]
    if resultados:
        return [
            {
                "role": "tool",
                "tool_call_id": bloque.tool_use_id,
                "content": bloque.content,
            }
            for bloque in resultados
        ]

    texto = "\n".join(b.text for b in message.blocks if isinstance(b, TextBlock))
    peticiones = [b for b in message.blocks if isinstance(b, ToolUse)]

    if not peticiones:
        return [{"role": message.role, "content": texto}]

    return [
        {
            "role": "assistant",
            "content": texto or None,
            "tool_calls": [
                {
                    "id": peticion.id,
                    "type": "function",
                    "function": {
                        "name": peticion.name,
                        "arguments": json.dumps(peticion.arguments, ensure_ascii=False),
                    },
                }
                for peticion in peticiones
            ],
        }
    ]


def _decode_arguments(raw: Any, tool_name: str) -> dict[str, Any]:
    """Interpreta los argumentos de una llamada a herramienta.

    Viajan como cadena JSON y los modelos abiertos la emiten mal formada con
    cierta frecuencia. Devolver un diccionario vacío permite que la validación
    del registro produzca un mensaje que el modelo pueda entender y corregir,
    en lugar de interrumpir el turno con una excepción.

    Args:
        raw: Valor recibido, normalmente una cadena JSON.
        tool_name: Nombre de la herramienta, solo para el mensaje de error.

    Returns:
        Los argumentos interpretados, o un diccionario vacío si no fue posible.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}

    try:
        interpretado = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}

    if isinstance(interpretado, dict):
        return interpretado
    return {"__valor__": interpretado} if interpretado is not None else {}


def _decode_message(payload: dict[str, Any]) -> tuple[Block, ...]:
    """Traduce el mensaje devuelto por el servicio a bloques neutrales."""
    bloques: list[Block] = []

    contenido = payload.get("content")
    if isinstance(contenido, str) and contenido.strip():
        bloques.append(TextBlock(text=contenido))

    for llamada in payload.get("tool_calls") or []:
        funcion = llamada.get("function") or {}
        nombre = funcion.get("name") or ""
        if not nombre:
            continue
        bloques.append(
            ToolUse(
                id=llamada.get("id") or f"call_{nombre}",
                name=nombre,
                arguments=_decode_arguments(funcion.get("arguments"), nombre),
            )
        )

    return tuple(bloques)


class OpenAICompatibleProvider:
    """Cliente de cualquier servicio que exponga el formato de OpenAI."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        """Prepara el proveedor.

        Args:
            settings: Configuración de la aplicación.
            client: Cliente HTTP alternativo. Se admite para poder sustituirlo
                en las pruebas por uno que no salga a la red.

        Raises:
            ConfigurationError: Si falta la dirección del servicio o el modelo.
        """
        self._settings = settings
        self._base_url = settings.resolved_base_url()
        self._model = settings.resolved_model()

        # Las cabeceras se adjuntan a cada petición, no al cliente. Así la
        # autenticación sigue siendo responsabilidad del proveedor aunque se le
        # inyecte un cliente ajeno, como ocurre en las pruebas.
        # Ollama en local no exige clave; el resto sí.
        self._headers = {"Content-Type": "application/json"}
        if settings.has_api_key():
            self._headers["Authorization"] = f"Bearer {settings.require_api_key()}"

        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

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
            LLMError: Si la petición falla o la respuesta es incomprensible.
        """
        cuerpo: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._settings.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *[m for mensaje in messages for m in _encode_message(mensaje)],
            ],
        }
        if tools:
            cuerpo["tools"] = _encode_tools(tools)
            cuerpo["tool_choice"] = "auto"

        respuesta = self._post_with_retries(cuerpo)

        if respuesta.status_code != httpx.codes.OK:
            raise LLMError(self._describe_failure(respuesta))

        return self._decode_response(respuesta)

    def _post_with_retries(self, body: dict[str, Any]) -> httpx.Response:
        """Envía la petición, reintentando si el servicio pide esperar.

        Las capas gratuitas limitan los tokens por minuto, y una orden que
        encadena varias herramientas los agota con facilidad. El servicio
        indica cuánto falta para poder continuar, de modo que esperar ese
        tiempo convierte un fallo en una pausa que el usuario apenas nota.

        Args:
            body: Cuerpo de la petición.

        Returns:
            La respuesta obtenida. Si tras agotar los reintentos sigue siendo
            un error, se devuelve tal cual para que la capa superior la
            explique.

        Raises:
            LLMError: Si la petición no llegó a completarse por red o espera.
        """
        for intento in range(MAX_RETRIES + 1):
            try:
                respuesta = self._client.post(
                    "/chat/completions", json=body, headers=self._headers
                )
            except httpx.TimeoutException as exc:
                raise LLMError(
                    f"El servicio no respondió en "
                    f"{REQUEST_TIMEOUT_SECONDS:.0f} segundos."
                ) from exc
            except httpx.RequestError as exc:
                raise LLMError(
                    _describe_connection_failure(exc, self._base_url)
                ) from exc

            if respuesta.status_code != httpx.codes.TOO_MANY_REQUESTS:
                return respuesta
            if intento == MAX_RETRIES:
                return respuesta

            espera = _retry_delay(respuesta, intento)
            _logger.info(
                "Límite de peticiones alcanzado; esperando %.1f s antes de "
                "reintentar (%d de %d).",
                espera,
                intento + 1,
                MAX_RETRIES,
            )
            time.sleep(espera)

        return respuesta  # pragma: no cover - inalcanzable

    # -- Interpretación ----------------------------------------------------- #

    def _describe_failure(self, response: httpx.Response) -> str:
        """Redacta un mensaje comprensible a partir de una respuesta de error."""
        detalle = ""
        try:
            cuerpo = response.json()
        except ValueError:
            detalle = response.text[:400]
        else:
            error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
            if isinstance(error, dict):
                detalle = str(error.get("message") or error)
            elif isinstance(error, str):
                detalle = error
            else:
                detalle = json.dumps(cuerpo, ensure_ascii=False)[:400]

        if response.status_code == httpx.codes.UNAUTHORIZED:
            return f"La clave de API fue rechazada por el servicio. {detalle}"
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            return (
                "Se alcanzó el límite de peticiones del servicio. Espera unos "
                f"segundos e inténtalo de nuevo. {detalle}"
            )
        if response.status_code == httpx.codes.NOT_FOUND:
            return (
                f"El servicio no reconoce el modelo «{self._model}» o la ruta "
                f"configurada. {detalle}"
            )
        return f"El servicio respondió con un error {response.status_code}: {detalle}"

    def _decode_response(self, response: httpx.Response) -> LLMResponse:
        """Convierte una respuesta correcta en la representación neutral."""
        try:
            cuerpo = response.json()
            opcion = cuerpo["choices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(
                "La respuesta del servicio no tiene el formato esperado."
            ) from exc

        uso = cuerpo.get("usage") or {}

        return LLMResponse(
            blocks=_decode_message(opcion.get("message") or {}),
            stop_reason=opcion.get("finish_reason") or "",
            input_tokens=int(uso.get("prompt_tokens") or 0),
            output_tokens=int(uso.get("completion_tokens") or 0),
        )
