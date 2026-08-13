"""Pruebas de la coincidencia común, las ventanas y la entrada de teclado.

Todo lo que depende de Windows se sustituye por postizos; lo que se comprueba
aquí es la lógica que decide qué hacer, que es donde están los errores.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.computer import matching
from jarvis.computer.input import (
    MAX_TEXT_LENGTH,
    InputError,
    normalise_keys,
)
from jarvis.computer.windows import Window, WindowNotFoundError, resolve_window


# --------------------------------------------------------------------------- #
# Coincidencia común
# --------------------------------------------------------------------------- #


class _Cosa:
    def __init__(self, nombre: str, *alias: str) -> None:
        self.nombre = nombre
        self.terminos = (nombre, *alias)


def _resolver(consulta: str, nombres: list[_Cosa]) -> _Cosa:
    return matching.resolve(consulta, nombres, lambda c: c.terminos, what="cosa")


def test_the_shared_matcher_handles_typos() -> None:
    cosas = [_Cosa("Google Chrome"), _Cosa("Discord"), _Cosa("Spotify")]
    assert _resolver("crome", cosas).nombre == "Google Chrome"
    assert _resolver("dicord", cosas).nombre == "Discord"


def test_the_shared_matcher_handles_abbreviations() -> None:
    cosas = [_Cosa("Visual Studio Code"), _Cosa("Discord")]
    assert _resolver("vs code", cosas).nombre == "Visual Studio Code"


def test_the_shared_matcher_reports_ambiguity() -> None:
    cosas = [_Cosa("Microsoft Office Word"), _Cosa("Microsoft Office Excel")]
    with pytest.raises(matching.AmbiguousMatchError) as excinfo:
        _resolver("microsoft office", cosas)
    assert len(excinfo.value.candidates) == 2
    assert "Word" in excinfo.value.names[0] or "Word" in excinfo.value.names[1]


def test_the_shared_matcher_reports_no_match() -> None:
    with pytest.raises(matching.NoMatchError, match="cosa"):
        _resolver("xyzabc", [_Cosa("Discord")])


def test_normalisation_is_shared() -> None:
    assert matching.normalise("Configuración") == "configuracion"
    assert matching.normalise("Adobe® Photoshop™") == "adobe photoshop"


# --------------------------------------------------------------------------- #
# Ventanas
# --------------------------------------------------------------------------- #


def _ventana(titulo: str, proceso: str = "", minimizada: bool = False) -> Window:
    return Window(handle=1, title=titulo, process=proceso, minimised=minimizada)


def test_a_window_is_found_by_its_application_name() -> None:
    """El usuario dice «Chrome»; el título real es el de la página abierta."""
    abiertas = [
        _ventana("Documento sin título - Google Chrome", "chrome.exe"),
        _ventana("Discord", "Discord.exe"),
    ]
    encontrada = resolve_window("chrome", abiertas)
    assert "Chrome" in encontrada.title


def test_a_window_is_found_by_part_of_its_title() -> None:
    abiertas = [
        _ventana("informe-anual.docx - Word", "WINWORD.EXE"),
        _ventana("Discord", "Discord.exe"),
    ]
    assert "Word" in resolve_window("informe anual", abiertas).title


def test_an_absent_window_is_reported() -> None:
    with pytest.raises(WindowNotFoundError, match="ventana"):
        resolve_window("photoshop", [_ventana("Discord", "Discord.exe")])


def test_the_process_name_widens_the_search() -> None:
    """Una ventana con título opaco sigue siendo localizable por su programa."""
    abiertas = [_ventana("Sin título 1", "notepad.exe")]
    assert resolve_window("notepad", abiertas).process == "notepad.exe"


def test_search_terms_include_the_process_without_extension() -> None:
    ventana = _ventana("Sin título", "notepad.exe")
    assert "notepad" in ventana.search_terms


def test_a_window_without_a_process_still_has_terms() -> None:
    assert _ventana("Solo título").search_terms == ("Solo título",)


# --------------------------------------------------------------------------- #
# Teclado
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("escrito", "esperado"),
    [
        ("ctrl+c", ["ctrl", "c"]),
        ("ctrl + c", ["ctrl", "c"]),
        ("ctrl c", ["ctrl", "c"]),
        ("CTRL+C", ["ctrl", "c"]),
        ("alt+tab", ["alt", "tab"]),
        ("enter", ["enter"]),
        ("ctrl+shift+esc", ["ctrl", "shift", "esc"]),
    ],
)
def test_key_combinations_are_interpreted(escrito: str, esperado: list[str]) -> None:
    assert normalise_keys(escrito) == esperado


@pytest.mark.parametrize(
    ("espanol", "esperado"),
    [
        ("control+c", ["ctrl", "c"]),
        ("intro", ["enter"]),
        ("mayus+tabulador", ["shift", "tab"]),
        ("suprimir", ["delete"]),
        ("espacio", ["space"]),
        ("windows+d", ["win", "d"]),
    ],
)
def test_spanish_key_names_are_accepted(espanol: str, esperado: list[str]) -> None:
    """El modelo responde en español y nombrará las teclas en español."""
    assert normalise_keys(espanol) == esperado


def test_an_empty_combination_is_rejected() -> None:
    with pytest.raises(InputError, match="ninguna tecla"):
        normalise_keys("   ")


# --------------------------------------------------------------------------- #
# Escritura de texto
# --------------------------------------------------------------------------- #


class _PortapapelesPostizo:
    def __init__(self, inicial: str = "lo que el usuario tenía copiado") -> None:
        self.contenido = inicial
        self.historial: list[str] = []

    def copy(self, texto: str) -> None:
        self.contenido = texto
        self.historial.append(texto)

    def paste(self) -> str:
        return self.contenido


class _AutomatizacionPostiza:
    def __init__(self) -> None:
        self.combinaciones: list[tuple[str, ...]] = []
        self.PAUSE = 0.0
        self.FAILSAFE = True

    def hotkey(self, *teclas: str) -> None:
        self.combinaciones.append(teclas)


def _preparar(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    from jarvis.computer import input as entrada

    portapapeles = _PortapapelesPostizo()
    automatizacion = _AutomatizacionPostiza()
    monkeypatch.setattr(entrada, "_clipboard", lambda: portapapeles)
    monkeypatch.setattr(entrada, "_pyautogui", lambda: automatizacion)
    monkeypatch.setattr(entrada, "_CLIPBOARD_SETTLE", 0.0)
    return portapapeles, automatizacion


def test_text_is_written_through_the_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simular pulsaciones no reproduce acentos ni eñes de forma fiable."""
    from jarvis.computer.input import type_text

    portapapeles, automatizacion = _preparar(monkeypatch)

    escritos = type_text("Añadí una señal de peligro")

    assert escritos == len("Añadí una señal de peligro")
    assert "Añadí una señal de peligro" in portapapeles.historial
    assert ("ctrl", "v") in automatizacion.combinaciones


