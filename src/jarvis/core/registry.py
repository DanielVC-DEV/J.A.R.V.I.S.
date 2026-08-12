"""Registro de herramientas del asistente.

Este módulo es la pieza central del sistema de herramientas. A partir de una
única declaración —una función Python anotada con ``@tool``— deriva
automáticamente:

* el esquema JSON que se envía al modelo de lenguaje,
* la validación de los argumentos que el modelo produce,
* la clasificación de riesgo que consumirá el guardia de seguridad.

De este modo nunca se escribe un esquema JSON a mano y resulta imposible que
la descripción vista por el modelo diverja de la implementación real.

Ejemplo de uso::

    @tool(risk=Risk.SAFE, category="system")
    def set_volume(level: int) -> str:
        '''Ajusta el volumen maestro del sistema.

        Args:
            level: Nivel de volumen entre 0 y 100.
        '''
        return f"Volumen ajustado al {level}%."
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import BaseModel, Field, ValidationError, create_model

from jarvis.security.risk import Risk

__all__ = [
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "registry",
    "tool",
]


# --------------------------------------------------------------------------- #
# Excepciones
# --------------------------------------------------------------------------- #


class ToolAlreadyRegisteredError(RuntimeError):
    """Se intentó registrar dos herramientas con el mismo nombre."""


class ToolNotFoundError(KeyError):
    """Se solicitó una herramienta que no existe en el registro."""


# --------------------------------------------------------------------------- #
# Análisis de docstrings
# --------------------------------------------------------------------------- #

_ARGS_SECTION = re.compile(r"^\s*(Args|Arguments|Parámetros|Params)\s*:\s*$", re.M)
_OTHER_SECTION = re.compile(
    r"^\s*(Returns|Devuelve|Raises|Lanza|Example|Ejemplo|Note|Nota)s?\s*:\s*$", re.M
)
_ARG_LINE = re.compile(r"^\s*(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


def _parse_docstring(docstring: str | None) -> tuple[str, dict[str, str]]:
    """Extrae la descripción y las descripciones de parámetros de un docstring.

    Reconoce el formato de estilo Google, en el que los parámetros se declaran
    bajo una sección ``Args:`` con la forma ``nombre: descripción``.

    Args:
        docstring: Contenido del docstring, tal cual lo expone Python.

    Returns:
        Una tupla ``(descripción, {parámetro: descripción})``. La descripción
        es el texto previo a la sección ``Args:``, con la indentación
        normalizada.
    """
    if not docstring:
        return "", {}

    text = inspect.cleandoc(docstring)

    args_match = _ARGS_SECTION.search(text)
    other_match = _OTHER_SECTION.search(text)

    # La descripción termina en la primera sección estructurada que aparezca,
    # sea cual sea. De lo contrario, un docstring con «Returns:» pero sin
    # «Args:» arrastraría esa sección hasta la descripción vista por el modelo.
    boundaries = [m.start() for m in (args_match, other_match) if m is not None]
    description = (text[: min(boundaries)] if boundaries else text).strip()

    if args_match is None:
        return description, {}

    remainder = text[args_match.end() :]

    # La sección de argumentos termina donde empieza cualquier otra sección.
    end = _OTHER_SECTION.search(remainder)
    if end is not None:
        remainder = remainder[: end.start()]

    params: dict[str, str] = {}
    current: str | None = None

    for line in remainder.splitlines():
        if not line.strip():
            continue
        arg_match = _ARG_LINE.match(line)
        if arg_match is not None:
            current = arg_match.group(1).lstrip("*")
            params[current] = arg_match.group(2).strip()
        elif current is not None:
            # Continuación de una descripción repartida en varias líneas.
            params[current] = f"{params[current]} {line.strip()}"

    return description, params


# --------------------------------------------------------------------------- #
# Generación de esquemas
# --------------------------------------------------------------------------- #


def _strip_titles(schema: Any) -> Any:
    """Elimina recursivamente las claves ``title`` de un esquema JSON.

    Pydantic las genera automáticamente a partir de los nombres de campo. No
    aportan información al modelo y consumen tokens en cada petición.
    """
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(item) for item in schema]
    return schema


def _build_args_model(func: Callable[..., Any]) -> type[BaseModel]:
    """Construye un modelo Pydantic que representa los argumentos de ``func``.

    El modelo cumple una doble función: genera el esquema JSON para el modelo
    de lenguaje y valida los argumentos que este produce antes de ejecutar
    nada.

    Args:
        func: Función a introspeccionar. Todos sus parámetros deben estar
            anotados con tipos.

    Returns:
        Una subclase de ``BaseModel`` con un campo por cada parámetro.

    Raises:
        TypeError: Si algún parámetro carece de anotación de tipo.
    """
    signature = inspect.signature(func)
    hints = get_type_hints(func)
    _, param_docs = _parse_docstring(func.__doc__)

    fields: dict[str, Any] = {}

    for name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                f"La herramienta '{func.__name__}' no puede declarar *args ni "
                "**kwargs: el esquema enviado al modelo debe ser explícito."
            )

        if name not in hints:
            raise TypeError(
                f"El parámetro '{name}' de la herramienta '{func.__name__}' "
                "carece de anotación de tipo. La anotación es obligatoria "
                "porque de ella se deriva el esquema JSON."
            )

        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[name] = (
            hints[name],
            Field(default, description=param_docs.get(name)),
        )

    return create_model(f"{func.__name__.title().replace('_', '')}Args", **fields)


# --------------------------------------------------------------------------- #
# Especificación y resultado
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Descripción completa de una herramienta registrada."""

    name: str
    """Identificador único, tal como lo invocará el modelo."""

    description: str
    """Texto que el modelo lee para decidir cuándo usar la herramienta."""

    parameters: dict[str, Any]
    """Esquema JSON de los argumentos."""

    risk: Risk
    """Riesgo estático declarado."""

    category: str
    """Agrupación funcional, útil para filtrar el catálogo y para los logs."""

    func: Callable[..., Any]
    """Implementación subyacente."""

    args_model: type[BaseModel]
    """Modelo Pydantic usado para validar los argumentos entrantes."""

    def to_schema(self) -> dict[str, Any]:
        """Devuelve la herramienta en el formato que espera la API del modelo."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Resultado de ejecutar una herramienta.

    Los errores no se propagan como excepciones hacia el orquestador: se
    devuelven en este contenedor para poder entregárselos al modelo, que
    dispone así de la oportunidad de corregirse por sí mismo.
    """

    ok: bool
    content: str
    tool: str
    error: str | None = None

    @classmethod
    def success(cls, tool: str, content: str) -> ToolResult:
        return cls(ok=True, content=content, tool=tool)

    @classmethod
    def failure(cls, tool: str, error: str) -> ToolResult:
        return cls(ok=False, content=error, tool=tool, error=error)


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ToolRegistry:
    """Catálogo de herramientas disponibles para el modelo.

    Se instancia normalmente una sola vez por proceso (véase ``registry``),
    pero admite instancias independientes, lo que resulta conveniente en las
    pruebas para no contaminar el registro global.
    """

    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    # -- Registro ---------------------------------------------------------- #

    def add(self, spec: ToolSpec) -> None:
        """Incorpora una herramienta al catálogo.

        Raises:
            ToolAlreadyRegisteredError: Si el nombre ya está ocupado.
        """
        if spec.name in self._tools:
            raise ToolAlreadyRegisteredError(
                f"Ya existe una herramienta llamada '{spec.name}'."
            )
        self._tools[spec.name] = spec

    # -- Consulta ---------------------------------------------------------- #

    def get(self, name: str) -> ToolSpec:
        """Recupera una herramienta por su nombre.

        Raises:
            ToolNotFoundError: Si la herramienta no está registrada.
        """
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(
                f"No existe ninguna herramienta llamada '{name}'."
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        """Nombres de todas las herramientas registradas, en orden alfabético."""
        return sorted(self._tools)

    def all(self, category: str | None = None) -> list[ToolSpec]:
        """Devuelve las herramientas registradas, opcionalmente filtradas."""
        specs = sorted(self._tools.values(), key=lambda s: s.name)
        if category is None:
            return specs
        return [spec for spec in specs if spec.category == category]

    def schemas(self, category: str | None = None) -> list[dict[str, Any]]:
        """Catálogo listo para enviar al modelo de lenguaje."""
        return [spec.to_schema() for spec in self.all(category)]

    # -- Ejecución --------------------------------------------------------- #

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Valida los argumentos y ejecuta la herramienta indicada.

        Nunca lanza excepciones derivadas de la herramienta: cualquier fallo se
        transforma en un ``ToolResult`` con ``ok=False`` cuyo texto está
        redactado para que el modelo pueda interpretarlo y reintentar.

        Args:
            name: Nombre de la herramienta solicitada por el modelo.
            arguments: Argumentos producidos por el modelo.

        Returns:
            El resultado de la ejecución, exitoso o fallido.
        """
        try:
            spec = self.get(name)
        except ToolNotFoundError as exc:
            return ToolResult.failure(name, str(exc))

        try:
            validated = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult.failure(
                name, f"Argumentos inválidos para '{name}': {_format_errors(exc)}"
            )

        try:
            output = spec.func(**validated.model_dump())
        except Exception as exc:  # noqa: BLE001 - frontera deliberada
            return ToolResult.failure(
                name, f"La herramienta '{name}' falló: {type(exc).__name__}: {exc}"
            )

        return ToolResult.success(name, "" if output is None else str(output))


def _format_errors(exc: ValidationError) -> str:
    """Convierte un error de validación de Pydantic en una frase legible."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "(raíz)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


#: Registro global de la aplicación. Los módulos de ``jarvis.tools`` se
#: registran en él por el mero hecho de ser importados.
registry = ToolRegistry()


# --------------------------------------------------------------------------- #
# Decorador
# --------------------------------------------------------------------------- #


def tool(
    *,
    risk: Risk = Risk.CONFIRM,
    category: str = "general",
    name: str | None = None,
    registry: ToolRegistry = registry,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declara una función como herramienta invocable por el modelo.

    El decorador devuelve la función intacta, de modo que sigue siendo
    invocable y comprobable de forma directa, sin pasar por el registro.

    El valor por omisión de ``risk`` es ``CONFIRM`` de forma intencionada: una
    herramienta cuyo riesgo se olvidó declarar pedirá confirmación en lugar de
    ejecutarse en silencio.

    Args:
        risk: Riesgo estático de la herramienta.
        category: Agrupación funcional (``system``, ``files``, ``web``...).
        name: Nombre expuesto al modelo. Por omisión, el de la función.
        registry: Registro de destino. Permite aislar el catálogo en pruebas.

    Returns:
        El decorador que registra la función y la devuelve sin modificar.

    Raises:
        ValueError: Si la función carece de docstring, ya que este constituye
            la descripción que lee el modelo para decidir cuándo usarla.
    """

    target_registry = registry

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        description, _ = _parse_docstring(func.__doc__)
        if not description:
            raise ValueError(
                f"La herramienta '{func.__name__}' necesita un docstring: es la "
                "descripción que el modelo lee para decidir cuándo usarla."
            )

        args_model = _build_args_model(func)
        schema = _strip_titles(args_model.model_json_schema())

        spec = ToolSpec(
            name=name or func.__name__,
            description=description,
            parameters=schema,
            risk=risk,
            category=category,
            func=func,
            args_model=args_model,
        )
        target_registry.add(spec)

        # Referencia inversa: facilita inspeccionar la especificación desde la
        # propia función durante el desarrollo y las pruebas.
        func.__tool_spec__ = spec  # type: ignore[attr-defined]
        return func

    return decorator
