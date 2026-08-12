"""Pruebas del resolvedor de aplicaciones.

Toda la lógica de coincidencia se ejercita contra un índice sintético, sin
tocar el menú Inicio ni el registro de Windows. Esa es precisamente la razón
de separar la indexación de la coincidencia: la parte compleja puede probarse
en cualquier sistema operativo.
"""

from __future__ import annotations

import pytest

from jarvis.computer.apps import (
    ACCEPT_THRESHOLD,
    AmbiguousApplicationError,
    Application,
    ApplicationNotFoundError,
    _normalise,
    rank,
    resolve,
    score_match,
)


@pytest.fixture
def indice() -> list[Application]:
    """Un conjunto de aplicaciones representativo de un equipo real."""
    return [
        Application("Google Chrome", "chrome.exe", aliases=("chrome",)),
        Application("Chrome Remote Desktop", "crd.exe"),
        Application("Discord", "discord.exe"),
        Application("Visual Studio Code", "code.exe", aliases=("code",)),
        Application("Bloc de notas", "notepad.exe", aliases=("notepad", "bloc")),
        Application("Spotify", "spotify.exe"),
        Application("Steam", "steam.exe"),
        Application("Configuración", "ms-settings:", aliases=("settings",)),
        Application("Epic Games Launcher", "epic.exe"),
    ]


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #


def test_normalisation_removes_accents_and_punctuation() -> None:
    assert _normalise("Configuración") == "configuracion"
    assert _normalise("  VISUAL   Studio  ") == "visual studio"
    assert _normalise("Adobe® Photoshop™") == "adobe photoshop"
    assert _normalise("Símbolo del sistema") == "simbolo del sistema"


def test_accented_and_unaccented_queries_are_equivalent(
    indice: list[Application],
) -> None:
    """La transcripción de voz produce ambas formas indistintamente."""
    assert resolve("configuración", indice).name == "Configuración"
    assert resolve("configuracion", indice).name == "Configuración"


# --------------------------------------------------------------------------- #
# Coincidencia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("consulta", "esperada"),
    [
        ("chrome", "Google Chrome"),
        ("google chrome", "Google Chrome"),
        ("discord", "Discord"),
        ("spotify", "Spotify"),
        ("steam", "Steam"),
        ("code", "Visual Studio Code"),
        ("visual studio", "Visual Studio Code"),
        ("epic games", "Epic Games Launcher"),
        ("epic", "Epic Games Launcher"),
        ("bloc de notas", "Bloc de notas"),
    ],
)
def test_direct_names_resolve(
    indice: list[Application], consulta: str, esperada: str
) -> None:
    assert resolve(consulta, indice).name == esperada


@pytest.mark.parametrize(
    ("consulta", "esperada"),
    [
        ("crome", "Google Chrome"),
        ("dicord", "Discord"),
        ("stem", "Steam"),
        ("spotifi", "Spotify"),
    ],
)
def test_typos_still_resolve(
    indice: list[Application], consulta: str, esperada: str
) -> None:
    """La transcripción de voz comete erratas con frecuencia."""
    assert resolve(consulta, indice).name == esperada


def test_abbreviations_resolve(indice: list[Application]) -> None:
    """«vs code» no aparece literalmente en «Visual Studio Code»."""
    assert resolve("vs code", indice).name == "Visual Studio Code"


def test_aliases_resolve(indice: list[Application]) -> None:
    """Un usuario de Windows en español puede decir «notepad»."""
    assert resolve("notepad", indice).name == "Bloc de notas"
    assert resolve("settings", indice).name == "Configuración"


def test_ranking_is_ordered_by_score(indice: list[Application]) -> None:
    puntuaciones = [m.score for m in rank("chrome", indice)]
    assert puntuaciones == sorted(puntuaciones, reverse=True)


def test_ranking_omits_irrelevant_entries(indice: list[Application]) -> None:
    for match in rank("chrome", indice):
        assert match.score > 0


# --------------------------------------------------------------------------- #
# Casos en los que no se debe adivinar
# --------------------------------------------------------------------------- #


def test_unknown_application_is_reported(indice: list[Application]) -> None:
    """Más vale admitir que no se encontró nada que abrir algo al azar."""
    with pytest.raises(ApplicationNotFoundError, match="xyzabc"):
        resolve("xyzabc", indice)


def test_empty_query_is_rejected(indice: list[Application]) -> None:
    with pytest.raises(ApplicationNotFoundError):
        resolve("   ", indice)


def test_equivalent_candidates_raise_ambiguity() -> None:
    """Dos aplicaciones igual de plausibles deben provocar una pregunta."""
    indice = [
        Application("Microsoft Office Word", "word.exe"),
        Application("Microsoft Office Excel", "excel.exe"),
    ]

    with pytest.raises(AmbiguousApplicationError) as excinfo:
        resolve("microsoft office", indice)

    assert len(excinfo.value.candidates) == 2
    assert excinfo.value.query == "microsoft office"
    assert "Word" in str(excinfo.value)
    assert "Excel" in str(excinfo.value)


def test_a_clear_winner_does_not_raise_ambiguity(indice: list[Application]) -> None:
    """«chrome» es un alias exacto: no debe preguntarse por Chrome Remote."""
    assert resolve("chrome", indice).name == "Google Chrome"


def test_ambiguity_lists_at_most_four_candidates() -> None:
    indice = [Application(f"Editor Alfa {i}", f"e{i}.exe") for i in range(8)]
    with pytest.raises(AmbiguousApplicationError) as excinfo:
        resolve("editor alfa", indice)
    assert len(excinfo.value.candidates) <= 4


# --------------------------------------------------------------------------- #
# Puntuación
# --------------------------------------------------------------------------- #


def test_exact_match_scores_maximum() -> None:
    assert score_match("discord", Application("Discord", "d")) == 100.0


def test_alias_match_scores_maximum() -> None:
    app = Application("Bloc de notas", "n", aliases=("notepad",))
    assert score_match("notepad", app) == 100.0


def test_unrelated_query_falls_below_threshold() -> None:
    app = Application("Google Chrome", "c")
    assert score_match("calculadora", app) < ACCEPT_THRESHOLD


def test_shorter_name_wins_a_tie() -> None:
    """«Google Chrome» debe anteponerse a «Chrome Remote Desktop»."""
    corto = Application("Google Chrome", "c")
    largo = Application("Chrome Remote Desktop", "c")
    assert score_match("chrome", corto) > score_match("chrome", largo)


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #


def test_search_terms_include_name_and_aliases() -> None:
    app = Application("Bloc de notas", "n", aliases=("notepad", "bloc"))
    assert app.search_terms == ("Bloc de notas", "notepad", "bloc")
