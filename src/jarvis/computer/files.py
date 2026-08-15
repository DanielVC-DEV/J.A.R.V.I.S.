"""Operaciones sobre archivos y carpetas del usuario.

Es la parte del asistente con más capacidad de causar daño, de modo que el
módulo asume tres compromisos:

* **Eliminar significa enviar a la papelera**, nunca borrar de forma
  definitiva. Una orden mal entendida debe ser reversible.
* **Los nombres de carpeta se resuelven de verdad.** «Documentos» no es una
  carpeta bajo el perfil del usuario: su ubicación real la guarda el registro
  de Windows y puede estar en otra unidad o en la nube.
* **Lo que se lee está acotado.** Volcar un archivo de cien megabytes en el
  contexto del modelo agotaría el presupuesto de tokens de un turno.

El confinamiento a las carpetas permitidas no se aplica aquí sino en el
guardia de seguridad, que intercepta la llamada antes de que este módulo se
ejecute. Así la política vive en un solo sitio y no depende de que cada
función se acuerde de comprobarla.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MAX_READ_CHARS",
    "FileEntry",
    "FileError",
    "copy_item",
    "create_folder",
    "known_folders",
    "list_directory",
    "move_item",
    "read_text",
    "resolve_path",
    "search_files",
    "send_to_trash",
    "write_text",
]

#: Longitud máxima de un archivo leído. Un texto más largo se recorta: el
#: presupuesto de tokens de un turno es limitado y volcarlo entero impediría
#: al modelo hacer nada más.
MAX_READ_CHARS = 6_000

#: Número máximo de entradas que devuelve un listado o una búsqueda.
MAX_ENTRIES = 100

#: Extensiones que se consideran texto legible. El resto se describe por su
#: tamaño en lugar de intentar interpretarlo.
TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss",
        ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb",
        ".php", ".sh", ".ps1", ".bat", ".sql", ".xml", ".svg", ".gitignore",
    }
)

#: Nombres con los que el usuario se referirá a sus carpetas, y la clave con
#: la que Windows las registra.
_SHELL_FOLDERS = {
    "escritorio": "Desktop",
    "documentos": "Personal",
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
    "imagenes": "My Pictures",
    "musica": "My Music",
    "videos": "My Video",
}

#: Alternativas por si el registro no está disponible.
_FALLBACK_FOLDERS = {
    "escritorio": ("Desktop", "Escritorio"),
    "documentos": ("Documents", "Documentos"),
    "descargas": ("Downloads", "Descargas"),
    "imagenes": ("Pictures", "Imágenes"),
    "musica": ("Music", "Música"),
    "videos": ("Videos", "Vídeos"),
}


class FileError(RuntimeError):
    """No se pudo completar la operación sobre el archivo o la carpeta."""


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Un archivo o una carpeta dentro de un listado."""

    path: Path
    is_dir: bool
    size_bytes: int = 0

    @property
    def name(self) -> str:
        return self.path.name

    def describe(self) -> str:
        """Redacta una línea legible para el modelo."""
        if self.is_dir:
            return f"{self.name}/"
        return f"{self.name} ({_human_size(self.size_bytes)})"


def _human_size(size: int) -> str:
    """Expresa un tamaño en la unidad que lo haga legible."""
    for unidad, umbral in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= umbral:
            return f"{size / umbral:.1f} {unidad}"
    return f"{size} B"


# --------------------------------------------------------------------------- #
# Carpetas conocidas
# --------------------------------------------------------------------------- #


def _from_registry(key: str) -> Path | None:
    """Consulta al registro la ubicación real de una carpeta del usuario."""
    if sys.platform != "win32":
        return None

    try:
        import winreg

        ruta = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ruta) as clave:
            valor = winreg.QueryValueEx(clave, key)[0]
    except OSError:
        return None

    carpeta = Path(os.path.expandvars(str(valor)))
    return carpeta if carpeta.is_dir() else None


def known_folders() -> dict[str, Path]:
    """Devuelve las carpetas personales del usuario, por su nombre en español.

    Se consulta el registro de Windows y no se compone la ruta a partir del
    perfil: el usuario puede haber movido «Documentos» a otra unidad o tenerlo
    sincronizado con la nube, y suponer la ubicación llevaría al asistente a
    trabajar sobre una carpeta vacía que no es la que ve.

    Returns:
        Las carpetas existentes, indexadas por su nombre sin acentos.
    """
    encontradas: dict[str, Path] = {}
    inicio = Path.home()

    for nombre, clave in _SHELL_FOLDERS.items():
        carpeta = _from_registry(clave)
        if carpeta is None:
            for candidato in _FALLBACK_FOLDERS[nombre]:
                posible = inicio / candidato
                if posible.is_dir():
                    carpeta = posible
                    break
        if carpeta is not None:
            encontradas[nombre] = carpeta

    return encontradas


