"""Pruebas de la representación de eventos en la interfaz gráfica.

Son funciones puras que no dependen de Qt, de modo que la parte donde se
concentran los errores —el formato y, sobre todo, el escapado— puede
comprobarse sin entorno gráfico.
"""

from __future__ import annotations

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
from jarvis.ui.gui.rendering import (
    format_audit_entry,
    format_event,
    format_user_message,
    summarise_turn,
)


# --------------------------------------------------------------------------- #
# Escapado
# --------------------------------------------------------------------------- #


def test_user_text_is_escaped() -> None:
    """Un archivo llamado «<b>informe» desbarataría el historial."""
    html = format_user_message("abre <b>informe</b>.txt")

    assert "&lt;b&gt;" in html
    assert "<b>informe" not in html


def test_the_assistant_text_is_escaped() -> None:
    html = format_event(AssistantMessage(text="Encontré <script>alerta()</script>"))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_tool_arguments_are_escaped() -> None:
    html = format_event(
        ToolRequested(name="write_file", arguments={"path": "<peligro>.txt"})
    )
    assert "&lt;peligro&gt;" in html


def test_error_messages_are_escaped() -> None:
    """Los errores citan rutas y textos que el usuario no controló."""
    html = format_event(ProviderFailed(message="falló <algo> raro"))
    assert "&lt;algo&gt;" in html


# --------------------------------------------------------------------------- #
# Contenido
# --------------------------------------------------------------------------- #


def test_the_user_message_is_labelled() -> None:
    html = format_user_message("sube el volumen")
    assert "tú" in html
    assert "sube el volumen" in html


def test_a_tool_call_shows_its_arguments() -> None:
    html = format_event(ToolRequested(name="set_volume", arguments={"level": 70}))
    assert "set_volume" in html
    assert "level=70" in html


def test_long_arguments_are_shortened() -> None:
    """Un write_file con dos páginas de texto llenaría la ventana."""
    html = format_event(
        ToolRequested(name="write_file", arguments={"content": "x" * 500})
    )
    assert len(html) < 300
    assert "…" in html


def test_a_failed_tool_is_always_shown() -> None:
    html = format_event(
        ToolExecuted(name="set_volume", succeeded=False, content="no hay dispositivo")
    )
    assert "no hay dispositivo" in html


# --------------------------------------------------------------------------- #
# Diario de auditoría
# --------------------------------------------------------------------------- #


def test_an_audit_entry_shows_the_tool_and_reason() -> None:
    html = format_audit_entry(
        {"tool": "delete_file", "decision": "deny", "reason": "fuera de la jaula"}
    )
    assert "delete_file" in html
    assert "fuera de la jaula" in html


def test_audit_entry_text_is_escaped() -> None:
    """La razón puede citar una ruta escrita por el modelo, no de fiar."""
    html = format_audit_entry({"tool": "x", "reason": "<script>alerta()</script>"})
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_a_successful_tool_is_quiet_unless_verbose() -> None:
    """El detalle de lo que salió bien solo estorba en el uso normal."""
    evento = ToolExecuted(name="set_volume", succeeded=True, content="Volumen al 70%.")

    assert format_event(evento) == ""
    assert "Volumen al 70%." in format_event(evento, verbose=True)


def test_only_the_first_line_of_a_long_result_is_shown() -> None:
    largo = "Sistema operativo: Windows 11\nCPU: 12%\nMemoria: 14 GB"
    html = format_event(
        ToolExecuted(name="get_system_info", succeeded=True, content=largo),
        verbose=True,
    )
    assert "Windows 11" in html
    assert "Memoria" not in html


def test_a_denial_distinguishes_who_refused() -> None:
    por_usuario = format_event(
        ToolDenied(name="delete_file", reason="no autorizado", by_user=True)
    )
    por_politica = format_event(
        ToolDenied(name="delete_file", reason="fuera de la jaula", by_user=False)
    )

    assert "rechazado por ti" in por_usuario
    assert "impedido" in por_politica


def test_the_iteration_limit_is_announced() -> None:
    assert "8 acciones" in format_event(IterationLimitReached(limit=8))


# --------------------------------------------------------------------------- #
# Resumen del turno
# --------------------------------------------------------------------------- #


def test_the_turn_summary_reports_cost() -> None:
    """El presupuesto de tokens es escaso: tenerlo a la vista ayuda."""
    resumen = summarise_turn(
        TurnCompleted(text="Listo.", iterations=2, input_tokens=1500, output_tokens=40)
    )
    assert "2 vueltas" in resumen
    assert "1540 tokens" in resumen


def test_a_single_iteration_is_singular() -> None:
    resumen = summarise_turn(
        TurnCompleted(text="Listo.", iterations=1, input_tokens=100, output_tokens=10)
    )
    assert "1 vuelta ·" in resumen


def test_the_summary_is_hidden_unless_verbose() -> None:
    evento = TurnCompleted(text="Listo.", iterations=1)
    assert format_event(evento) == ""
    assert format_event(evento, verbose=True) != ""


# --------------------------------------------------------------------------- #
# Eventos sin representación
# --------------------------------------------------------------------------- #


def test_thinking_is_not_added_to_the_history() -> None:
    """Se muestra en la barra de estado, no como una línea más."""
    assert format_event(ThinkingStarted(iteration=1)) == ""


def test_every_event_produces_valid_markup() -> None:
    """Un HTML sin cerrar arrastraría su formato al resto del historial."""
    eventos = [
        AssistantMessage(text="hola"),
        ToolRequested(name="x", arguments={"a": 1}),
        ToolExecuted(name="x", succeeded=False, content="mal"),
        ToolDenied(name="x", reason="no"),
        IterationLimitReached(limit=8),
        ProviderFailed(message="error"),
    ]

    for evento in eventos:
        html = format_event(evento, verbose=True)
        assert html.count("<p") == html.count("</p>")
        assert html.startswith("<p")
        assert html.endswith("</p>")
