"""Bucle del agente: convierte una orden en acciones y una respuesta.

Es el corazón del asistente. Un turno se desarrolla así:

1. Se envía el historial al modelo junto con el catálogo de herramientas.
2. Si el modelo pide herramientas, cada una pasa por el guardia de seguridad.
3. Las admitidas se ejecutan y sus resultados vuelven al modelo.
4. Se repite hasta que el modelo responde sin pedir nada más, o hasta agotar
   el límite de vueltas.

El orquestador **no imprime nada**: emite eventos. Y **no pregunta nada**:
delega la confirmación en una función que el cliente proporciona. Esas dos
decisiones son las que permiten reutilizarlo tal cual desde la consola, desde
la interfaz gráfica y desde la voz.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from jarvis.ai.prompts import build_system_prompt
from jarvis.ai.provider import (
    LLMError,
    LLMProvider,
    Message,
    ToolResultBlock,
    ToolUse,
)
from jarvis.core.events import (
    AssistantMessage,
    Event,
    IterationLimitReached,
    ProviderFailed,
    ThinkingStarted,
    ToolDenied,
    ToolExecuted,
    ToolRequested,
    TurnCompleted,
)
from jarvis.core.registry import ToolRegistry, ToolSpec
from jarvis.security.audit import AuditEntry, AuditLog, audit_log
from jarvis.security.guard import Guard, Verdict

__all__ = ["ConfirmationRequest", "Confirmer", "Orchestrator", "always_deny"]


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """Petición de confirmación que se traslada al usuario."""

    tool: str
    arguments: dict[str, Any]
    reason: str


#: Función que el cliente proporciona para resolver las confirmaciones.
#: Devuelve ``True`` si el usuario autoriza la ejecución.
Confirmer = Callable[[ConfirmationRequest], bool]


def always_deny(request: ConfirmationRequest) -> bool:
    """Confirmador por omisión: rechaza todo lo que requiera autorización.

    Es el valor predeterminado a propósito. Un cliente que olvide instalar su
    propio confirmador se comportará de forma conservadora en lugar de
    autorizar en silencio acciones delicadas.
    """
    return False


@dataclass(slots=True)
class Orchestrator:
    """Ejecuta turnos de conversación con acceso a herramientas."""

    provider: LLMProvider
    registry: ToolRegistry
    guard: Guard
    confirmer: Confirmer = always_deny
    audit: AuditLog = field(default_factory=lambda: audit_log)
    max_iterations: int = 8
    extra_context: str = ""
    history: list[Message] = field(default_factory=list)

    categories: frozenset[str] | None = None
    """Categorías de herramientas que se ofrecen al modelo. ``None`` las
    ofrece todas. Acotarlas reduce lo que se reenvía en cada vuelta, que es el
    gasto fijo de un turno."""

    # -- Conversación ------------------------------------------------------- #

    def reset(self) -> None:
        """Descarta el historial y comienza una conversación nueva."""
        self.history.clear()

    def submit(self, text: str) -> Iterator[Event]:
        """Procesa una orden del usuario y emite el progreso como eventos.

        Args:
            text: Orden en lenguaje natural.

        Yields:
            Los eventos del turno, en orden. El último es siempre
            ``TurnCompleted``, salvo que el proveedor falle.
        """
        self.history.append(Message.user(text))

        catalogo = self._catalogue()
        sistema = build_system_prompt(self.extra_context)
        respuesta_final = ""
        entrada = salida = 0

        for iteracion in range(1, self.max_iterations + 1):
            yield ThinkingStarted(iteration=iteracion)

            try:
                respuesta = self.provider.chat(sistema, self.history, catalogo)
            except LLMError as exc:
                yield ProviderFailed(message=str(exc))
                return

            entrada += respuesta.input_tokens
            salida += respuesta.output_tokens
            self.history.append(respuesta.as_message())

            if respuesta.text:
                respuesta_final = respuesta.text
                yield AssistantMessage(text=respuesta.text)

            if not respuesta.wants_tools:
                yield TurnCompleted(
                    text=respuesta_final,
                    iterations=iteracion,
                    input_tokens=entrada,
                    output_tokens=salida,
                )
                return

            resultados: list[ToolResultBlock] = []
            for peticion in respuesta.tool_uses:
                for evento, resultado in self._handle_tool_use(peticion):
                    yield evento
                    if resultado is not None:
                        resultados.append(resultado)

            self.history.append(Message.tool_results(resultados))

        yield IterationLimitReached(limit=self.max_iterations)
        yield TurnCompleted(
            text=respuesta_final,
            iterations=self.max_iterations,
            input_tokens=entrada,
            output_tokens=salida,
        )

    def _catalogue(self) -> list[dict[str, Any]]:
        """Construye el catálogo que se envía al modelo.

        Returns:
            Los esquemas de las herramientas activas. Si no se acotaron
            categorías, todas.
        """
        if self.categories is None:
            return self.registry.schemas()

        return [
            spec.to_schema()
            for spec in self.registry.all()
            if spec.category in self.categories
        ]

    # -- Ejecución de una herramienta --------------------------------------- #

    def _handle_tool_use(
        self, request: ToolUse
    ) -> Iterator[tuple[Event, ToolResultBlock | None]]:
        """Evalúa, confirma y ejecuta una petición de herramienta.

        Yields:
            Pares ``(evento, resultado)``. El resultado es ``None`` en los
            elementos que solo informan del progreso, y contiene el bloque a
            devolver al modelo en el último.
        """
        try:
            spec = self.registry.get(request.name)
        except KeyError:
            texto = (
                f"La herramienta «{request.name}» no existe. "
                f"Las disponibles son: {', '.join(self.registry.names())}."
            )
            yield ToolDenied(name=request.name, reason=texto), None
            yield (
                ToolExecuted(name=request.name, succeeded=False, content=texto),
                ToolResultBlock(request.id, texto, is_error=True),
            )
            return

        veredicto = self.guard.evaluate(spec, request.arguments)

        if veredicto.denied:
            self._record(spec, request, veredicto, executed=False)
            yield ToolDenied(name=spec.name, reason=veredicto.reason), None
            yield (
                ToolExecuted(name=spec.name, succeeded=False, content=veredicto.reason),
                ToolResultBlock(request.id, veredicto.reason, is_error=True),
            )
            return

        if veredicto.needs_confirmation:
            autorizado = self.confirmer(
                ConfirmationRequest(
                    tool=spec.name,
                    arguments=request.arguments,
                    reason=veredicto.reason,
                )
            )
            if not autorizado:
                motivo = "El usuario no autorizó esta acción."
                self._record(spec, request, veredicto, executed=False, error=motivo)
                yield ToolDenied(name=spec.name, reason=motivo, by_user=True), None
                yield (
                    ToolExecuted(name=spec.name, succeeded=False, content=motivo),
                    ToolResultBlock(request.id, motivo, is_error=True),
                )
                return

        yield (
            ToolRequested(
                name=spec.name,
                arguments=request.arguments,
                needed_confirmation=veredicto.needs_confirmation,
            ),
            None,
        )

        inicio = time.perf_counter()
        resultado = self.registry.execute(request.name, request.arguments)
        duracion = (time.perf_counter() - inicio) * 1000

        self._record(
            spec,
            request,
            veredicto,
            executed=True,
            succeeded=resultado.ok,
            error=resultado.error,
            duration_ms=duracion,
        )

        yield (
            ToolExecuted(
                name=spec.name,
                succeeded=resultado.ok,
                content=resultado.content,
                duration_ms=duracion,
            ),
            ToolResultBlock(
                tool_use_id=request.id,
                content=resultado.content,
                is_error=not resultado.ok,
            ),
        )

    # -- Auditoría ---------------------------------------------------------- #

    def _record(
        self,
        spec: ToolSpec,
        request: ToolUse,
        verdict: Verdict,
        *,
        executed: bool,
        succeeded: bool | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Deja constancia de la llamada en el registro de auditoría."""
        self.audit.record(
            AuditEntry(
                tool=spec.name,
                arguments=request.arguments,
                risk=str(spec.risk),
                decision=str(verdict.decision),
                reason=verdict.reason,
                executed=executed,
                succeeded=succeeded,
                error=error,
                duration_ms=duration_ms,
            )
        )
