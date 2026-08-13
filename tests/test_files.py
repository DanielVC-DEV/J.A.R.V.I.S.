"""Pruebas de las operaciones de archivos.

Todo ocurre bajo un directorio temporal, de modo que ninguna prueba puede
tocar archivos reales del usuario ni depende de cómo esté configurado el
equipo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.computer import files
from jarvis.computer.files import (
    FileError,
    copy_item,
    create_folder,
    list_directory,
    move_item,
    read_text,
    resolve_path,
    search_files,
    write_text,
)


@pytest.fixture(autouse=True)
def _carpetas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Sustituye las carpetas personales por un árbol temporal."""
    documentos = tmp_path / "Documentos"
    descargas = tmp_path / "Descargas"
    documentos.mkdir()
    descargas.mkdir()

    monkeypatch.setattr(
        files, "known_folders", lambda: {"documentos": documentos, "descargas": descargas}
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _documentos(tmp_path: Path) -> Path:
    return tmp_path / "Documentos"


# --------------------------------------------------------------------------- #
# Resolución de rutas
# --------------------------------------------------------------------------- #


def test_spanish_folder_names_resolve(tmp_path: Path) -> None:
    """El usuario dice «documentos», no la ruta completa de su perfil."""
    assert resolve_path("documentos") == _documentos(tmp_path)
    assert resolve_path("documentos/informe.txt") == _documentos(tmp_path) / "informe.txt"


def test_folder_names_ignore_accents_and_case(tmp_path: Path) -> None:
    assert resolve_path("Documentos") == _documentos(tmp_path)
    assert resolve_path("DESCARGAS") == tmp_path / "Descargas"


def test_an_absolute_path_is_respected(tmp_path: Path) -> None:
    destino = tmp_path / "otra" / "cosa.txt"
    assert resolve_path(str(destino)) == destino


def test_quotes_around_a_path_are_stripped(tmp_path: Path) -> None:
    """El modelo entrecomilla las rutas con espacios."""
    assert resolve_path('"documentos"') == _documentos(tmp_path)


def test_an_empty_path_is_rejected() -> None:
    with pytest.raises(FileError, match="ninguna ruta"):
        resolve_path("   ")


def test_an_unknown_relative_path_falls_under_the_profile(tmp_path: Path) -> None:
    """Nunca bajo el directorio de trabajo, que para el asistente no significa nada."""
    assert resolve_path("cosa.txt") == tmp_path / "cosa.txt"


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #


def test_a_text_file_is_read(tmp_path: Path) -> None:
    (_documentos(tmp_path) / "nota.txt").write_text("Añadí una señal", encoding="utf-8")
    contenido, recortado = read_text("documentos/nota.txt")
    assert contenido == "Añadí una señal"
    assert not recortado


def test_a_long_file_is_truncated(tmp_path: Path) -> None:
    """Volcar un archivo entero agotaría el presupuesto de tokens del turno."""
    (_documentos(tmp_path) / "largo.txt").write_text("x" * 20_000, encoding="utf-8")
    contenido, recortado = read_text("documentos/largo.txt")
    assert recortado
    assert len(contenido) == files.MAX_READ_CHARS


def test_legacy_windows_encoding_is_handled(tmp_path: Path) -> None:
    """Los archivos creados en Windows no siempre están en UTF-8."""
    (_documentos(tmp_path) / "viejo.txt").write_bytes("señal".encode("cp1252"))
    contenido, _ = read_text("documentos/viejo.txt")
    assert "señal" in contenido


def test_a_binary_file_is_refused(tmp_path: Path) -> None:
    (_documentos(tmp_path) / "foto.jpg").write_bytes(b"\xff\xd8\xff")
    with pytest.raises(FileError, match="texto"):
        read_text("documentos/foto.jpg")


def test_a_missing_file_is_reported() -> None:
    with pytest.raises(FileError, match="No existe"):
        read_text("documentos/inexistente.txt")


def test_reading_a_folder_is_redirected() -> None:
    with pytest.raises(FileError, match="list_directory"):
        read_text("documentos")


# --------------------------------------------------------------------------- #
# Listado y búsqueda
# --------------------------------------------------------------------------- #


def test_folders_are_listed_first(tmp_path: Path) -> None:
    docs = _documentos(tmp_path)
    (docs / "zeta.txt").write_text("a", encoding="utf-8")
    (docs / "alfa").mkdir()

    entradas = list_directory("documentos")
    assert entradas[0].is_dir
    assert entradas[0].name == "alfa"


def test_a_pattern_filters_the_listing(tmp_path: Path) -> None:
    docs = _documentos(tmp_path)
    (docs / "informe.pdf").write_text("a", encoding="utf-8")
    (docs / "nota.txt").write_text("b", encoding="utf-8")

    nombres = [e.name for e in list_directory("documentos", "*.pdf")]
    assert nombres == ["informe.pdf"]


def test_search_descends_into_subfolders(tmp_path: Path) -> None:
    docs = _documentos(tmp_path)
    (docs / "proyecto" / "sub").mkdir(parents=True)
    (docs / "proyecto" / "sub" / "informe.pdf").write_text("a", encoding="utf-8")

    encontrados = search_files("documentos", "*.pdf")
    assert len(encontrados) == 1
    assert encontrados[0].name == "informe.pdf"


def test_listing_a_missing_folder_is_reported() -> None:
    with pytest.raises(FileError, match="No existe"):
        list_directory("documentos/inexistente")


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #


def test_a_file_is_created(tmp_path: Path) -> None:
    archivo, existia = write_text("documentos/nuevo.txt", "hola")
    assert not existia
    assert archivo.read_text(encoding="utf-8") == "hola"


def test_writing_reports_that_it_replaced_content(tmp_path: Path) -> None:
    """Distinguir crear de reemplazar permite al asistente decirlo."""
    write_text("documentos/nota.txt", "primero")
    _, existia = write_text("documentos/nota.txt", "segundo")
    assert existia
    assert (_documentos(tmp_path) / "nota.txt").read_text(encoding="utf-8") == "segundo"


def test_appending_keeps_the_previous_content(tmp_path: Path) -> None:
    write_text("documentos/diario.txt", "lunes\n")
    write_text("documentos/diario.txt", "martes\n", append=True)
    contenido = (_documentos(tmp_path) / "diario.txt").read_text(encoding="utf-8")
    assert contenido == "lunes\nmartes\n"


def test_missing_parent_folders_are_created(tmp_path: Path) -> None:
    archivo, _ = write_text("documentos/uno/dos/tres.txt", "hola")
    assert archivo.exists()


def test_a_folder_is_created(tmp_path: Path) -> None:
    carpeta, existia = create_folder("documentos/proyecto")
    assert carpeta.is_dir()
    assert not existia
    assert create_folder("documentos/proyecto")[1] is True


# --------------------------------------------------------------------------- #
# Mover y copiar
# --------------------------------------------------------------------------- #


def test_a_file_is_moved(tmp_path: Path) -> None:
    write_text("documentos/nota.txt", "hola")
    _, destino = move_item("documentos/nota.txt", "descargas/nota.txt")

    assert destino.exists()
    assert not (_documentos(tmp_path) / "nota.txt").exists()


def test_moving_into_a_folder_keeps_the_name(tmp_path: Path) -> None:
    write_text("documentos/nota.txt", "hola")
    _, destino = move_item("documentos/nota.txt", "descargas")
    assert destino.name == "nota.txt"


def test_moving_never_overwrites(tmp_path: Path) -> None:
    """Sobrescribir en silencio destruiría el trabajo del usuario."""
    write_text("documentos/nota.txt", "original")
    write_text("descargas/nota.txt", "otro")

    with pytest.raises(FileError, match="Ya existe"):
        move_item("documentos/nota.txt", "descargas/nota.txt")

    assert (tmp_path / "Descargas" / "nota.txt").read_text(encoding="utf-8") == "otro"


def test_a_file_is_copied(tmp_path: Path) -> None:
    write_text("documentos/nota.txt", "hola")
    _, destino = copy_item("documentos/nota.txt", "descargas/copia.txt")

    assert destino.read_text(encoding="utf-8") == "hola"
    assert (_documentos(tmp_path) / "nota.txt").exists()


def test_a_folder_is_copied_whole(tmp_path: Path) -> None:
    write_text("documentos/proyecto/uno.txt", "a")
    _, destino = copy_item("documentos/proyecto", "descargas/proyecto")
    assert (destino / "uno.txt").exists()


def test_moving_something_missing_is_reported() -> None:
    with pytest.raises(FileError, match="No existe"):
        move_item("documentos/fantasma.txt", "descargas")


# --------------------------------------------------------------------------- #
# Eliminación
# --------------------------------------------------------------------------- #


def test_deleting_uses_the_recycle_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nunca se borra de forma definitiva: una orden mal entendida se deshace."""
    enviados: list[str] = []
    modulo = type(
        "send2trash_postizo", (), {"send2trash": staticmethod(enviados.append)}
    )
    monkeypatch.setitem(__import__("sys").modules, "send2trash", modulo)

    write_text("documentos/borrame.txt", "hola")
    files.send_to_trash("documentos/borrame.txt")

    assert enviados
    assert "borrame.txt" in enviados[0]


def test_deleting_something_missing_is_reported() -> None:
    with pytest.raises(FileError, match="No existe"):
        files.send_to_trash("documentos/fantasma.txt")


# --------------------------------------------------------------------------- #
# Confinamiento a las carpetas permitidas
# --------------------------------------------------------------------------- #


def test_the_guard_confines_file_tools(tmp_path: Path) -> None:
    """La política vive en el guardia, no repartida por cada función."""
    from jarvis.core.registry import ToolRegistry, tool
    from jarvis.security.guard import Guard, path_jail_policy
    from jarvis.security.risk import Risk

    reg = ToolRegistry()

    @tool(risk=Risk.SAFE, category="files", registry=reg)
    def leer(path: str) -> str:
        """Lee algo.

        Args:
            path: Ruta.
        """
        return "contenido"

    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = reg.get("leer")

    assert guard.evaluate(spec, {"path": str(tmp_path / "nota.txt")}).allowed
    assert guard.evaluate(spec, {"path": "C:\\Windows\\System32\\config"}).denied
