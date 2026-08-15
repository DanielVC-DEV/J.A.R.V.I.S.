"""Almacenamiento de la memoria en SQLite.

Capa fina sobre la base de datos: crea el esquema, lo mantiene al día entre
versiones y ofrece las operaciones elementales. La decisión de qué recordar y
qué recuperar corresponde a ``jarvis.memory.manager``.

Se usa SQLite porque viene con Python, guarda todo en un archivo que el
usuario puede copiar o borrar, y soporta sin esfuerzo los pocos cientos de
hechos que una memoria personal llega a acumular.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jarvis.config.paths import database_file

__all__ = ["Fact", "MemoryDatabase"]

#: Versión del esquema. Al cambiarla hay que añadir su migración en
#: ``_MIGRATIONS``: la memoria del usuario no puede perderse al actualizar.
SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS facts (
            id         INTEGER PRIMARY KEY,
            subject    TEXT NOT NULL,
            content    TEXT NOT NULL,
            category   TEXT NOT NULL DEFAULT 'general',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        # El asunto identifica el hecho: recordar dos veces lo mismo debe
        # actualizarlo, no duplicarlo.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)",
        "CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)",
    ),
}


def _now() -> str:
    """Marca de tiempo en UTC, con precisión de segundos."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Fact:
    """Un hecho que el asistente recuerda."""

    subject: str
    """Aquello sobre lo que trata: «proyecto de python», «mi cumpleaños»."""

    content: str
    """Lo que hay que recordar."""

    category: str = "general"
    created_at: str = ""
    updated_at: str = ""

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Términos con los que puede localizarse.

        Se incluye el contenido además del asunto: el usuario puede preguntar
        por una ruta o un nombre que solo aparece dentro del hecho.
        """
        return (self.subject, self.content)

    def describe(self) -> str:
        """Redacta el hecho de forma compacta."""
        return f"{self.subject}: {self.content}"


class MemoryDatabase:
    """Acceso a la base de datos de la memoria."""

    def __init__(self, path: Path | None = None) -> None:
        """Prepara la base de datos, creándola si no existe.

        Args:
            path: Archivo de la base de datos. Si se omite, el del perfil del
                usuario. Las pruebas pasan uno temporal.
        """
        self.path = path if path is not None else database_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión y garantiza el cierre y la confirmación."""
        conexion = sqlite3.connect(self.path)
        conexion.row_factory = sqlite3.Row
        try:
            yield conexion
            conexion.commit()
        finally:
            conexion.close()

    def _migrate(self) -> None:
        """Lleva el esquema hasta la versión actual.

        Se emplea ``user_version``, un entero que SQLite guarda en el propio
        archivo. Así la actualización es idempotente y no requiere una tabla
        de control aparte.
        """
        with self._connect() as conexion:
            actual = conexion.execute("PRAGMA user_version").fetchone()[0]

            for version in sorted(_MIGRATIONS):
                if version <= actual:
                    continue
                for sentencia in _MIGRATIONS[version]:
                    conexion.execute(sentencia)
                conexion.execute(f"PRAGMA user_version = {version}")

    # -- Escritura ---------------------------------------------------------- #

    def save(self, subject: str, content: str, category: str = "general") -> bool:
        """Guarda un hecho, actualizándolo si ya se conocía.

        Args:
            subject: Aquello sobre lo que trata.
            content: Lo que hay que recordar.
            category: Agrupación, útil para revisar la memoria por temas.

        Returns:
            ``True`` si el hecho es nuevo; ``False`` si actualizó uno anterior.
        """
        momento = _now()

        with self._connect() as conexion:
            existente = conexion.execute(
                "SELECT created_at FROM facts WHERE subject = ?", (subject,)
            ).fetchone()

            if existente is None:
                conexion.execute(
                    "INSERT INTO facts (subject, content, category, created_at,"
                    " updated_at) VALUES (?, ?, ?, ?, ?)",
                    (subject, content, category, momento, momento),
                )
                return True

            conexion.execute(
                "UPDATE facts SET content = ?, category = ?, updated_at = ?"
                " WHERE subject = ?",
                (content, category, momento, subject),
            )
            return False

    def delete(self, subject: str) -> bool:
        """Olvida un hecho.

        Returns:
            ``True`` si existía y se borró.
        """
        with self._connect() as conexion:
            cursor = conexion.execute("DELETE FROM facts WHERE subject = ?", (subject,))
            return cursor.rowcount > 0

    def clear(self) -> int:
        """Vacía la memoria por completo.

        Returns:
            El número de hechos eliminados.
        """
        with self._connect() as conexion:
            cursor = conexion.execute("DELETE FROM facts")
            return cursor.rowcount

    # -- Lectura ------------------------------------------------------------ #

    def all_facts(self, category: str | None = None) -> list[Fact]:
        """Devuelve los hechos almacenados, del más reciente al más antiguo."""
        consulta = "SELECT * FROM facts"
        parametros: tuple = ()

        if category:
            consulta += " WHERE category = ?"
            parametros = (category,)

        consulta += " ORDER BY updated_at DESC"

        with self._connect() as conexion:
            filas = conexion.execute(consulta, parametros).fetchall()

        return [
            Fact(
                subject=fila["subject"],
                content=fila["content"],
                category=fila["category"],
                created_at=fila["created_at"],
                updated_at=fila["updated_at"],
            )
            for fila in filas
        ]

    def count(self) -> int:
        """Número de hechos almacenados."""
        with self._connect() as conexion:
            return int(conexion.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
