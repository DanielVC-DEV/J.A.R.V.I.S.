"""Control del teclado y el ratón.

Permite al asistente escribir texto, pulsar combinaciones de teclas y manejar
el puntero.

Escribir texto en Windows tiene una trampa que conviene conocer: **simular
pulsaciones de tecla no reproduce fielmente los acentos ni la eñe**, porque el
resultado depende de la distribución de teclado activa. Un texto en español
escrito así llega mutilado. Por eso el módulo escribe a través del
portapapeles, que transporta el texto tal cual, y **restaura después el
contenido anterior** para no perder lo que el usuario tuviera copiado.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from typing import Any

__all__ = [
    "InputError",
    "Position",
    "click",
    "get_screen_size",
    "move_mouse",
    "press_keys",
    "scroll",
    "type_text",
]

_logger = logging.getLogger(__name__)

#: Pausa tras poner el texto en el portapapeles. Sin ella, algunas
#: aplicaciones pegan el contenido anterior porque leen antes de que el
#: sistema haya terminado de actualizarlo.
_CLIPBOARD_SETTLE = 0.12

#: Longitud máxima de un texto que se escribe de una vez. Evita que un error
#: del modelo vuelque miles de caracteres en una ventana.
MAX_TEXT_LENGTH = 5_000

#: Equivalencias entre los nombres que el modelo usará y los que espera la
#: biblioteca de automatización.
_KEY_ALIASES = {
    "control": "ctrl",
    "mayus": "shift",
    "mayúsculas": "shift",
    "intro": "enter",
    "entrar": "enter",
    "retorno": "enter",
    "espacio": "space",
    "tabulador": "tab",
    "escape": "esc",
    "suprimir": "delete",
    "borrar": "backspace",
    "arriba": "up",
    "abajo": "down",
    "izquierda": "left",
    "derecha": "right",
    "inicio": "home",
    "fin": "end",
    "windows": "win",
}


class InputError(RuntimeError):
    """No se pudo enviar la entrada al sistema."""


@dataclass(frozen=True, slots=True)
class Position:
    """Una posición en la pantalla."""

    x: int
    y: int


def _pyautogui() -> Any:
    """Importa la biblioteca de automatización con un mensaje útil si falta."""
    try:
        import pyautogui
    except ImportError as exc:
        raise InputError(
            "Falta el paquete «pyautogui». Instala el proyecto con "
            '«pip install -e .».'
        ) from exc
    except Exception as exc:  # noqa: BLE001 - falla sin escritorio disponible
        raise InputError(f"No se pudo acceder al escritorio: {exc}") from exc

    # La pausa que la biblioteca inserta tras cada acción hace que escribir
    # resulte lento sin aportar fiabilidad en las operaciones que se usan aquí.
    pyautogui.PAUSE = 0.02
    # El mecanismo de emergencia mueve el ratón a una esquina para abortar. Se
    # mantiene activo a propósito: es la forma que tiene el usuario de
    # interrumpir una automatización descontrolada.
    pyautogui.FAILSAFE = True
    return pyautogui


def get_screen_size() -> tuple[int, int]:
    """Devuelve el tamaño de la pantalla en píxeles.

    Returns:
        Una tupla ``(ancho, alto)``.
    """
    ancho, alto = _pyautogui().size()
    return int(ancho), int(alto)


# --------------------------------------------------------------------------- #
# Teclado
# --------------------------------------------------------------------------- #


def _clipboard() -> Any:
    """Importa el acceso al portapapeles."""
    if sys.platform != "win32":
        raise InputError("La escritura de texto solo está disponible en Windows.")

    try:
        import pyperclip
    except ImportError as exc:
        raise InputError(
            "Falta el paquete «pyperclip», necesario para escribir texto con "
            'acentos. Instala el proyecto con «pip install -e .».'
        ) from exc
    return pyperclip


def type_text(text: str) -> int:
    """Escribe un texto en la ventana que tenga el foco.

    El texto viaja por el portapapeles y no como pulsaciones simuladas, porque
    estas no reproducen los acentos ni la eñe de forma fiable. El contenido
    anterior del portapapeles se restaura al terminar.

    Args:
        text: Texto a escribir.

    Returns:
        El número de caracteres escritos.

    Raises:
        InputError: Si el texto excede el límite o el sistema no responde.
    """
    if not text:
        return 0

    if len(text) > MAX_TEXT_LENGTH:
        raise InputError(
            f"El texto tiene {len(text)} caracteres y el límite son "
            f"{MAX_TEXT_LENGTH}. Divídelo en partes."
        )

    pyperclip = _clipboard()
    pyautogui = _pyautogui()

    try:
        anterior = pyperclip.paste()
    except Exception:  # noqa: BLE001 - puede estar vacío o contener otro formato
        anterior = None

    try:
        pyperclip.copy(text)
        time.sleep(_CLIPBOARD_SETTLE)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(_CLIPBOARD_SETTLE)
    except Exception as exc:  # noqa: BLE001 - frontera con el sistema
        raise InputError(f"No se pudo escribir el texto: {exc}") from exc
    finally:
        if anterior is not None:
            try:
                pyperclip.copy(anterior)
            except Exception:  # noqa: BLE001 - restaurar es un intento, no una garantía
                _logger.debug("No se pudo restaurar el portapapeles.")

    return len(text)


def normalise_keys(combination: str) -> list[str]:
    """Interpreta una combinación de teclas escrita en lenguaje natural.

    Acepta las formas en que el modelo puede expresarla —«ctrl+c», «control +
    c», «ctrl c»— y los nombres en español de las teclas especiales.

    Args:
        combination: Combinación tal como la propuso el modelo.

    Returns:
        Las teclas en el orden en que deben pulsarse.

    Raises:
        InputError: Si la combinación está vacía.
    """
    crudo = combination.replace("+", " ").replace(",", " ")
    teclas = [t.strip().lower() for t in crudo.split() if t.strip()]

    if not teclas:
        raise InputError("No se indicó ninguna tecla.")

    return [_KEY_ALIASES.get(tecla, tecla) for tecla in teclas]


def press_keys(combination: str) -> str:
    """Pulsa una tecla o una combinación.

    Args:
        combination: Combinación como «ctrl+c», «alt+tab» o «enter».

    Returns:
        La combinación efectivamente enviada.

    Raises:
        InputError: Si la combinación no es válida o el sistema no responde.
    """
    teclas = normalise_keys(combination)
    pyautogui = _pyautogui()

    try:
        if len(teclas) == 1:
            pyautogui.press(teclas[0])
        else:
            pyautogui.hotkey(*teclas)
    except Exception as exc:  # noqa: BLE001 - frontera con el sistema
        raise InputError(
            f"No se pudo enviar «{'+'.join(teclas)}»: {exc}. Comprueba que los "
            "nombres de las teclas sean correctos."
        ) from exc

    return "+".join(teclas)


# --------------------------------------------------------------------------- #
# Ratón
# --------------------------------------------------------------------------- #


def _clamp_to_screen(x: int, y: int) -> Position:
    """Restringe una posición a los límites de la pantalla.

    El modelo puede proponer coordenadas desmedidas al estimar dónde está algo.
    Recortarlas evita que la biblioteca aborte por el mecanismo de emergencia,
    que se dispara al llegar a una esquina.
    """
    ancho, alto = get_screen_size()
    return Position(max(1, min(ancho - 2, int(x))), max(1, min(alto - 2, int(y))))


def move_mouse(x: int, y: int) -> Position:
    """Mueve el puntero a una posición de la pantalla.

    Args:
        x: Coordenada horizontal en píxeles.
        y: Coordenada vertical en píxeles.

    Returns:
        La posición efectivamente alcanzada.
    """
    destino = _clamp_to_screen(x, y)
    _pyautogui().moveTo(destino.x, destino.y, duration=0.15)
    return destino


def click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    double: bool = False,
) -> Position:
    """Hace clic, opcionalmente tras mover el puntero.

    Args:
        x: Coordenada horizontal. Si se omite, se hace clic donde esté.
        y: Coordenada vertical.
        button: ``left``, ``right`` o ``middle``.
        double: Si se trata de un doble clic.

    Returns:
        La posición donde se hizo clic.

    Raises:
        InputError: Si el botón no es válido.
    """
    if button not in {"left", "right", "middle"}:
        raise InputError(
            f"Botón no reconocido: «{button}». Usa left, right o middle."
        )

    pyautogui = _pyautogui()

    if x is not None and y is not None:
        destino = move_mouse(x, y)
    else:
        actual = pyautogui.position()
        destino = Position(int(actual[0]), int(actual[1]))

    pyautogui.click(clicks=2 if double else 1, button=button, interval=0.08)
    return destino


def scroll(amount: int) -> int:
    """Desplaza la rueda del ratón.

    Args:
        amount: Muescas a desplazar. Positivo hacia arriba, negativo hacia
            abajo.

    Returns:
        La cantidad aplicada.
    """
    _pyautogui().scroll(int(amount))
    return int(amount)
