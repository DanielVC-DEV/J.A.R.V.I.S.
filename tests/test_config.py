"""Pruebas de la carga de configuración.

Se ejercitan sin tocar el almacén de credenciales real ni requerir una clave
de API válida.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import settings as settings_module
from jarvis.config.paths import config_file, data_dir
from jarvis.config.settings import (
    ConfigurationError,
    Settings,
    load_settings,
    save_settings,
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aísla cada prueba del entorno real y del archivo .env del proyecto.

    El cambio de directorio de trabajo es imprescindible: sin él, en cuanto
    exista un ``.env`` con una clave real, ``load_settings`` la tomaría y las
    pruebas fallarían por un motivo desconcertante.
    """
    for name in ("API_KEY", "MODEL", "MAX_TOKENS", "MAX_TOOL_ITERATIONS", "LOG_LEVEL"):
        monkeypatch.delenv(f"JARVIS_{name}", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings_module, "_api_key_from_keyring", lambda: None)
    load_settings.cache_clear()


def _build(**overrides: object) -> Settings:
    """Construye una configuración sin leer el archivo .env del proyecto."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Valores por omisión y validación
# --------------------------------------------------------------------------- #


def test_defaults_are_sensible() -> None:
    settings = _build()
    assert settings.resolved_model()
    assert settings.max_tokens >= 64
    assert 1 <= settings.max_tool_iterations <= 25
    assert settings.log_level == "INFO"


def test_log_level_is_case_insensitive() -> None:
    assert _build(log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="Nivel de registro"):
        _build(log_level="verboso")


def test_a_blank_model_falls_back_to_the_default() -> None:
    """Copiar la plantilla .env no debe dejar el asistente sin arrancar."""
    assert _build(model="   ").resolved_model() == _build().resolved_model()


def test_blocked_patterns_are_split_and_trimmed() -> None:
    settings = _build(blocked_patterns=" contraseñas.txt ; secretos ")
    assert settings.resolved_blocked_patterns() == ("contraseñas.txt", "secretos")


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Proveedor no reconocido"):
        _build(provider="inventado")


def test_tool_iteration_limit_is_bounded() -> None:
    with pytest.raises(ValueError):
        _build(max_tool_iterations=0)
    with pytest.raises(ValueError):
        _build(max_tool_iterations=99)


# --------------------------------------------------------------------------- #
# Tratamiento de la clave de API
# --------------------------------------------------------------------------- #


def test_missing_api_key_raises_actionable_error() -> None:
    settings = _build()
    assert not settings.has_api_key()
    with pytest.raises(ConfigurationError, match="clave de API"):
        settings.require_api_key()


def test_api_key_is_returned_when_present() -> None:
    settings = _build(api_key="secreto-de-prueba")
    assert settings.has_api_key()
    assert settings.require_api_key() == "secreto-de-prueba"


def test_repr_never_leaks_the_api_key() -> None:
    """La clave no debe aparecer en trazas, registros ni mensajes de error."""
    settings = _build(api_key="secreto-de-prueba")
    assert "secreto-de-prueba" not in repr(settings)
    assert "secreto-de-prueba" not in str(settings.api_key)
    assert "configurada" in repr(settings)


def test_environment_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_MODEL", "modelo-de-prueba")
    monkeypatch.setenv("JARVIS_MAX_TOKENS", "256")
    settings = _build()
    assert settings.model == "modelo-de-prueba"
    assert settings.max_tokens == 256


def test_keyring_is_used_when_environment_has_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module, "_api_key_from_keyring", lambda: "clave-del-almacen"
    )
    load_settings.cache_clear()
    assert load_settings().require_api_key() == "clave-del-almacen"


def test_environment_takes_precedence_over_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_API_KEY", "clave-del-entorno")
    monkeypatch.setattr(
        settings_module, "_api_key_from_keyring", lambda: "clave-del-almacen"
    )
    load_settings.cache_clear()
    assert load_settings().require_api_key() == "clave-del-entorno"


def test_settings_are_cached() -> None:
    assert load_settings() is load_settings()


# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #


def test_data_dir_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    data_dir.cache_clear()
    try:
        assert data_dir() == tmp_path.resolve()
    finally:
        data_dir.cache_clear()


# --------------------------------------------------------------------------- #
# Preferencias guardadas desde la pantalla de configuración
# --------------------------------------------------------------------------- #


def test_no_saved_preferences_file_is_not_an_error() -> None:
    """La primera vez que arranca la aplicación no existe «settings.json»."""
    assert not config_file().exists()
    assert _build().model == ""


def test_saved_preferences_are_read_back() -> None:
    save_settings({"model": "modelo-guardado", "max_tokens": 512})
    assert config_file().exists()

    settings = _build()
    assert settings.model == "modelo-guardado"
    assert settings.max_tokens == 512


def test_environment_takes_precedence_over_saved_preferences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_settings({"model": "modelo-guardado"})
    monkeypatch.setenv("JARVIS_MODEL", "modelo-del-entorno")
    assert _build().model == "modelo-del-entorno"


def test_saved_preferences_never_include_the_api_key() -> None:
    """La clave solo se guarda cifrada en el almacén, nunca en el JSON."""
    save_settings({"model": "x", "api_key": "no-debe-guardarse"})
    contenido = config_file().read_text(encoding="utf-8")
    assert "no-debe-guardarse" not in contenido
    assert not _build().has_api_key()


def test_saving_preferences_overwrites_previous_values() -> None:
    save_settings({"model": "primero", "max_tokens": 128})
    save_settings({"model": "segundo"})
    settings = _build()
    assert settings.model == "segundo"
    assert settings.max_tokens == 1024  # de vuelta al valor por omisión


def test_saving_preferences_clears_the_settings_cache() -> None:
    load_settings()
    save_settings({"model": "recien-guardado"})
    assert load_settings().model == "recien-guardado"
