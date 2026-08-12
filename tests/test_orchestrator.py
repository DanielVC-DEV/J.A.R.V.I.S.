"""Pruebas del bucle del agente.

El proveedor se sustituye por uno simulado con respuestas predefinidas, de
modo que el bucle se ejercita por completo sin clave de API, sin red y de
forma determinista.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from jarvis.ai.provider import LLMError, LLMResponse, Message, TextBlock, ToolUse
from jarvis.core.events import (
    AssistantMessage,
    IterationLimitReached,
    ProviderFailed,
    ThinkingStarted,
    ToolDenied,
    ToolExecuted,
    ToolRequested,
    TurnCompleted,
)
from jarvis.core.orchestrator import ConfirmationRequest, Orchestrator, always_deny
from jarvis.core.registry import ToolRegistry, tool
from jarvis.security.audit import AuditLog
from jarvis.security.guard import Guard
from jarvis.security.risk import Risk


class FakeProvider:
    """Proveedor simulado que reproduce una secuencia de respuestas."""

    def __init__(self, respuestas: list[LLMResponse]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[dict[str, Any]] = []

    def chat(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
    ) -> LLMResponse:
        self.llamadas.append(
            {"system": system, "messages": list(messages), "tools": list(tools)}
        )
        if not self.respuestas:
            return _texto("Sin más que añadir.")
        return self.respuestas.pop(0)


class FailingProvider:
    """Proveedor que siempre falla, para ejercitar el camino de error."""

    def __init__(self, mensaje: str = "No hay conexión.") -> None:
        self.mensaje = mensaje

    def chat(self, system: str, messages: Sequence[Message], tools: Sequence) -> LLMResponse:
        raise LLMError(self.mensaje)


def _texto(texto: str) -> LLMResponse:
    return LLMResponse(blocks=(TextBlock(texto),), stop_reason="end_turn")


def _usa_tool(nombre: str, argumentos: dict[str, Any], identificador: str = "t1") -> LLMResponse:
    return LLMResponse(
        blocks=(ToolUse(id=identificador, name=nombre, arguments=argumentos),),
        stop_reason="tool_use",
    )


@pytest.fixture
def reg() -> ToolRegistry:
    """Registro con herramientas de los tres niveles de riesgo."""
    registro = ToolRegistry()

    @tool(risk=Risk.SAFE, category="system", registry=registro)
    def saludar(nombre: str) -> str:
        """Saluda a alguien por su nombre.

        Args:
            nombre: Nombre de la persona.
        """
        return f"Hola, {nombre}."

    @tool(risk=Risk.CONFIRM, category="files", registry=registro)
    def borrar(cuantos: int) -> str:
        """Borra archivos.

        Args:
            cuantos: Número de archivos.
        """
        return f"{cuantos} archivos borrados."

    @tool(risk=Risk.BLOCKED, category="system", registry=registro)
    def formatear() -> str:
        """Formatea el disco."""
        return "disco formateado"

    @tool(risk=Risk.SAFE, category="system", registry=registro)
    def romper() -> str:
        """Herramienta que siempre falla."""
        raise RuntimeError("el dispositivo no responde")

    return registro


@pytest.fixture
def auditoria(tmp_path: Path) -> AuditLog:
    return AuditLog(path=tmp_path / "audit.jsonl")


def _orquestador(
    provider: Any, reg: ToolRegistry, auditoria: AuditLog, **kwargs: Any
) -> Orchestrator:
    kwargs.setdefault("guard", Guard.with_default_policies())
    return Orchestrator(provider=provider, registry=reg, audit=auditoria, **kwargs)


# --------------------------------------------------------------------------- #
# Turno sin herramientas
# --------------------------------------------------------------------------- #


def test_a_plain_answer_completes_in_one_iteration(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    orq = _orquestador(FakeProvider([_texto("Buenos días.")]), reg, auditoria)
    eventos = list(orq.submit("hola"))

    assert isinstance(eventos[0], ThinkingStarted)
    assert any(isinstance(e, AssistantMessage) and e.text == "Buenos días." for e in eventos)

    final = eventos[-1]
    assert isinstance(final, TurnCompleted)
    assert final.text == "Buenos días."
    assert final.iterations == 1


def test_the_catalogue_is_sent_to_the_provider(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    provider = FakeProvider([_texto("Listo.")])
    list(_orquestador(provider, reg, auditoria).submit("hola"))

    nombres = {t["name"] for t in provider.llamadas[0]["tools"]}
    assert nombres == {"saludar", "borrar", "formatear", "romper"}


def test_the_personality_is_included_in_the_system_prompt(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    provider = FakeProvider([_texto("Listo.")])
    list(_orquestador(provider, reg, auditoria).submit("hola"))
    assert "JARVIS" in provider.llamadas[0]["system"]


def test_token_usage_is_accumulated(reg: ToolRegistry, auditoria: AuditLog) -> None:
    respuesta = LLMResponse(
        blocks=(TextBlock("Listo."),), input_tokens=120, output_tokens=8
    )
    eventos = list(_orquestador(FakeProvider([respuesta]), reg, auditoria).submit("hola"))

    final = eventos[-1]
    assert isinstance(final, TurnCompleted)
    assert final.input_tokens == 120
    assert final.output_tokens == 8


# --------------------------------------------------------------------------- #
# Turno con herramientas
# --------------------------------------------------------------------------- #


def test_a_safe_tool_runs_without_asking(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    provider = FakeProvider(
        [_usa_tool("saludar", {"nombre": "Ana"}), _texto("Ya la saludé.")]
    )
    eventos = list(_orquestador(provider, reg, auditoria).submit("saluda a Ana"))

    ejecutadas = [e for e in eventos if isinstance(e, ToolExecuted)]
    assert len(ejecutadas) == 1
    assert ejecutadas[0].succeeded
    assert ejecutadas[0].content == "Hola, Ana."
    assert not any(isinstance(e, ToolDenied) for e in eventos)


def test_the_result_is_returned_to_the_model(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    """Sin este paso el modelo no sabría qué ocurrió y respondería a ciegas."""
    provider = FakeProvider(
        [_usa_tool("saludar", {"nombre": "Ana"}), _texto("Ya la saludé.")]
    )
    list(_orquestador(provider, reg, auditoria).submit("saluda a Ana"))

    segunda_llamada = provider.llamadas[1]["messages"]
    bloques = segunda_llamada[-1].blocks
    assert bloques[0].tool_use_id == "t1"
    assert bloques[0].content == "Hola, Ana."
    assert bloques[0].is_error is False


def test_several_tools_in_one_turn(reg: ToolRegistry, auditoria: AuditLog) -> None:
    provider = FakeProvider(
        [
            LLMResponse(
                blocks=(
                    ToolUse(id="a", name="saludar", arguments={"nombre": "Ana"}),
                    ToolUse(id="b", name="saludar", arguments={"nombre": "Luis"}),
                )
            ),
            _texto("Saludados los dos."),
        ]
    )
    eventos = list(_orquestador(provider, reg, auditoria).submit("saluda a ambos"))

    contenidos = [e.content for e in eventos if isinstance(e, ToolExecuted)]
    assert contenidos == ["Hola, Ana.", "Hola, Luis."]


def test_a_failing_tool_does_not_break_the_turn(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    """El fallo se le cuenta al modelo para que pueda explicarlo o reintentar."""
    provider = FakeProvider(
        [_usa_tool("romper", {}), _texto("El dispositivo de audio no responde.")]
    )
    eventos = list(_orquestador(provider, reg, auditoria).submit("haz algo"))

    ejecutada = next(e for e in eventos if isinstance(e, ToolExecuted))
    assert not ejecutada.succeeded
    assert "el dispositivo no responde" in ejecutada.content
    assert isinstance(eventos[-1], TurnCompleted)


def test_an_unknown_tool_is_reported_to_the_model(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    provider = FakeProvider([_usa_tool("inventada", {}), _texto("Me equivoqué.")])
    eventos = list(_orquestador(provider, reg, auditoria).submit("haz algo"))

    denegada = next(e for e in eventos if isinstance(e, ToolDenied))
    assert "inventada" in denegada.reason
    assert "saludar" in denegada.reason  # se le indican las disponibles


# --------------------------------------------------------------------------- #
# Seguridad
# --------------------------------------------------------------------------- #


def test_a_blocked_tool_never_runs(reg: ToolRegistry, auditoria: AuditLog) -> None:
    provider = FakeProvider([_usa_tool("formatear", {}), _texto("No puedo hacer eso.")])
    eventos = list(_orquestador(provider, reg, auditoria).submit("formatea el disco"))

    assert any(isinstance(e, ToolDenied) for e in eventos)
    assert not any(isinstance(e, ToolRequested) for e in eventos)


def test_a_confirm_tool_is_denied_by_default(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    """El confirmador por omisión rechaza: olvidar instalarlo no debe autorizar."""
    provider = FakeProvider([_usa_tool("borrar", {"cuantos": 2340}), _texto("Cancelado.")])
    orq = _orquestador(provider, reg, auditoria, confirmer=always_deny)
    eventos = list(orq.submit("borra esos archivos"))

    denegada = next(e for e in eventos if isinstance(e, ToolDenied))
    assert denegada.by_user
    assert not any(isinstance(e, ToolRequested) for e in eventos)


def test_a_confirm_tool_runs_when_authorised(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    recibidas: list[ConfirmationRequest] = []

    def confirmar(request: ConfirmationRequest) -> bool:
        recibidas.append(request)
        return True

    provider = FakeProvider([_usa_tool("borrar", {"cuantos": 3}), _texto("Hecho.")])
    orq = _orquestador(provider, reg, auditoria, confirmer=confirmar)
    eventos = list(orq.submit("borra tres archivos"))

    assert recibidas[0].tool == "borrar"
    assert recibidas[0].arguments == {"cuantos": 3}

    solicitada = next(e for e in eventos if isinstance(e, ToolRequested))
    assert solicitada.needed_confirmation

    ejecutada = next(e for e in eventos if isinstance(e, ToolExecuted))
    assert ejecutada.content == "3 archivos borrados."


def test_a_destructive_argument_is_denied(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    """La política dinámica endurece el veredicto de una herramienta SAFE."""
    provider = FakeProvider(
        [_usa_tool("saludar", {"nombre": "format c:"}), _texto("No puedo.")]
    )
    eventos = list(_orquestador(provider, reg, auditoria).submit("hazlo"))

    assert any(isinstance(e, ToolDenied) for e in eventos)
    assert not any(isinstance(e, ToolRequested) for e in eventos)


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #


def test_every_call_is_audited(reg: ToolRegistry, auditoria: AuditLog) -> None:
    provider = FakeProvider([_usa_tool("saludar", {"nombre": "Ana"}), _texto("Listo.")])
    list(_orquestador(provider, reg, auditoria).submit("saluda a Ana"))

    entradas = auditoria.read_all()
    assert len(entradas) == 1
    assert entradas[0]["tool"] == "saludar"
    assert entradas[0]["executed"] is True
    assert entradas[0]["succeeded"] is True
    assert entradas[0]["risk"] == "safe"


def test_denied_calls_are_audited_too(reg: ToolRegistry, auditoria: AuditLog) -> None:
    provider = FakeProvider([_usa_tool("formatear", {}), _texto("No puedo.")])
    list(_orquestador(provider, reg, auditoria).submit("formatea"))

    entrada = auditoria.read_all()[0]
    assert entrada["executed"] is False
    assert entrada["decision"] == "deny"


# --------------------------------------------------------------------------- #
# Límites y errores
# --------------------------------------------------------------------------- #


def test_the_iteration_limit_stops_a_runaway_loop(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    """Un modelo que pidiera herramientas sin parar agotaría los tokens."""
    provider = FakeProvider([_usa_tool("saludar", {"nombre": "Ana"}) for _ in range(20)])
    orq = _orquestador(provider, reg, auditoria, max_iterations=3)
    eventos = list(orq.submit("saluda sin parar"))

    assert any(isinstance(e, IterationLimitReached) for e in eventos)
    assert len([e for e in eventos if isinstance(e, ThinkingStarted)]) == 3
    assert isinstance(eventos[-1], TurnCompleted)


def test_a_provider_failure_is_reported_and_stops_the_turn(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    eventos = list(_orquestador(FailingProvider(), reg, auditoria).submit("hola"))

    fallo = next(e for e in eventos if isinstance(e, ProviderFailed))
    assert "conexión" in fallo.message
    assert not any(isinstance(e, TurnCompleted) for e in eventos)


# --------------------------------------------------------------------------- #
# Historial
# --------------------------------------------------------------------------- #


def test_the_conversation_is_remembered_across_turns(
    reg: ToolRegistry, auditoria: AuditLog
) -> None:
    provider = FakeProvider([_texto("Encantado."), _texto("Te llamas Ana.")])
    orq = _orquestador(provider, reg, auditoria)

    list(orq.submit("me llamo Ana"))
    list(orq.submit("¿cómo me llamo?"))

    historial = provider.llamadas[1]["messages"]
    assert any("me llamo Ana" in m.text for m in historial)


def test_reset_clears_the_conversation(reg: ToolRegistry, auditoria: AuditLog) -> None:
    provider = FakeProvider([_texto("Encantado."), _texto("No lo sé.")])
    orq = _orquestador(provider, reg, auditoria)

    list(orq.submit("me llamo Ana"))
    orq.reset()
    list(orq.submit("¿cómo me llamo?"))

    historial = provider.llamadas[1]["messages"]
    assert len(historial) == 1
    assert not any("Ana" in m.text for m in historial)