def resolve_path(path: str) -> Path:
    """Interpreta una ruta escrita por el modelo o dicha por el usuario.

    Admite rutas absolutas, rutas con «~», variables de entorno y los nombres
    en español de las carpetas personales: «Documentos/informe.txt» se
    resuelve a la ubicación real de Documentos.

    Args:
        path: Ruta tal como llegó.

    Returns:
        La ruta absoluta correspondiente.

    Raises:
        FileError: Si la ruta está vacía o no puede interpretarse.
    """
    crudo = path.strip().strip('"').strip("'")
    if not crudo:
        raise FileError("No se indicó ninguna ruta.")

    expandido = Path(os.path.expandvars(crudo)).expanduser()

    if expandido.is_absolute():
        return expandido

    # Rutas relativas a una carpeta personal: «Documentos/informe.txt».
    partes = expandido.parts
    if partes:
        from jarvis.computer.matching import normalise

        primera = normalise(partes[0])
        carpetas = known_folders()
        if primera in carpetas:
            return carpetas[primera].joinpath(*partes[1:])

    # Sin más contexto, se interpreta bajo el perfil del usuario. Nunca bajo
    # el directorio de trabajo, que para el asistente carece de significado.
    return Path.home() / expandido


# --------------------------------------------------------------------------- #
# Consulta
# --------------------------------------------------------------------------- #


def list_directory(path: str, pattern: str = "*") -> list[FileEntry]:
    """Enumera el contenido de una carpeta.

    Args:
        path: Carpeta a listar.
        pattern: Patrón de nombre, como ``*.pdf``.

    Returns:
        Las entradas encontradas, con las carpetas primero.

    Raises:
        FileError: Si la carpeta no existe o no es accesible.
    """
    carpeta = resolve_path(path)

    if not carpeta.exists():
        raise FileError(f"No existe la carpeta «{carpeta}».")
    if not carpeta.is_dir():
        raise FileError(f"«{carpeta}» es un archivo, no una carpeta.")

    entradas: list[FileEntry] = []
    try:
        for elemento in sorted(carpeta.glob(pattern)):
            try:
                es_carpeta = elemento.is_dir()
                tamano = 0 if es_carpeta else elemento.stat().st_size
            except OSError:
                continue
            entradas.append(FileEntry(elemento, es_carpeta, tamano))
    except OSError as exc:
        raise FileError(f"No se pudo leer «{carpeta}»: {exc}") from exc

    entradas.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entradas[:MAX_ENTRIES]


def read_text(path: str, max_chars: int = MAX_READ_CHARS) -> tuple[str, bool]:
    """Lee el contenido de un archivo de texto.

    Args:
        path: Archivo a leer.
        max_chars: Longitud máxima devuelta.

    Returns:
        Una tupla ``(contenido, recortado)``.

    Raises:
        FileError: Si el archivo no existe, es binario o no puede leerse.
    """
    archivo = resolve_path(path)

    if not archivo.exists():
        raise FileError(f"No existe el archivo «{archivo}».")
    if archivo.is_dir():
        raise FileError(f"«{archivo}» es una carpeta. Usa list_directory.")

    if archivo.suffix.lower() not in TEXT_SUFFIXES:
        tamano = _human_size(archivo.stat().st_size)
        raise FileError(
            f"«{archivo.name}» no parece un archivo de texto ({tamano}). "
            "Solo puedo leer texto plano."
        )

    try:
        # Los archivos escritos en Windows pueden venir en la codificación
        # heredada; recurrir a ella evita fallar con acentos y eñes.
        contenido = archivo.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            contenido = archivo.read_text(encoding="cp1252")
        except (UnicodeDecodeError, OSError) as exc:
            raise FileError(
                f"No se pudo interpretar el texto de «{archivo.name}»."
            ) from exc
    except OSError as exc:
        raise FileError(f"No se pudo leer «{archivo.name}»: {exc}") from exc

    if len(contenido) > max_chars:
        return contenido[:max_chars], True
    return contenido, False


def search_files(root: str, pattern: str, max_results: int = 25) -> list[FileEntry]:
    """Busca archivos por nombre dentro de una carpeta y sus subcarpetas.

    Args:
        root: Carpeta desde la que buscar.
        pattern: Patrón de nombre, como ``*.pdf`` o ``informe*``.
        max_results: Número máximo de resultados.

    Returns:
        Los archivos encontrados, del más reciente al más antiguo.

    Raises:
        FileError: Si la carpeta de partida no existe.
    """
    carpeta = resolve_path(root)

    if not carpeta.is_dir():
        raise FileError(f"No existe la carpeta «{carpeta}».")

    encontrados: list[FileEntry] = []
    for elemento in carpeta.rglob(pattern):
        try:
            if elemento.is_dir():
                continue
            encontrados.append(FileEntry(elemento, False, elemento.stat().st_size))
        except OSError:
            continue
        # Se corta pronto: recorrer un árbol entero puede tardar minutos y el
        # usuario espera una respuesta, no un inventario.
        if len(encontrados) >= max_results * 4:
            break

    encontrados.sort(key=lambda e: _mtime(e.path), reverse=True)
    return encontrados[:max_results]


