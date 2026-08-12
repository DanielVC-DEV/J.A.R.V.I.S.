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
import difflib
import io
import os
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
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


# --------------------------------------------------------------------------- #
# Umbrales de decisión
# --------------------------------------------------------------------------- #

#: Puntuación mínima para considerar que un término identifica una aplicación.
#: Por debajo se informa de que no se encontró nada, en lugar de abrir algo
#: que el usuario no pidió.
ACCEPT_THRESHOLD = 70.0

#: Distancia mínima entre la mejor coincidencia y la siguiente para darla por
#: buena. Si dos aplicaciones puntúan casi igual, se pregunta.
AMBIGUITY_GAP = 5.0

#: Número máximo de alternativas que se ofrecen al desambiguar.
MAX_CANDIDATES = 4


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
# Coincidencia (lógica pura, sin dependencias del sistema operativo)
# --------------------------------------------------------------------------- #


def _normalise(text: str) -> str:
    """Reduce un texto a una forma comparable.

    Pasa a minúsculas, elimina las tildes y la puntuación, y normaliza los
    espacios. Sin este paso, «configuración» y «configuracion» serían términos
    distintos, y la transcripción de voz produce ambas formas indistintamente.

    Se emplea la descomposición canónica (NFD) y no la de compatibilidad
    (NFKD): esta última traduciría el símbolo «™» a las letras «TM», que se
    colarían en el nombre y falsearían la comparación.

    Args:
        text: Texto a normalizar.

    Returns:
        El texto reducido a minúsculas sin acentos ni signos.
    """
    descompuesto = unicodedata.normalize("NFD", text)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes)
    return " ".join(limpio.lower().split())


def _ratio(a: str, b: str) -> float:
    """Similitud entre dos cadenas, entre 0 y 1."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def _score_word(query_word: str, word: str) -> float:
    """Puntúa una palabra suelta contra otra.

    Args:
        query_word: Palabra dicha por el usuario, ya normalizada.
        word: Palabra de la denominación de la aplicación, ya normalizada.

    Returns:
        Una puntuación entre 0 y 100.
    """
    if query_word == word:
        return 100.0
    if word.startswith(query_word):
        return 92.0
    if query_word in word:
        return 78.0
    return _ratio(query_word, word) * 90.0


def _score_term(query: str, term: str) -> float:
    """Puntúa un término de búsqueda contra una denominación concreta.

    Se combinan dos lecturas complementarias:

    * **Consulta completa contra denominación completa.** Cubre el caso
      habitual, en el que el usuario nombra la aplicación tal cual o comete
      una errata: «crome» debe encontrar «Chrome».
    * **Palabra a palabra.** Cubre las abreviaturas y las omisiones, donde la
      consulta no aparece literalmente: «vs code» debe encontrar «Visual
      Studio Code». Se pondera a partes iguales la mejor palabra y el promedio
      de todas, de modo que acertar una palabra no baste si las demás fallan.

    Esta gradación explícita se prefiere a una métrica opaca porque su
    comportamiento puede razonarse y comprobarse caso por caso.

    Args:
        query: Término normalizado que dijo el usuario.
        term: Denominación normalizada de la aplicación.

    Returns:
        Una puntuación entre 0 y 100.
    """
    if not query or not term:
        return 0.0

    if query == term:
        return 100.0

    palabras = term.split()
    tokens = query.split()

    if query in palabras:
        directa = 95.0
    elif term.startswith(query):
        directa = 92.0
    elif any(palabra.startswith(query) for palabra in palabras):
        directa = 88.0
    elif query in term:
        directa = 78.0
    else:
        directa = _ratio(query, term) * 75.0

    parciales = [
        max((_score_word(token, palabra) for palabra in palabras), default=0.0)
        for token in tokens
    ]
    por_palabras = (
        0.5 * max(parciales) + 0.5 * (sum(parciales) / len(parciales))
        if parciales
        else 0.0
    )

    # Ante dos coincidencias equivalentes se prefiere el nombre más escueto:
    # «Google Chrome» antes que «Chrome Remote Desktop».
    exceso = max(0, len(term) - len(query))
    return max(0.0, max(directa, por_palabras) - min(6.0, exceso * 0.5))


def score_match(query: str, application: Application) -> float:
    """Puntúa un término de búsqueda contra una aplicación.

    Se evalúan el nombre y todos sus alias, y se conserva la mejor puntuación.

    Args:
        query: Término tal como lo dijo el usuario.
        application: Aplicación candidata.

    Returns:
        Una puntuación entre 0 y 100.
    """
    normalizado = _normalise(query)
    return max(
        (_score_term(normalizado, _normalise(term)) for term in application.search_terms),
        default=0.0,
    )


def rank(query: str, applications: list[Application]) -> list[Match]:
    """Ordena las aplicaciones por su afinidad con el término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        applications: Índice sobre el que buscar.

    Returns:
        Las coincidencias con puntuación positiva, de mejor a peor. En caso de
        empate se antepone el nombre más corto, para que el resultado sea
        estable entre ejecuciones.
    """
    coincidencias = [
        Match(application=app, score=score_match(query, app)) for app in applications
    ]
    positivas = [m for m in coincidencias if m.score > 0]
    positivas.sort(key=lambda m: (-m.score, len(m.application.name), m.application.name))
    return positivas


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
    coincidencias = rank(query, indice)

    if not coincidencias or coincidencias[0].score < ACCEPT_THRESHOLD:
        raise ApplicationNotFoundError(
            f"No encontré ninguna aplicación que se corresponda con «{query}»."
        )

    mejor = coincidencias[0]
    empatadas = [
        m
        for m in coincidencias
        if m.score >= ACCEPT_THRESHOLD and mejor.score - m.score < AMBIGUITY_GAP
    ]

    if len(empatadas) > 1:
        raise AmbiguousApplicationError(
            query, [m.application for m in empatadas[:MAX_CANDIDATES]]
        )

    return mejor.application


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
