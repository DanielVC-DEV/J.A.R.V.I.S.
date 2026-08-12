"""Registro de auditoría de las herramientas ejecutadas.

Cada llamada a una herramienta deja una línea en un archivo JSONL, tanto si se
ejecutó como si fue denegada o quedó pendiente de confirmación. El formato —un
objeto JSON por línea— permite añadir registros sin releer el archivo y
analizarlo después con cualquier herramienta.

El registro es una garantía para el usuario: siempre puede saber qué hizo el
asistente en su equipo. Por eso un fallo al escribirlo nunca interrumpe la
tarea en curso, pero tampoco se ignora en silencio: se anota en el registro
ordinario de la aplicación.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.config.paths import audit_file

__all__ = ["AuditEntry", "AuditLog", "audit_log"]

_logger = logging.getLogger(__name__)

#: Longitud máxima de un valor de texto en el registro. Evita que una llamada
#: con un argumento extenso convierta el archivo en algo inmanejable.
MAX_VALUE_LENGTH = 500


def _truncate(value: Any) -> Any:
    """Acorta los valores de texto desmedidos, dejando constancia del recorte."""
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return f"{value[:MAX_VALUE_LENGTH]}… (+{len(value) - MAX_VALUE_LENGTH})"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Una línea del registro de auditoría."""

    tool: str
    arguments: dict[str, Any]
    risk: str
    decision: str
    reason: str
    executed: bool
    succeeded: bool | None = None
    error: str | None = None
    duration_ms: float | None = None

    def to_json(self) -> str:
        """Serializa la entrada como una línea JSON.

        Returns:
            Un objeto JSON en una sola línea, con marca de tiempo en UTC.
        """
        registro: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": self.tool,
            "arguments": _truncate(self.arguments),
            "risk": self.risk,
            "decision": self.decision,
            "reason": self.reason,
            "executed": self.executed,
        }
        if self.succeeded is not None:
            registro["succeeded"] = self.succeeded
        if self.error is not None:
            registro["error"] = _truncate(self.error)
        if self.duration_ms is not None:
            registro["duration_ms"] = round(self.duration_ms, 1)

        return json.dumps(registro, ensure_ascii=False, default=str)


@dataclass(slots=True)
class AuditLog:
    """Escritor del registro de auditoría.

    Args:
        path: Archivo de destino. Si se omite, se resuelve al arrancar a partir
            de la configuración, de modo que las pruebas puedan sustituirlo.
    """

    path: Path | None = None

    def _destination(self) -> Path:
        return self.path if self.path is not None else audit_file()

    def record(self, entry: AuditEntry) -> bool:
        """Añade una entrada al registro.

        Args:
            entry: Entrada a registrar.

        Returns:
            ``True`` si se escribió correctamente. Un fallo se anota en el
            registro ordinario y devuelve ``False``, pero nunca lanza: la
            auditoría no debe poder tumbar una tarea del usuario.
        """
        destino = self._destination()
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("a", encoding="utf-8") as archivo:
                archivo.write(f"{entry.to_json()}\n")
        except OSError as exc:
            _logger.warning("No se pudo escribir el registro de auditoría: %s", exc)
            return False
        return True

    def read_all(self) -> list[dict[str, Any]]:
        """Lee el registro completo.

        Las líneas corruptas se omiten en lugar de invalidar la lectura: un
        registro truncado por un apagado repentino sigue siendo útil.

        Returns:
            Las entradas registradas, de la más antigua a la más reciente.
        """
        destino = self._destination()
        if not destino.exists():
            return []

        entradas: list[dict[str, Any]] = []
        with destino.open(encoding="utf-8") as archivo:
            for linea in archivo:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    entradas.append(json.loads(linea))
                except json.JSONDecodeError:
                    _logger.debug("Línea de auditoría ilegible, se omite.")
        return entradas

    def clear(self) -> None:
        """Vacía el registro de auditoría."""
        destino = self._destination()
        if destino.exists():
            destino.unlink()


#: Registro de auditoría de la aplicación.
audit_log = AuditLog()
