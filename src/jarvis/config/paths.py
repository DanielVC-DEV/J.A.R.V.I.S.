"""Rutas de los datos de la aplicación.

Centraliza la ubicación de la configuración, los registros y los modelos
descargados. Ningún otro módulo debe construir estas rutas por su cuenta.

En Windows los datos residen en ``%APPDATA%\\JARVIS``. Se contempla también
una ubicación equivalente en otros sistemas para que las pruebas puedan
ejecutarse fuera de Windows.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path

__all__ = [
    "audit_file",
    "config_file",
    "data_dir",
    "database_file",
    "ensure_directories",
    "log_dir",
    "models_dir",
]


@cache
def data_dir() -> Path:
    """Directorio raíz de los datos de la aplicación.

    Puede forzarse mediante la variable de entorno ``JARVIS_DATA_DIR``, lo que
    resulta útil en pruebas y en instalaciones portables.
    """
    override = os.environ.get("JARVIS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"

    return Path(base).expanduser().resolve() / "JARVIS"


def config_file() -> Path:
    """Preferencias del usuario, editables desde la interfaz."""
    return data_dir() / "settings.json"


def database_file() -> Path:
    """Base de datos SQLite de la memoria del asistente."""
    return data_dir() / "memory.db"


def audit_file() -> Path:
    """Registro de auditoría: una línea JSON por herramienta ejecutada."""
    return data_dir() / "audit.jsonl"


def log_dir() -> Path:
    """Directorio de los registros de la aplicación."""
    return data_dir() / "logs"


def models_dir() -> Path:
    """Modelos descargados en el primer arranque (transcripción, wake word)."""
    return data_dir() / "models"


def ensure_directories() -> None:
    """Crea los directorios necesarios si aún no existen.

    Es idempotente y debe invocarse una sola vez durante el arranque.
    """
    for directory in (data_dir(), log_dir(), models_dir()):
        directory.mkdir(parents=True, exist_ok=True)
