"""Cliente de voz: el asistente escuchando y hablando.

Es el segundo cliente del núcleo, y demuestra que la arquitectura cumple lo
que prometía: consume exactamente los mismos eventos que la consola y no toca
ni una línea del orquestador.

El bucle mantiene **un único flujo de audio abierto** y reparte cada bloque
entre la palabra de activación y la grabación. Abrir dos flujos sobre el mismo
micrófono —uno para vigilar y otro para grabar— falla en Windows o entrega
audio cortado, así que la exclusividad no es una optimización sino un
requisito.

Ciclo de una orden::

    escuchar ──► activación (voz o tecla) ──► grabar ──► transcribir
                                                            │
    hablar ◄── respuesta ◄── núcleo ◄── herramientas ◄───────┘
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from rich.console import Console
from rich.panel import Panel

from jarvis.core.events import (
    AssistantMessage,
    Event,
    ProviderFailed,
    ToolDenied,
    ToolExecuted,
    ToolRequested,
    TurnCompleted,
)
from jarvis.core.orchestrator import Orchestrator
from jarvis.voice.audio import AudioClip
from jarvis.voice.hotkey import PushToTalk
from jarvis.voice.recorder import Microphone
from jarvis.voice.tts import Speaker
from jarvis.voice.transcriber import Transcriber, TranscriptionError
from jarvis.voice.vad import UtteranceDetector, UtteranceState
from jarvis.voice.wake_word import WakeWordDetector

__all__ = ["VoiceSession"]

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VoiceSession:
    """Sesión de voz: escucha, entiende, actúa y responde."""

    orchestrator: Orchestrator
    microphone: Microphone
    transcriber: Transcriber
    speaker: Speaker
    utterance: UtteranceDetector
    console: Console = field(default_factory=Console)

    hotkey: PushToTalk | None = None
    """Atajo de teclado. ``None`` si se prefiere solo la palabra de activación."""

    wake_word: WakeWordDetector | None = None
    """Detector de la palabra de activación. ``None`` si solo se usa el atajo."""

    verbose: bool = False

    # -- Presentación ------------------------------------------------------- #

    def _render(self, event: Event) -> None:
        """Escribe un evento del núcleo en el terminal."""
        if isinstance(event, AssistantMessage):
            self.console.print(f"[bright_white]{event.text}[/bright_white]")

        elif isinstance(event, ToolRequested):
            argumentos = ", ".join(f"{k}={v!r}" for k, v in event.arguments.items())
            self.console.print(f"[cyan]→ {event.name}[/cyan][dim]({argumentos})[/dim]")

        elif isinstance(event, ToolExecuted):
            if not event.succeeded:
                self.console.print(f"[yellow]  ⚠ {event.content}[/yellow]")
            elif self.verbose:
                self.console.print(f"[dim]  ✓ {event.content}[/dim]")

        elif isinstance(event, ToolDenied):
            origen = "rechazado por ti" if event.by_user else "impedido"
            self.console.print(f"[red]  ✗ {origen}: {event.reason}[/red]")

        elif isinstance(event, ProviderFailed):
            self.console.print(f"[red]{event.message}[/red]")

        elif isinstance(event, TurnCompleted) and self.verbose:
            self.console.print(
                f"[dim]· {event.iterations} vuelta(s), "
                f"{event.input_tokens}+{event.output_tokens} tokens[/dim]"
            )

    # -- Un turno completo -------------------------------------------------- #

    def _handle(self, clip: AudioClip) -> None:
        """Transcribe una intervención, la procesa y responde en voz alta."""
        if clip.is_empty:
            return

        try:
            transcripcion = self.transcriber.transcribe(clip)
        except TranscriptionError as exc:
            self.console.print(f"[red]{exc}[/red]")
            return

        if transcripcion.is_empty:
            self.console.print("[dim]· no se entendió nada[/dim]")
            return

        self.console.print(f"\n[bold blue]tú[/bold blue] {transcripcion.text}\n")

        respuesta = ""
        for evento in self.orchestrator.submit(transcripcion.text):
            self._render(evento)
            if isinstance(evento, TurnCompleted):
                respuesta = evento.text

        if respuesta:
            self.speaker.speak(respuesta)

    # -- Bucle principal ---------------------------------------------------- #

    def _capture_after_wake_word(self, stream: object) -> AudioClip:
        """Graba una intervención tras oír la palabra de activación.

        Se reutiliza el flujo ya abierto en lugar de abrir otro, y el final se
        deduce con el detector de intervenciones: aquí el usuario no indica
        cuándo termina.
        """
        self.utterance.reset()

        while True:
            estado = self.utterance.push(self.microphone.read(stream))
            if estado is UtteranceState.FINISHED:
                return self.utterance.clip
            if estado is UtteranceState.TIMED_OUT:
                self.console.print("[dim]· no oí nada[/dim]")
                return AudioClip(np.zeros(0, dtype=np.int16))

    def _capture_while_held(self, stream: object) -> AudioClip:
        """Graba mientras la tecla siga pulsada.

        Aquí no interviene la detección de voz: el usuario señala el principio
        y el final, de modo que no hay nada que deducir.
        """
        assert self.hotkey is not None
        bloques: list[np.ndarray] = []
        maximo = int(60_000 / self.microphone.frame_ms)

        while self.hotkey.is_held and len(bloques) < maximo:
            bloques.append(self.microphone.read(stream))

        return AudioClip.from_frames(bloques, self.microphone.sample_rate)

    def _announce(self) -> None:
        """Muestra cómo se le habla al asistente."""
        modos = []
        if self.wake_word is not None:
            modos.append('di [bold]"Jarvis"[/bold]')
        if self.hotkey is not None:
            modos.append(f"mantén [bold]{self.hotkey.key.upper()}[/bold]")

        self.console.print(
            Panel(
                f"[bold]JARVIS[/bold] escuchando.\n"
                f"Para hablarle: {' o '.join(modos)}.\n"
                "[dim]Ctrl+C para terminar.[/dim]",
                border_style="blue",
            )
        )

    def run(self) -> int:
        """Ejecuta la sesión hasta que el usuario la interrumpa.

        Returns:
            El código de salida del proceso.
        """
        if self.hotkey is None and self.wake_word is None:
            self.console.print(
                "[red]No hay ninguna forma de activar el asistente. Activa la "
                "palabra de activación o define un atajo de teclado.[/red]"
            )
            return 1

        self.console.print("[dim]Midiendo el ruido de fondo, guarda silencio…[/dim]")
        umbral = self.microphone.calibrate(self.utterance)
        if self.verbose:
            self.console.print(f"[dim]· umbral de voz: {umbral:.4f}[/dim]")

        if self.hotkey is not None:
            self.hotkey.start()

        self._announce()

        try:
            with self.microphone.open_stream() as flujo:
                while True:
                    bloque = self.microphone.read(flujo)

                    if self.hotkey is not None and self.hotkey.is_held:
                        self.console.print("[cyan]● grabando…[/cyan]")
                        # La respuesta anterior puede seguir sonando: callarla
                        # evita que el asistente se oiga a sí mismo.
                        self.speaker.stop()
                        self._handle(self._capture_while_held(flujo))
                        self._reset_detectors()
                        continue

                    if self.wake_word is not None and self.wake_word.push(bloque):
                        self.console.print("[cyan]● te escucho…[/cyan]")
                        self.speaker.stop()
                        self._handle(self._capture_after_wake_word(flujo))
                        self._reset_detectors()

        except KeyboardInterrupt:
            self.console.print()
        finally:
            if self.hotkey is not None:
                self.hotkey.stop()
            self.speaker.stop()

        self.console.print("[dim]Hasta luego.[/dim]")
        return 0

    def _reset_detectors(self) -> None:
        """Descarta el audio acumulado durante el turno anterior.

        Sin esto, la propia respuesta del asistente y los restos de la orden
        quedarían en las reservas de los detectores y podrían provocar una
        activación fantasma inmediata.
        """
        self.utterance.reset()
        if self.wake_word is not None:
            self.wake_word.reset()
