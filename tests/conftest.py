"""Configuración común de las pruebas.

Aísla los datos de la aplicación (``%APPDATA%\\JARVIS`` en un equipo real) en
un directorio temporal propio de cada prueba. Sin esto, cualquier prueba que
construya ``Settings`` leería —o, con la pantalla de configuración, incluso
sobrescribiría— las preferencias reales de quien ejecuta la batería en su
propio equipo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config.paths import data_dir


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirige ``data_dir()`` a un directorio temporal durante cada prueba."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    data_dir.cache_clear()
    yield
    data_dir.cache_clear()