def _mtime(path: Path) -> float:
    """Fecha de modificación, o cero si no puede consultarse."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- #
# Modificación
# --------------------------------------------------------------------------- #


def write_text(path: str, content: str, append: bool = False) -> tuple[Path, bool]:
    """Escribe texto en un archivo, creándolo si no existe.

    Args:
        path: Archivo de destino.
        content: Texto a escribir.
        append: Si se añade al final en lugar de reemplazar el contenido.

    Returns:
        Una tupla ``(ruta, existía)``.

    Raises:
        FileError: Si no se pudo escribir.
    """
    archivo = resolve_path(path)
    existia = archivo.exists()

    try:
        archivo.parent.mkdir(parents=True, exist_ok=True)
        with archivo.open("a" if append else "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except OSError as exc:
        raise FileError(f"No se pudo escribir en «{archivo}»: {exc}") from exc

    return archivo, existia


def create_folder(path: str) -> tuple[Path, bool]:
    """Crea una carpeta, incluidas las intermedias que falten.

    Args:
        path: Carpeta a crear.

    Returns:
        Una tupla ``(ruta, ya existía)``.

    Raises:
        FileError: Si no se pudo crear.
    """
    carpeta = resolve_path(path)
    existia = carpeta.is_dir()

    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileError(f"No se pudo crear «{carpeta}»: {exc}") from exc

    return carpeta, existia


def move_item(source: str, destination: str) -> tuple[Path, Path]:
    """Mueve o renombra un archivo o una carpeta.

    Args:
        source: Elemento a mover.
        destination: Destino. Si es una carpeta existente, se mueve dentro.

    Returns:
        Una tupla ``(origen, destino final)``.

    Raises:
        FileError: Si el origen no existe o el destino ya está ocupado.
    """
    origen = resolve_path(source)
    destino = resolve_path(destination)

    if not origen.exists():
        raise FileError(f"No existe «{origen}».")

    if destino.is_dir():
        destino = destino / origen.name

    if destino.exists():
        raise FileError(
            f"Ya existe «{destino.name}» en el destino. Elige otro nombre o "
            "muévelo a otra carpeta."
        )

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(destino))
    except OSError as exc:
        raise FileError(f"No se pudo mover «{origen.name}»: {exc}") from exc

    return origen, destino


def copy_item(source: str, destination: str) -> tuple[Path, Path]:
    """Copia un archivo o una carpeta.

    Args:
        source: Elemento a copiar.
        destination: Destino. Si es una carpeta existente, se copia dentro.

    Returns:
        Una tupla ``(origen, destino final)``.

    Raises:
        FileError: Si el origen no existe o el destino ya está ocupado.
    """
    origen = resolve_path(source)
    destino = resolve_path(destination)

    if not origen.exists():
        raise FileError(f"No existe «{origen}».")

    if destino.is_dir() and origen.is_file():
        destino = destino / origen.name

    if destino.exists():
        raise FileError(f"Ya existe «{destino.name}» en el destino.")

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if origen.is_dir():
            shutil.copytree(origen, destino)
        else:
            shutil.copy2(origen, destino)
    except OSError as exc:
        raise FileError(f"No se pudo copiar «{origen.name}»: {exc}") from exc

    return origen, destino


def open_in_explorer(path: str) -> Path:
    """Abre una carpeta en el Explorador, o un archivo con su programa.

    Es lo que el usuario espera al decir «abre mi proyecto»: una carpeta se
    muestra, un documento se abre con la aplicación que le corresponda. Sin
    esto, el asistente intenta interpretar la ruta como el nombre de un
    programa instalado y no encuentra nada coherente.

    Args:
        path: Carpeta o archivo a abrir.

    Returns:
        La ruta abierta.

    Raises:
        FileError: Si no existe o el sistema no admite la operación.
    """
    destino = resolve_path(path)

    if not destino.exists():
        raise FileError(f"No existe «{destino}».")

    if sys.platform != "win32":
        raise FileError("Abrir carpetas solo está disponible en Windows.")

    try:
        os.startfile(destino)
    except OSError as exc:
        raise FileError(f"No se pudo abrir «{destino.name}»: {exc}") from exc

    return destino


def send_to_trash(path: str) -> Path:
    """Envía un archivo o una carpeta a la papelera de reciclaje.

    **No se elimina de forma definitiva.** Una orden mal entendida —o una
    ambigüedad que el modelo resolvió mal— debe poder deshacerse, y la
    papelera es el mecanismo que el usuario ya conoce para hacerlo.

    Args:
        path: Elemento a eliminar.

    Returns:
        La ruta del elemento enviado a la papelera.

    Raises:
        FileError: Si el elemento no existe o no pudo enviarse.
    """
    elemento = resolve_path(path)

    if not elemento.exists():
        raise FileError(f"No existe «{elemento}».")

    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise FileError(
            "Falta el paquete «send2trash», necesario para eliminar sin "
            'riesgo. Instala el proyecto con «pip install -e .».'
        ) from exc

    try:
        send2trash(str(elemento))
    except Exception as exc:  # noqa: BLE001 - frontera con la biblioteca
        raise FileError(
            f"No se pudo enviar «{elemento.name}» a la papelera: {exc}"
        ) from exc

    return elemento
