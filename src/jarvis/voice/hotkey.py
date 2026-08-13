"""Activación por atajo de teclado.

Alternativa a la palabra de activación, y su complemento: hay situaciones —una
sala compartida, una llamada en curso, un micrófono con ruido— en las que
tener el asistente escuchando de continuo no es aceptable. Con el atajo, el
usuario decide exactamente cuándo se le está oyendo.

Funciona manteniendo pulsada una tecla: se graba mientras está abajo y se
procesa al soltarla. Ese gesto evita por completo la cuestión de decidir
cuándo terminó la intervención, porque lo indica el propio usuario.

La escucha del teclado es global —funciona aunque la ventana del asistente no
tenga el foco— y corre en un hilo aparte, de modo que no bloquea nada.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DEFAULT_HOTKEY", "HotkeyError", "PushToTalk", "parse_key"]

_logger = logging.getLogger(__name__)

#: Tecla por omisión. Se elige una de función porque no interfiere con la
#: escritura normal y está libre en la mayoría de las aplicaciones.
DEFAULT_HOTKEY = "f9"


class HotkeyError(RuntimeError):
    """No se pudo registrar el atajo de teclado."""


def parse_key(name: str) -> tuple[str, str]:
    """Interpreta el nombre de una tecla escrito por el usuario.

    Acepta tanto teclas especiales —``f9``, ``ctrl``, ``space``— como
    caracteres sueltos, sin distinguir mayúsculas ni exigir un prefijo.

    Args:
        name: Nombre tal como aparece en la configuración.

    Returns:
        Una tupla ``(clase, valor)`` donde la clase es ``special`` para las
        teclas con nombre propio y ``char`` para los caracteres corrientes.

    Raises:
        HotkeyError: Si el nombre está vacío o no puede interpretarse.
    """
    limpio = name.strip().lower().removeprefix("key.")

    if not limpio:
        raise HotkeyError("El atajo de teclado no puede estar vacío.")

    if len(limpio) == 1:
        return ("char", limpio)

    return ("special", limpio)


@dataclass(slots=True)
class PushToTalk:
    """Escucha global de una tecla para grabar mientras se mantiene pulsada.

    Args:
        key: Nombre de la tecla, tal como lo interpreta ``parse_key``.
        on_press: Se invoca cuando la tecla baja.
        on_release: Se invoca cuando la tecla sube.
    """

    key: str = DEFAULT_HOTKEY
    on_press: Callable[[], None] = lambda: None
    on_release: Callable[[], None] = lambda: None

    _listener: Any = None
    _held: threading.Event = field(default_factory=threading.Event)

    @property
    def is_held(self) -> bool:
        """Indica si la tecla está pulsada en este momento.

        Es lo que consulta el grabador para saber si debe seguir capturando.
        """
        return self._held.is_set()

    # -- Comparación de teclas ---------------------------------------------- #

    def _matches(self, pressed: Any) -> bool:
        """Comprueba si la tecla recibida es la configurada.

        La biblioteca entrega objetos distintos según el tipo de tecla —las
        especiales tienen ``name`` y los caracteres corrientes ``char``—, de
        modo que hay que contemplar ambos casos.
        """
        clase, valor = parse_key(self.key)

        if clase == "special":
            return str(getattr(pressed, "name", "")).lower() == valor

        caracter = getattr(pressed, "char", None)
        return caracter is not None and caracter.lower() == valor

    # -- Ciclo de vida ------------------------------------------------------ #

    def start(self) -> None:
        """Comienza a escuchar el teclado en segundo plano.

        Raises:
            HotkeyError: Si falta la biblioteca o el sistema no lo permite.
        """
        if self._listener is not None:
            return

        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyError(
                "Falta el paquete «pynput». Instala el proyecto con "
                '«pip install -e ".[dev]"».'
            ) from exc

        parse_key(self.key)  # valida antes de registrar nada

        def pulsada(tecla: Any) -> None:
            # El sistema repite el evento mientras la tecla sigue abajo; solo
            # interesa la primera vez.
            if self._matches(tecla) and not self._held.is_set():
                self._held.set()
                self._invoke(self.on_press)

        def soltada(tecla: Any) -> None:
            if self._matches(tecla) and self._held.is_set():
                self._held.clear()
                self._invoke(self.on_release)

        try:
            self._listener = keyboard.Listener(on_press=pulsada, on_release=soltada)
            self._listener.start()
        except Exception as exc:  # noqa: BLE001 - depende del sistema
            raise HotkeyError(
                f"No se pudo registrar el atajo «{self.key}»: {exc}"
            ) from exc

    def stop(self) -> None:
        """Deja de escuchar el teclado."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._held.clear()

    def __enter__(self) -> PushToTalk:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    @staticmethod
    def _invoke(callback: Callable[[], None]) -> None:
        """Ejecuta un aviso sin dejar que su fallo detenga la escucha.

        Los avisos corren en el hilo del oyente de teclado: una excepción sin
        capturar dejaría al asistente sordo al atajo durante el resto de la
        sesión, y de forma silenciosa.
        """
        try:
            callback()
        except Exception:  # noqa: BLE001 - frontera con código ajeno
            _logger.exception("Fallo al atender el atajo de teclado.")
