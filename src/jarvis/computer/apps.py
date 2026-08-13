"""Localización y apertura de aplicaciones instaladas.

Abrir un programa a partir de un nombre dicho en voz alta es más delicado de lo
que parece: ``os.system("chrome")`` no funciona, el usuario dirá «crome» o
«el bloc de notas», y el mismo término puede corresponder a varias
aplicaciones. Este módulo resuelve el problema en dos capas bien separadas:

* **Indexación** (dependiente de Windows): reúne las aplicaciones disponibles
  a partir del menú Inicio, del registro y de una tabla de programas
  integrados en el sistema.
* **Coincidencia** (lógica pura): puntúa un término de búsqueda contra el
  índice, sin tocar el sistema operativo.

Esta separación es deliberada. La segunda capa concentra casi toda la
complejidad y, al no depender de Windows, puede probarse por completo con un
índice sintético.

Ante varias coincidencias igual de buenas el módulo **no adivina**: informa de
la ambigüedad para que el asistente pregunte.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

from jarvis.computer import matching
from jarvis.computer.matching import (
    ACCEPT_THRESHOLD,
    AMBIGUITY_GAP,
    MAX_CANDIDATES,
    normalise as _normalise,
)

__all__ = [
    "ACCEPT_THRESHOLD",
    "AMBIGUITY_GAP",
    "MAX_CANDIDATES",
    "AmbiguousApplicationError",
    "Application",
    "ApplicationError",
    "ApplicationNotFoundError",
    "Match",
    "index_applications",
    "open_application",
    "rank",
    "resolve",
    "score_match",
]


class ApplicationError(RuntimeError):
    """Error genérico al resolver o abrir una aplicación."""


class ApplicationNotFoundError(ApplicationError):
    """Ninguna aplicación instalada se corresponde con el término buscado."""


class AmbiguousApplicationError(ApplicationError):
    """Varias aplicaciones coinciden con solvencia parecida.

    Attributes:
        candidates: Alternativas ordenadas de mejor a peor.
    """

    def __init__(self, query: str, candidates: list[Application]) -> None:
        self.query = query
        self.candidates = candidates
        nombres = ", ".join(app.name for app in candidates)
        super().__init__(
            f"«{query}» coincide con varias aplicaciones: {nombres}. "
            "Conviene preguntar al usuario cuál quiere."
        )


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Application:
    """Una aplicación que el asistente puede abrir."""

    name: str
    """Nombre legible, tal como se le mostrará al usuario."""

    target: str
    """Lo que se entrega al sistema para abrirla: ruta, comando o URI."""

    source: str = "desconocido"
    """Procedencia del registro, útil para diagnosticar el índice."""

    aliases: tuple[str, ...] = ()
    """Denominaciones alternativas, incluidas las de otros idiomas."""

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Todos los términos con los que puede identificarse."""
        return (self.name, *self.aliases)


@dataclass(frozen=True, slots=True)
class Match:
    """Una aplicación junto con su puntuación frente a un término."""

    application: Application
    score: float


# --------------------------------------------------------------------------- #
# Aplicaciones integradas en Windows
# --------------------------------------------------------------------------- #

