"""Control del volumen maestro de Windows.

Actúa sobre el mezclador del sistema mediante las Core Audio APIs, expuestas
en Python por ``pycaw``. Se prefiere esta vía a simular las teclas de volumen
porque permite leer el nivel actual y fijar un valor exacto, en lugar de
avanzar a ciegas en incrementos fijos.

El módulo puede importarse en cualquier sistema operativo —así las pruebas del
resto del proyecto no quedan atadas a Windows—, pero sus funciones fallan de
forma explícita si se invocan fuera de él.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = [
    "AudioError",
    "adjust_volume",
    "get_volume",
    "is_muted",
    "set_muted",
    "set_volume",
    "toggle_mute",
]


class AudioError(RuntimeError):
    """No se pudo acceder al dispositivo de audio o manipular el volumen."""


def _require_windows() -> None:
    """Comprueba que el sistema admite el control de audio."""
    if sys.platform != "win32":
        raise AudioError(
            "El control de volumen solo está disponible en Windows."
        )


@contextmanager
def _volume_interface() -> Iterator[Any]:
    """Proporciona la interfaz de volumen del dispositivo de salida activo.

    Las dependencias se importan dentro de la función, no al cargar el módulo,
    para que este siga siendo importable en sistemas donde ``pycaw`` no está
    instalado.

    Yields:
        El objeto ``IAudioEndpointVolume`` del dispositivo predeterminado.

    Raises:
        AudioError: Si el sistema no es Windows, si faltan las dependencias o
            si no hay ningún dispositivo de salida disponible.
    """
    _require_windows()

    try:
        from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
        from ctypes import POINTER, cast

        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except ImportError as exc:
        raise AudioError(
            "Faltan las dependencias de audio. Instala el proyecto con "
            "«pip install -e .» en Windows."
        ) from exc

    # Cada hilo que use COM debe inicializarlo por su cuenta. El asistente
    # ejecuta las herramientas fuera del hilo principal, de modo que esta
    # inicialización no es opcional.
    CoInitialize()
    try:
        speakers = AudioUtilities.GetSpeakers()
        if speakers is None:
            raise AudioError("No se encontró ningún dispositivo de salida de audio.")

        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        yield cast(interface, POINTER(IAudioEndpointVolume))
    except AudioError:
        raise
    except OSError as exc:
        raise AudioError(f"El dispositivo de audio no respondió: {exc}") from exc
    finally:
        CoUninitialize()


def _clamp(level: int) -> int:
    """Restringe un nivel al rango admitido."""
    return max(0, min(100, level))


def get_volume() -> int:
    """Consulta el volumen maestro actual.

    Returns:
        El nivel actual, entre 0 y 100.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    with _volume_interface() as volume:
        return round(volume.GetMasterVolumeLevelScalar() * 100)


def set_volume(level: int) -> int:
    """Fija el volumen maestro a un valor absoluto.

    Args:
        level: Nivel deseado. Los valores fuera del rango 0-100 se ajustan al
            extremo más cercano en lugar de provocar un error, ya que el modelo
            puede proponer valores desmedidos al interpretar una orden vaga.

    Returns:
        El nivel efectivamente aplicado.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    target = _clamp(level)
    with _volume_interface() as volume:
        volume.SetMasterVolumeLevelScalar(target / 100, None)
    return target


def adjust_volume(delta: int) -> int:
    """Modifica el volumen de forma relativa al nivel actual.

    Resuelve órdenes como «súbelo un poco» sin que el modelo tenga que
    consultar antes el nivel vigente.

    Args:
        delta: Incremento en puntos porcentuales. Puede ser negativo.

    Returns:
        El nivel resultante.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    with _volume_interface() as volume:
        current = round(volume.GetMasterVolumeLevelScalar() * 100)
        target = _clamp(current + delta)
        volume.SetMasterVolumeLevelScalar(target / 100, None)
    return target


def is_muted() -> bool:
    """Indica si la salida de audio está silenciada.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    with _volume_interface() as volume:
        return bool(volume.GetMute())


def set_muted(muted: bool) -> bool:
    """Silencia o restablece la salida de audio.

    Args:
        muted: ``True`` para silenciar, ``False`` para restablecer.

    Returns:
        El estado aplicado.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    with _volume_interface() as volume:
        volume.SetMute(int(muted), None)
    return muted


def toggle_mute() -> bool:
    """Invierte el estado de silencio.

    Returns:
        El estado resultante: ``True`` si quedó silenciado.

    Raises:
        AudioError: Si no se pudo acceder al dispositivo de audio.
    """
    with _volume_interface() as volume:
        target = not bool(volume.GetMute())
        volume.SetMute(int(target), None)
    return target
