"""Cliente de consola.

Primer cliente del núcleo y el más sencillo: traduce los eventos a texto y las
peticiones de confirmación a una pregunta en el terminal.

Su valor no es solo servir durante el desarrollo. Demuestra que el núcleo es
verdaderamente independiente de su interfaz: la ventana gráfica de la Fase 7
consumirá exactamente los mismos eventos y no exigirá tocar el orquestador.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from jarvis.ai.factory import create_provider
from jarvis.config.paths import audit_file, ensure_directories
from jarvis.config.settings import ConfigurationError, load_settings
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
from jarvis.core.orchestrator import ConfirmationRequest, Orchestrator
from jarvis.core.registry import registry
from jarvis.security.guard import Guard

__all__ = ["main"]

console = Console()

#: Órdenes que atiende la propia consola sin consultar al modelo.
COMMANDS = {
    "/salir": "termina la sesión",
    "/nueva": "olvida la conversación y empieza de cero",
    "/tools": "enumera las herramientas disponibles",
    "/log": "muestra dónde se guarda el registro de auditoría",
    "/ayuda": "muestra esta lista",
}


def _render(event: Event, *, verbose: bool) -> None:
    """Escribe un evento en el terminal.

    Args:
        event: Evento emitido por el núcleo.
        verbose: Si es verdadero, se muestran también los detalles internos
            —resultados en bruto, consumo de tokens— útiles al depurar.
    """
    if isinstance(event, ThinkingStarted):
        if verbose and event.iteration > 1:
            console.print(f"[dim]· vuelta {event.iteration}[/dim]")

    elif isinstance(event, AssistantMessage):
        console.print(Text(event.text, style="bright_white"))

    elif isinstance(event, ToolRequested):
        argumentos = ", ".join(f"{k}={v!r}" for k, v in event.arguments.items())
        console.print(f"[cyan]→ {event.name}[/cyan][dim]({argumentos})[/dim]")

    elif isinstance(event, ToolExecuted):
        if not event.succeeded:
            console.print(f"[yellow]  ⚠ {event.content}[/yellow]")
        elif verbose:
            console.print(f"[dim]  ✓ {event.content} ({event.duration_ms:.0f} ms)[/dim]")

    elif isinstance(event, ToolDenied):
        origen = "rechazado por ti" if event.by_user else "impedido"
        console.print(f"[red]  ✗ {origen}: {event.reason}[/red]")

    elif isinstance(event, IterationLimitReached):
        console.print(
            f"[yellow]Se alcanzó el límite de {event.limit} acciones en un turno.[/yellow]"
        )

    elif isinstance(event, ProviderFailed):
        console.print(f"[red]{event.message}[/red]")

    elif isinstance(event, TurnCompleted) and verbose:
        console.print(
            f"[dim]· {event.iterations} vuelta(s), "
            f"{event.input_tokens}+{event.output_tokens} tokens[/dim]"
        )


def _confirm(request: ConfirmationRequest) -> bool:
    """Pregunta al usuario si autoriza una acción delicada."""
    argumentos = ", ".join(f"{k}={v!r}" for k, v in request.arguments.items())
    console.print(
        Panel(
            f"{request.reason}\n\n[bold]{request.tool}[/bold]({argumentos})",
            title="Confirmación necesaria",
            border_style="yellow",
        )
    )
    try:
        respuesta = console.input("[yellow]¿Autorizas esta acción? (s/N) [/yellow]")
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return respuesta.strip().lower() in {"s", "si", "sí", "y", "yes"}


def _handle_command(orden: str, orchestrator: Orchestrator) -> bool:
    """Atiende una orden propia de la consola.

    Args:
        orden: Texto introducido, que empieza por una barra.
        orchestrator: Orquestador de la sesión.

    Returns:
        ``True`` si la sesión debe continuar; ``False`` si hay que terminar.
    """
    comando = orden.strip().lower()

    if comando in {"/salir", "/exit", "/quit"}:
        return False

    if comando == "/nueva":
        orchestrator.reset()
        console.print("[dim]Conversación reiniciada.[/dim]")

    elif comando == "/tools":
        for spec in registry.all():
            console.print(
                f"  [cyan]{spec.name}[/cyan] [dim]({spec.category}, "
                f"riesgo {spec.risk})[/dim]\n    {spec.description}"
            )

    elif comando == "/log":
        console.print(f"[dim]{audit_file()}[/dim]")

    else:
        for nombre, descripcion in COMMANDS.items():
            console.print(f"  [cyan]{nombre}[/cyan] — {descripcion}")

    return True


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la consola.

    Args:
        argv: Argumentos de línea de órdenes. Se admite ``--verbose`` para ver
            los detalles internos de cada turno.

    Returns:
        El código de salida del proceso.
    """
    verbose = "--verbose" in (argv if argv is not None else sys.argv[1:])

    # El propio acto de importar el paquete registra las herramientas en el
    # catálogo global, gracias al decorador ``@tool``.
    import jarvis.tools  # noqa: F401

    try:
        settings = load_settings()
        provider = create_provider(settings)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except Exception as exc:  # noqa: BLE001 - frontera hacia el usuario
        console.print(f"[red]No se pudo iniciar el asistente: {exc}[/red]")
        return 1

    ensure_directories()

    orchestrator = Orchestrator(
        provider=provider,
        registry=registry,
        guard=Guard.with_default_policies(),
        confirmer=_confirm,
        max_iterations=settings.max_tool_iterations,
        categories=settings.enabled_categories(),
    )

    console.print(
        Panel(
            f"[bold]JARVIS[/bold] listo. {len(registry)} herramientas disponibles.\n"
            f"[dim]{settings.provider} · {settings.resolved_model()}\n"
            "Escribe /ayuda para ver las órdenes de la consola.[/dim]",
            border_style="blue",
        )
    )

    while True:
        try:
            entrada = console.input("\n[bold blue]tú[/bold blue] ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        entrada = entrada.strip()
        if not entrada:
            continue

        if entrada.startswith("/"):
            if not _handle_command(entrada, orchestrator):
                break
            continue

        console.print()
        for evento in orchestrator.submit(entrada):
            _render(evento, verbose=verbose)

    console.print("[dim]Hasta luego.[/dim]")
    return 0
