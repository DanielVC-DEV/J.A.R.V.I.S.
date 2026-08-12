"""Niveles de riesgo de las herramientas.

Este módulo se mantiene deliberadamente sin dependencias para que tanto el
registro de herramientas como el guardia de seguridad puedan importarlo sin
generar ciclos de importación.
"""

from __future__ import annotations

from enum import StrEnum


class Risk(StrEnum):
    """Clasificación del riesgo asociado a la ejecución de una herramienta.

    El valor declarado en el decorador ``@tool`` constituye el *riesgo
    estático*. El guardia de seguridad puede elevarlo en tiempo de ejecución
    según los argumentos recibidos (*riesgo dinámico*): por ejemplo, borrar un
    archivo temporal y borrar un directorio del sistema son la misma
    herramienta con consecuencias radicalmente distintas.
    """

    SAFE = "safe"
    """Se ejecuta automáticamente. Sin efectos destructivos ni irreversibles."""

    CONFIRM = "confirm"
    """Requiere confirmación explícita del usuario antes de ejecutarse."""

    BLOCKED = "blocked"
    """Nunca se ejecuta de forma automática. Requiere autorización expresa."""