def test_the_previous_clipboard_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perder lo que el usuario tenía copiado sería inaceptable."""
    from jarvis.computer.input import type_text

    portapapeles, _ = _preparar(monkeypatch)
    original = portapapeles.contenido

    type_text("texto nuevo")

    assert portapapeles.contenido == original


def test_the_clipboard_is_restored_even_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.computer import input as entrada

    portapapeles = _PortapapelesPostizo()
    original = portapapeles.contenido

    class Rota(_AutomatizacionPostiza):
        def hotkey(self, *teclas: str) -> None:
            raise RuntimeError("el escritorio no responde")

    monkeypatch.setattr(entrada, "_clipboard", lambda: portapapeles)
    monkeypatch.setattr(entrada, "_pyautogui", lambda: Rota())
    monkeypatch.setattr(entrada, "_CLIPBOARD_SETTLE", 0.0)

    with pytest.raises(InputError):
        entrada.type_text("algo")

    assert portapapeles.contenido == original


def test_an_empty_text_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.computer.input import type_text

    _, automatizacion = _preparar(monkeypatch)
    assert type_text("") == 0
    assert automatizacion.combinaciones == []


def test_an_excessive_text_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un error del modelo no debe volcar miles de caracteres en una ventana."""
    from jarvis.computer.input import type_text

    _preparar(monkeypatch)
    with pytest.raises(InputError, match="límite"):
        type_text("x" * (MAX_TEXT_LENGTH + 1))
