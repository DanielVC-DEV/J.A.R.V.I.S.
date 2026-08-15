"""Representación de los eventos del núcleo en la interfaz gráfica.

Funciones puras, sin dependencia de Qt: reciben un evento y devuelven el HTML
con el que mostrarlo. Esa separación permite comprobar por completo la parte
que concentra los errores —el formato, el escapado, los textos— sin necesidad
de un entorno gráfico.

El escapado no es un detalle. El historial se muestra como HTML, y el texto
que llega procede del usuario, del modelo y de páginas web: si no se escapa,
un archivo llamado ``<b>informe`` desbarataría el formato de la conversación.
"""

from __future__ import annotations

from html import escape

from jarvis.core.events import (
    AssistantMessage,
    Event,
    IterationLimitReached,
    ProviderFailed,
    ToolDenied,
    ToolExecuted,
    ToolRequested,
    TurnCompleted,
)

__all__ = [
    "PALETTE",
    "format_audit_entry",
    "format_event",
    "format_user_message",
    "format_voice_error",
    "summarise_turn",
]

#: Colores de la conversación. Se definen aquí y no repartidos por la ventana
#: para poder cambiar el aspecto en un solo sitio.
PALETTE = {
    "user": "#4da3ff",
    "assistant": "#e8e8e8",
    "tool": "#4fc3c3",
    "warning": "#e0b060",
    "error": "#e07070",
    "muted": "#808080",
}


def _bloque(texto: str, color: str, *, sangria: int = 0, cursiva: bool = False) -> str:
    """Envuelve un texto ya escapado en un párrafo con estilo."""
    estilo = f"color:{color};margin:2px 0;"
    if sangria:
        estilo += f"margin-left:{sangria}px;"
    if cursiva:
        estilo += "font-style:italic;"
    return f'<p style="{estilo}">{texto}</p>'


def format_user_message(text: str) -> str:
    """Da formato a lo que acaba de decir el usuario."""
    return _bloque(f"<b>tú</b> &nbsp;{escape(text)}", PALETTE["user"])


def format_audit_entry(entry: dict) -> str:
    """Da formato a una línea del diario de auditoría (ver `security/audit.py`)."""
    color = PALETTE["assistant"]
    if entry.get("decision") == "deny" or entry.get("succeeded") is False:
        color = PALETTE["error"]
    elif entry.get("decision") == "confirm":
        color = PALETTE["warning"]

    marca = escape(str(entry.get("timestamp", "")))
    herramienta = escape(str(entry.get("tool", "")))
    estado = "ejecutada" if entry.get("executed") else "no ejecutada"
    razon = escape(str(entry.get("reason", "")))

    return _bloque(
        f'<span style="color:{PALETTE["muted"]}">{marca}</span> '
        f"<b>{herramienta}</b> · {escape(str(entry.get('decision', '')))} · {estado}"
        f'<br><span style="color:{PALETTE["muted"]}">{razon}</span>',
        color,
    )


def format_voice_error(message: str) -> str:
    """Da formato a un fallo de captura o transcripción del botón de micrófono."""
    return _bloque(f"✗ voz: {escape(message)}", PALETTE["error"])


def _formatear_argumentos(arguments: dict) -> str:
    """Redacta los argumentos de una herramienta de forma compacta.

    Los valores largos se recortan: un ``write_file`` con dos páginas de
    contenido llenaría la ventana con algo que el usuario no necesita ver.
    """
    partes = []
    for clave, valor in arguments.items():
        texto = str(valor)
        if len(texto) > 60:
            texto = f"{texto[:60]}…"
        partes.append(f"{escape(clave)}={escape(texto)}")
    return ", ".join(partes)


def format_event(event: Event, *, verbose: bool = False) -> str:
    """Convierte un evento del núcleo en HTML.

    Args:
        event: Evento emitido por el núcleo.
        verbose: Si se muestran también los detalles internos.

    Returns:
        El HTML con el que añadirlo al historial, o una cadena vacía si el
        evento no debe mostrarse.
    """
    if isinstance(event, AssistantMessage):
        return _bloque(escape(event.text), PALETTE["assistant"])

    if isinstance(event, ToolRequested):
        argumentos = _formatear_argumentos(event.arguments)
        return _bloque(
            f"→ <b>{escape(event.name)}</b>"
            f'<span style="color:{PALETTE["muted"]}">({argumentos})</span>',
            PALETTE["tool"],
            sangria=12,
        )

    if isinstance(event, ToolExecuted):
        if not event.succeeded:
            return _bloque(
                f"⚠ {escape(event.content)}", PALETTE["warning"], sangria=24
            )
        if verbose:
            return _bloque(
                f"✓ {escape(_primera_linea(event.content))} "
                f"({event.duration_ms:.0f} ms)",
                PALETTE["muted"],
                sangria=24,
            )
        return ""

    if isinstance(event, ToolDenied):
        origen = "rechazado por ti" if event.by_user else "impedido"
        return _bloque(
            f"✗ {origen}: {escape(event.reason)}", PALETTE["error"], sangria=24
        )

    if isinstance(event, IterationLimitReached):
        return _bloque(
            f"Se alcanzó el límite de {event.limit} acciones en un turno.",
            PALETTE["warning"],
        )

    if isinstance(event, ProviderFailed):
        return _bloque(escape(event.message), PALETTE["error"])

    if isinstance(event, TurnCompleted) and verbose:
        return _bloque(summarise_turn(event), PALETTE["muted"], cursiva=True)

    return ""


def _primera_linea(texto: str) -> str:
    """Devuelve la primera línea de un texto, para los resultados extensos."""
    lineas = texto.strip().splitlines()
    if not lineas:
        return ""
    return lineas[0] + ("…" if len(lineas) > 1 else "")


def summarise_turn(event: TurnCompleted) -> str:
    """Redacta el resumen de coste de un turno.

    Se muestra porque el presupuesto de tokens de una capa gratuita es un
    recurso escaso: tenerlo a la vista permite notar cuándo una orden sale
    cara antes de chocar con el límite.
    """
    vueltas = "vuelta" if event.iterations == 1 else "vueltas"
    return (
        f"{event.iterations} {vueltas} · "
        f"{event.input_tokens + event.output_tokens} tokens"
    )
