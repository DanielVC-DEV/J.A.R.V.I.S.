"""Herramientas de archivos y carpetas.

Envoltorios sobre ``jarvis.computer.files``. Todas se declaran en la categoría
``files``, que es la que el guardia de seguridad confina a las carpetas
personales del usuario: cualquier ruta fuera de ellas se rechaza antes de que
estas funciones lleguen a ejecutarse.

Las descripciones son deliberadamente escuetas. El catálogo entero se reenvía
al modelo en cada vuelta del bucle, y con una capa gratuita el presupuesto de
tokens por minuto se agota antes de lo que parece.
"""

from __future__ import annotations

from jarvis.computer import files
from jarvis.core.registry import tool
from jarvis.security.risk import Risk

__all__ = [
    "copy_file",
    "create_folder",
    "delete_file",
    "list_folder",
    "move_file",
    "open_folder",
    "read_file",
    "search_files",
    "write_file",
]


@tool(risk=Risk.SAFE, category="files")
def list_folder(path: str = "documentos", pattern: str = "*") -> str:
    """Enumera el contenido de una carpeta.

    Args:
        path: Carpeta. Admite «documentos», «descargas», «escritorio» o una
            ruta completa.
        pattern: Filtro de nombre, como «*.pdf».
    """
    entradas = files.list_directory(path, pattern)

    if not entradas:
        return f"«{path}» está vacía o nada coincide con «{pattern}»."

    lineas = "\n".join(f"· {e.describe()}" for e in entradas)
    return f"Contenido de {files.resolve_path(path)}:\n{lineas}"


@tool(risk=Risk.SAFE, category="files")
def open_folder(path: str) -> str:
    """Abre una carpeta en el Explorador, o un archivo con su programa.

    Úsala cuando el usuario nombre una ruta o un proyecto. Para abrir una
    aplicación instalada, usa open_program.

    Args:
        path: Carpeta o archivo, como «D:/Proyectos» o «documentos/informe.pdf».
    """
    destino = files.open_in_explorer(path)
    return f"Abierto {destino}."


@tool(risk=Risk.SAFE, category="files")
def read_file(path: str) -> str:
    """Lee un archivo de texto. Solo texto plano, no documentos ni binarios.

    Args:
        path: Archivo a leer.
    """
    contenido, recortado = files.read_text(path)

    if not contenido.strip():
        return f"«{path}» está vacío."

    aviso = (
        f"\n\n[Recortado a los primeros {files.MAX_READ_CHARS} caracteres.]"
        if recortado
        else ""
    )
    return f"Contenido de {path}:\n\n{contenido}{aviso}"


@tool(risk=Risk.SAFE, category="files")
def search_files(pattern: str, folder: str = "documentos") -> str:
    """Busca archivos por nombre dentro de una carpeta y sus subcarpetas.

    Args:
        pattern: Patrón de nombre, como «*.pdf» o «informe*».
        folder: Carpeta desde la que buscar.
    """
    encontrados = files.search_files(folder, pattern)

    if not encontrados:
        return f"No encontré nada que coincida con «{pattern}» en {folder}."

    lineas = "\n".join(f"· {e.path}" for e in encontrados)
    return f"Encontrados {len(encontrados)} archivos:\n{lineas}"


@tool(risk=Risk.SAFE, category="files")
def create_folder(path: str) -> str:
    """Crea una carpeta.

    Args:
        path: Carpeta a crear.
    """
    carpeta, existia = files.create_folder(path)
    if existia:
        return f"La carpeta {carpeta} ya existía."
    return f"Carpeta creada: {carpeta}"


@tool(risk=Risk.CONFIRM, category="files")
def write_file(path: str, content: str, append: bool = False) -> str:
    """Escribe texto en un archivo, creándolo si no existe.

    Sin append, reemplaza el contenido anterior.

    Args:
        path: Archivo de destino.
        content: Texto a escribir.
        append: Verdadero para añadir al final en lugar de reemplazar.
    """
    archivo, existia = files.write_text(path, content, append=append)

    if append:
        return f"Añadidos {len(content)} caracteres a {archivo}."
    if existia:
        return f"Reemplazado el contenido de {archivo} ({len(content)} caracteres)."
    return f"Creado {archivo} con {len(content)} caracteres."


@tool(risk=Risk.CONFIRM, category="files")
def move_file(source: str, destination: str) -> str:
    """Mueve o renombra un archivo o una carpeta.

    Args:
        source: Elemento a mover.
        destination: Destino. Si es una carpeta, se mueve dentro.
    """
    _, destino = files.move_item(source, destination)
    return f"Movido a {destino}."


@tool(risk=Risk.CONFIRM, category="files")
def copy_file(source: str, destination: str) -> str:
    """Copia un archivo o una carpeta.

    Args:
        source: Elemento a copiar.
        destination: Destino. Si es una carpeta, se copia dentro.
    """
    _, destino = files.copy_item(source, destination)
    return f"Copiado a {destino}."


@tool(risk=Risk.CONFIRM, category="files")
def delete_file(path: str) -> str:
    """Envía un archivo o carpeta a la papelera. No borra definitivamente.

    Args:
        path: Elemento a eliminar.
    """
    elemento = files.send_to_trash(path)
    return f"{elemento.name} enviado a la papelera. Puedes restaurarlo desde allí."