#: Programas del sistema que conviene declarar explícitamente. Algunos no
#: aparecen en el menú Inicio y otros solo figuran con su nombre traducido, de
#: modo que un usuario que diga «notepad» en un Windows en español no los
#: encontraría.
BUILTIN_APPLICATIONS: tuple[Application, ...] = (
    Application(
        name="Bloc de notas",
        target="notepad.exe",
        source="integrada",
        aliases=("notepad", "blocnotas", "bloc"),
    ),
    Application(
        name="Calculadora",
        target="calc.exe",
        source="integrada",
        aliases=("calculator", "calc"),
    ),
    Application(
        name="Explorador de archivos",
        target="explorer.exe",
        source="integrada",
        aliases=("explorer", "file explorer", "explorador", "archivos"),
    ),
    Application(
        name="Símbolo del sistema",
        target="cmd.exe",
        source="integrada",
        aliases=("cmd", "command prompt", "consola", "terminal"),
    ),
    Application(
        name="Windows PowerShell",
        target="powershell.exe",
        source="integrada",
        aliases=("powershell", "ps"),
    ),
    Application(
        name="Configuración",
        target="ms-settings:",
        source="integrada",
        aliases=("settings", "ajustes", "configuracion"),
    ),
    Application(
        name="Panel de control",
        target="control.exe",
        source="integrada",
        aliases=("control panel", "panel"),
    ),
    Application(
        name="Administrador de tareas",
        target="taskmgr.exe",
        source="integrada",
        aliases=("task manager", "taskmgr", "administrador de tareas"),
    ),
    Application(
        name="Paint",
        target="mspaint.exe",
        source="integrada",
        aliases=("mspaint", "dibujo"),
    ),
)


# --------------------------------------------------------------------------- #
# Coincidencia
# --------------------------------------------------------------------------- #
#
# La lógica vive en ``jarvis.computer.matching``, compartida con la gestión de
# ventanas: el problema —encontrar lo que el usuario nombró de forma
# aproximada— es el mismo, y tenerlo en un solo sitio evita que ambas
# implementaciones diverjan.


def score_match(query: str, application: Application) -> float:
    """Puntúa un término de búsqueda contra una aplicación.

    Args:
        query: Término tal como lo dijo el usuario.
        application: Aplicación candidata.

    Returns:
        Una puntuación entre 0 y 100.
    """
    return matching.score(query, application.search_terms)


def rank(query: str, applications: list[Application]) -> list[Match]:
    """Ordena las aplicaciones por su afinidad con el término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        applications: Índice sobre el que buscar.

    Returns:
        Las coincidencias con puntuación positiva, de mejor a peor.
    """
    return [
        Match(application=m.item, score=m.score)  # type: ignore[arg-type]
        for m in matching.rank(query, applications, lambda a: a.search_terms)
    ]


def resolve(query: str, applications: list[Application] | None = None) -> Application:
    """Determina a qué aplicación se refiere un término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        applications: Índice sobre el que buscar. Si se omite, se construye el
            índice real del equipo.

    Returns:
        La aplicación identificada.

    Raises:
        ApplicationNotFoundError: Si ninguna candidata alcanza el umbral.
        AmbiguousApplicationError: Si varias puntúan de forma equivalente.
    """
    indice = applications if applications is not None else list(index_applications())

    try:
        return matching.resolve(
            query, indice, lambda a: a.search_terms, what="aplicación"
        )
    except matching.AmbiguousMatchError as exc:
        raise AmbiguousApplicationError(query, list(exc.candidates)) from None
    except matching.NoMatchError as exc:
        raise ApplicationNotFoundError(str(exc)) from None


# --------------------------------------------------------------------------- #
# Indexación (dependiente de Windows)
# --------------------------------------------------------------------------- #


def _start_menu_applications() -> list[Application]:
    """Enumera las entradas del menú Inicio mediante PowerShell.

    ``Get-StartApps`` devuelve en una sola consulta tanto los accesos directos
    clásicos como las aplicaciones de la Tienda, cada una con el identificador
    que permite abrirla. Recorrer a mano las carpetas de accesos directos
    dejaría fuera estas últimas.

    Returns:
        Las aplicaciones encontradas, o una lista vacía si la consulta falla.
        Un fallo aquí degrada el índice pero no impide funcionar: quedan el
        registro y la tabla de programas integrados.
    """
    comando = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-StartApps | ConvertTo-Csv -NoTypeInformation",
    ]

    try:
        salida = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    aplicaciones: list[Application] = []
    for fila in csv.DictReader(io.StringIO(salida)):
        nombre = (fila.get("Name") or "").strip()
        identificador = (fila.get("AppID") or "").strip()
        if not nombre or not identificador:
            continue
        aplicaciones.append(
            Application(
                name=nombre,
                target=f"shell:AppsFolder\\{identificador}",
                source="menú inicio",
            )
        )

    return aplicaciones


