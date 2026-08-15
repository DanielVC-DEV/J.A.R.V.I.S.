"""Arranque de la sesión de voz.

Reúne las piezas —configuración, modelo, herramientas, micrófono,
transcripción y voz— y cede el control al bucle. Se mantiene aparte de
``voice_loop`` para que ese módulo contenga solo la lógica de la sesión y
pueda probarse sin construir el sistema entero.
"""

from __future__ import annotations

import sys

from rich.console import Console

from jarvis.ai.factory import create_provider
from jarvis.config.paths import ensure_directories
from jarvis.config.settings import ConfigurationError, load_settings
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.registry import registry
from jarvis.security.guard import Guard
from jarvis.tools.memory_tools import manager as memory_manager
from jarvis.ui.cli import _confirm
from jarvis.ui.voice_loop import VoiceSession
from jarvis.voice.factory import create_transcriber
from jarvis.voice.hotkey import PushToTalk
from jarvis.voice.recorder import Microphone, MicrophoneError
from jarvis.voice.tts import EdgeSpeaker
from jarvis.voice.vad import UtteranceDetector
from jarvis.voice.wake_word import WakeWordDetector, WakeWordError

__all__ = ["main"]

console = Console()


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del modo de voz.

    Args:
        argv: Argumentos de línea de órdenes. Se admite ``--verbose``.

    Returns:
        El código de salida del proceso.
    """
    argumentos = argv if argv is not None else sys.argv[1:]
    verbose = "--verbose" in argumentos

    import jarvis.tools  # noqa: F401  # el import registra las herramientas

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

    # La transcripción y el micrófono son imprescindibles: sin ellos no hay
    # modo de voz posible.
    try:
        transcriptor = create_transcriber(settings)
        microfono = Microphone(device=settings.mic_device)
    except (ConfigurationError, MicrophoneError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    # La palabra de activación sí es prescindible: si su modelo no carga, el
    # asistente sigue siendo utilizable con el atajo de teclado.
    detector_activacion = None
    if settings.wake_word_enabled:
        try:
            detector_activacion = WakeWordDetector(
                wake_word=settings.wake_word,
                threshold=settings.wake_word_threshold,
            )
        except WakeWordError as exc:
            console.print(f"[yellow]Sin palabra de activación: {exc}[/yellow]")

    sesion = VoiceSession(
        orchestrator=Orchestrator(
            provider=provider,
            registry=registry,
            guard=Guard.with_default_policies(settings.resolved_allowed_paths()),
            confirmer=_confirm,
            max_iterations=settings.max_tool_iterations,
            categories=settings.enabled_categories(),
            context_provider=memory_manager().context_for,
        ),
        microphone=microfono,
        transcriber=transcriptor,
        speaker=EdgeSpeaker(settings),
        utterance=UtteranceDetector(frame_ms=microfono.frame_ms),
        hotkey=PushToTalk(key=settings.hotkey),
        wake_word=detector_activacion,
        console=console,
        verbose=verbose,
    )

    try:
        return sesion.run()
    except MicrophoneError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
