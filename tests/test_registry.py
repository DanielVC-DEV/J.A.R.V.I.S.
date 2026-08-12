"""Pruebas del registro de herramientas.

Estas pruebas no realizan ninguna llamada de red ni requieren clave de API:
verifican de forma aislada la generación de esquemas, la validación de
argumentos y el tratamiento de errores.
"""

from __future__ import annotations

import pytest

from jarvis.core.registry import (
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
    ToolRegistry,
    tool,
)
from jarvis.security.risk import Risk


@pytest.fixture
def reg() -> ToolRegistry:
    """Registro aislado, para no contaminar el catálogo global."""
    return ToolRegistry()


# --------------------------------------------------------------------------- #
# Generación de esquemas
# --------------------------------------------------------------------------- #


def test_schema_generated_from_signature_and_docstring(reg: ToolRegistry) -> None:
    @tool(risk=Risk.SAFE, category="system", registry=reg)
    def set_volume(level: int) -> str:
        """Ajusta el volumen maestro del sistema.

        Args:
            level: Nivel de volumen entre 0 y 100.
        """
        return f"Volumen al {level}%."

    spec = reg.get("set_volume")

    assert spec.description == "Ajusta el volumen maestro del sistema."
    assert spec.risk is Risk.SAFE
    assert spec.category == "system"
    assert spec.parameters["properties"]["level"]["type"] == "integer"
    assert (
        spec.parameters["properties"]["level"]["description"]
        == "Nivel de volumen entre 0 y 100."
    )
    assert spec.parameters["required"] == ["level"]