def _registered_applications() -> list[Application]:
    """Enumera las aplicaciones registradas en la clave ``App Paths``.

    Es la clave que los instaladores rellenan para que un programa pueda
    abrirse por su nombre de ejecutable. Aporta denominaciones cortas —
    «chrome», «code»— que no siempre figuran en el menú Inicio.

    Returns:
        Las aplicaciones encontradas, o una lista vacía si el registro no es
        accesible.
    """
    if sys.platform != "win32":
        return []

    import winreg

    ruta = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    aplicaciones: list[Application] = []

    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            clave = winreg.OpenKey(raiz, ruta)
        except OSError:
            continue

        with clave:
            for indice in range(winreg.QueryInfoKey(clave)[0]):
                try:
                    nombre_exe = winreg.EnumKey(clave, indice)
                    with winreg.OpenKey(clave, nombre_exe) as subclave:
                        destino = winreg.QueryValueEx(subclave, "")[0]
                except OSError:
                    continue

                if not destino:
                    continue

                base = nombre_exe.removesuffix(".exe")
                aplicaciones.append(
                    Application(
                        name=base.replace("_", " ").title(),
                        target=str(destino).strip('"'),
                        source="registro",
                        aliases=(base, nombre_exe),
                    )
                )

    return aplicaciones


def _deduplicate(applications: list[Application]) -> tuple[Application, ...]:
    """Elimina entradas repetidas conservando la de mejor procedencia.

    Una misma aplicación suele aparecer en el menú Inicio y en el registro. Se
    conserva la primera vista y se le incorporan los alias de las demás, de
    modo que «Google Chrome» siga siendo localizable diciendo «chrome».
    """
    por_nombre: dict[str, Application] = {}

    for app in applications:
        clave = _normalise(app.name)
        if not clave:
            continue

        existente = por_nombre.get(clave)
        if existente is None:
            por_nombre[clave] = app
            continue

        alias = tuple(dict.fromkeys((*existente.aliases, *app.aliases)))
        por_nombre[clave] = Application(
            name=existente.name,
            target=existente.target,
            source=existente.source,
            aliases=alias,
        )

    return tuple(por_nombre.values())


@lru_cache(maxsize=1)
def index_applications() -> tuple[Application, ...]:
    """Construye el índice de aplicaciones disponibles en el equipo.

    El resultado se memoriza: recorrer el menú Inicio y el registro cuesta
    alrededor de un segundo, y el conjunto de programas instalados no cambia
    durante una sesión. Use ``index_applications.cache_clear()`` tras instalar
    algo nuevo.

    Returns:
        El índice completo, sin duplicados.
    """
    return _deduplicate(
        [
            *_start_menu_applications(),
            *_registered_applications(),
            *BUILTIN_APPLICATIONS,
        ]
    )


def open_application(query: str) -> str:
    """Abre la aplicación que corresponda al término indicado.

    Args:
        query: Nombre de la aplicación tal como lo dijo el usuario.

    Returns:
        Una confirmación con el nombre real de la aplicación abierta.

    Raises:
        ApplicationNotFoundError: Si no se encontró ninguna correspondencia.
        AmbiguousApplicationError: Si el término resulta ambiguo.
        ApplicationError: Si la aplicación se identificó pero no pudo abrirse.
    """
    if sys.platform != "win32":
        raise ApplicationError(
            "La apertura de aplicaciones solo está disponible en Windows."
        )

    aplicacion = resolve(query)

    try:
        os.startfile(aplicacion.target)
    except OSError as exc:
        raise ApplicationError(
            f"Encontré «{aplicacion.name}» pero no pude abrirla: {exc}"
        ) from exc

    return f"{aplicacion.name} abierta."