def test_schema_omits_titles_to_save_tokens(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def echo(text: str) -> str:
        """Repite el texto recibido.

        Args:
            text: Texto a repetir.
        """
        return text

    schema = reg.get("echo").parameters
    assert "title" not in schema
    assert "title" not in schema["properties"]["text"]


def test_optional_parameters_are_not_required(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def search(query: str, limit: int = 5) -> str:
        """Busca información en la web.

        Args:
            query: Términos de búsqueda.
            limit: Número máximo de resultados.
        """
        return f"{query} ({limit})"

    schema = reg.get("search").parameters
    assert schema["required"] == ["query"]
    assert schema["properties"]["limit"]["default"] == 5


def test_multiline_parameter_description_is_joined(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def note(text: str) -> str:
        """Guarda una nota.

        Args:
            text: Contenido de la nota, que puede
                ocupar varias líneas.
        """
        return text

    description = reg.get("note").parameters["properties"]["text"]["description"]
    assert description == "Contenido de la nota, que puede ocupar varias líneas."


def test_returns_section_is_excluded_from_description(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def uptime() -> str:
        """Informa del tiempo que lleva encendido el equipo.

        Returns:
            Una descripción legible del tiempo transcurrido.
        """
        return "3 horas"

    spec = reg.get("uptime")
    assert spec.description == "Informa del tiempo que lleva encendido el equipo."
    assert spec.parameters["properties"] == {}


def test_to_schema_matches_api_format(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def ping() -> str:
        """Comprueba que el asistente responde."""
        return "pong"

    schema = reg.get("ping").to_schema()
    assert set(schema) == {"name", "description", "input_schema"}
    assert schema["name"] == "ping"


# --------------------------------------------------------------------------- #
# Declaración incorrecta
# --------------------------------------------------------------------------- #


def test_tool_without_docstring_is_rejected(reg: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="docstring"):

        @tool(registry=reg)
        def broken(value: int) -> str:
            return str(value)


def test_untyped_parameter_is_rejected(reg: ToolRegistry) -> None:
    with pytest.raises(TypeError, match="anotación de tipo"):

        @tool(registry=reg)
        def broken(value) -> str:  # type: ignore[no-untyped-def]
            """Herramienta con un parámetro sin anotar."""
            return str(value)


def test_varargs_are_rejected(reg: ToolRegistry) -> None:
    with pytest.raises(TypeError, match=r"\*args"):

        @tool(registry=reg)
        def broken(*values: int) -> str:
            """Herramienta con argumentos variables."""
            return str(values)


def test_duplicate_names_are_rejected(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def duplicated() -> str:
        """Primera versión."""
        return "a"

    with pytest.raises(ToolAlreadyRegisteredError):

        @tool(name="duplicated", registry=reg)
        def other() -> str:
            """Segunda versión."""
            return "b"


def test_risk_defaults_to_confirm(reg: ToolRegistry) -> None:
    """Una herramienta sin riesgo declarado debe pedir confirmación."""

    @tool(registry=reg)
    def undeclared() -> str:
        """Herramienta cuyo riesgo se olvidó declarar."""
        return "ok"

    assert reg.get("undeclared").risk is Risk.CONFIRM


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #


def test_execute_returns_success(reg: ToolRegistry) -> None:
    @tool(risk=Risk.SAFE, registry=reg)
    def add(a: int, b: int) -> int:
        """Suma dos números.

        Args:
            a: Primer sumando.
            b: Segundo sumando.
        """
        return a + b

    result = reg.execute("add", {"a": 2, "b": 3})
    assert result.ok
    assert result.content == "5"
    assert result.error is None


def test_execute_coerces_compatible_types(reg: ToolRegistry) -> None:
    """El modelo a veces envía números como cadenas; Pydantic los convierte."""

    @tool(registry=reg)
    def double(value: int) -> int:
        """Duplica un número.

        Args:
            value: Número a duplicar.
        """
        return value * 2

    assert reg.execute("double", {"value": "21"}).content == "42"


def test_execute_reports_invalid_arguments(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def double(value: int) -> int:
        """Duplica un número.

        Args:
            value: Número a duplicar.
        """
        return value * 2

    result = reg.execute("double", {"value": "no soy un número"})
    assert not result.ok
    assert "value" in result.content


def test_execute_reports_missing_arguments(reg: ToolRegistry) -> None:
    @tool(registry=reg)
    def greet(name: str) -> str:
        """Saluda a alguien.

        Args:
            name: Nombre de la persona.
        """
        return f"Hola, {name}."

    result = reg.execute("greet", {})
    assert not result.ok
    assert "name" in result.content


def test_execute_captures_tool_exceptions(reg: ToolRegistry) -> None:
    """Un fallo interno nunca debe propagarse hasta el orquestador."""

    @tool(registry=reg)
    def explode() -> str:
        """Herramienta que siempre falla."""
        raise RuntimeError("el dispositivo de audio no responde")

    result = reg.execute("explode", {})
    assert not result.ok
    assert "RuntimeError" in result.content
    assert "el dispositivo de audio no responde" in result.content


def test_execute_reports_unknown_tool(reg: ToolRegistry) -> None:
    result = reg.execute("inexistente", {})
    assert not result.ok
    assert "inexistente" in result.content


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #


def test_catalogue_is_filterable_and_sorted(reg: ToolRegistry) -> None:
    @tool(category="system", registry=reg)
    def volume() -> str:
        """Consulta el volumen."""
        return "70%"

    @tool(category="files", registry=reg)
    def read() -> str:
        """Lee un archivo."""
        return "contenido"

    assert reg.names() == ["read", "volume"]
    assert len(reg) == 2
    assert "volume" in reg
    assert [s.name for s in reg.all(category="system")] == ["volume"]
    assert len(reg.schemas(category="files")) == 1


def test_unknown_tool_lookup_raises(reg: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError):
        reg.get("inexistente")


def test_decorated_function_remains_directly_callable(reg: ToolRegistry) -> None:
    """El decorador no debe envolver la función: facilita probarla aislada."""

    @tool(risk=Risk.SAFE, registry=reg)
    def add(a: int, b: int) -> int:
        """Suma dos números.

        Args:
            a: Primer sumando.
            b: Segundo sumando.
        """
        return a + b

    assert add(2, 3) == 5
    assert add.__tool_spec__.risk is Risk.SAFE
