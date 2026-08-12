"""Guardia de seguridad: decide si una herramienta puede ejecutarse.

Se interpone entre la decisión del modelo y la ejecución real. Ninguna
herramienta debe invocarse sin pasar antes por aquí.

La evaluación combina dos fuentes:

* El **riesgo estático** declarado en el decorador ``@tool``.
* Un conjunto de **políticas dinámicas** que examinan los argumentos concretos.
  Borrar un archivo temporal y borrar un directorio del sistema son la misma
  herramienta con consecuencias opuestas; solo los argumentos distinguen una
  situación de la otra.

Las políticas únicamente pueden endurecer el veredicto, nunca relajarlo. Esta
asimetría es deliberada: una política mal escrita podrá resultar molesta, pero
no podrá abrir una puerta que el riesgo estático mantenía cerrada.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from jarvis.core.registry import ToolSpec
from jarvis.security.risk import Risk

__all__ = [
    "DANGEROUS_COMMAND_PATTERNS",
    "Decision",
    "Guard",
    "Policy",
    "Verdict",
    "dangerous_command_policy",
    "default_allowed_roots",
    "path_jail_policy",
]


class Decision(StrEnum):
    """Resolución del guardia sobre una llamada concreta."""

    ALLOW = "allow"
    """Puede ejecutarse sin intervención del usuario."""

    CONFIRM = "confirm"
    """Requiere confirmación explícita antes de ejecutarse."""

    DENY = "deny"
    """No debe ejecutarse."""


#: Orden de severidad. Al combinar veredictos siempre prevalece el más estricto.
_SEVERITY: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.CONFIRM: 1,
    Decision.DENY: 2,
}

_FROM_RISK: dict[Risk, Decision] = {
    Risk.SAFE: Decision.ALLOW,
    Risk.CONFIRM: Decision.CONFIRM,
    Risk.BLOCKED: Decision.DENY,
}


@dataclass(frozen=True, slots=True)
class Verdict:
    """Resolución razonada sobre una llamada a herramienta."""

    decision: Decision
    reason: str
    policy: str = "riesgo declarado"

    @property
    def allowed(self) -> bool:
        """Indica si puede ejecutarse sin preguntar."""
        return self.decision is Decision.ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is Decision.CONFIRM

    @property
    def denied(self) -> bool:
        return self.decision is Decision.DENY


#: Una política examina una llamada y devuelve un veredicto, o ``None`` si el
#: caso no le concierne.
Policy = Callable[[ToolSpec, dict[str, Any]], Verdict | None]


# --------------------------------------------------------------------------- #
# Política: comandos destructivos
# --------------------------------------------------------------------------- #

#: Patrones que denotan una operación destructiva o irreversible. Se comparan
#: contra cualquier argumento de texto, con independencia de la herramienta:
#: si una cadena así llega a ejecutarse, el daño es el mismo venga de donde
#: venga.
DANGEROUS_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bformat\s+[a-z]:", "formatear una unidad"),
    (r"\bdel\s+/[sqf]", "borrado recursivo con del"),
    (r"\b(rd|rmdir)\s+/s", "borrado recursivo de directorios"),
    (r"\bdiskpart\b", "manipulación de particiones"),
    (r"\bvssadmin\s+delete", "eliminación de instantáneas del sistema"),
    (r"\bbcdedit\b", "modificación del arranque del sistema"),
    (r"\breg\s+delete\b", "eliminación de claves del registro"),
    (r"\bcipher\s+/w", "borrado seguro del espacio libre"),
    (r"\bshutdown\b", "apagado o reinicio del equipo"),
    (r"\bmkfs\b", "formateo de sistemas de archivos"),
    (r"\brm\s+-[a-z]*[rf]", "borrado recursivo con rm"),
    (r"remove-item.*-recurse.*-force", "borrado recursivo forzado"),
    (r"set-executionpolicy", "relajación de la política de ejecución"),
    (r":\s*\|\s*:\s*&", "bomba de bifurcación"),
    (r"%0\s*\|\s*%0", "bomba de bifurcación"),
)

_COMPILED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(patron, re.IGNORECASE), descripcion)
    for patron, descripcion in DANGEROUS_COMMAND_PATTERNS
)


def _text_arguments(arguments: dict[str, Any]) -> Iterable[str]:
    """Recorre los valores de texto de los argumentos, incluidos los anidados."""
    pendientes: list[Any] = list(arguments.values())
    while pendientes:
        valor = pendientes.pop()
        if isinstance(valor, str):
            yield valor
        elif isinstance(valor, dict):
            pendientes.extend(valor.values())
        elif isinstance(valor, list | tuple | set):
            pendientes.extend(valor)


def dangerous_command_policy(
    spec: ToolSpec, arguments: dict[str, Any]
) -> Verdict | None:
    """Rechaza los argumentos que contienen instrucciones destructivas.

    Args:
        spec: Herramienta invocada.
        arguments: Argumentos propuestos por el modelo.

    Returns:
        Un veredicto de denegación si se detecta un patrón peligroso, o
        ``None`` en caso contrario.
    """
    for texto in _text_arguments(arguments):
        for patron, descripcion in _COMPILED_PATTERNS:
            if patron.search(texto):
                return Verdict(
                    decision=Decision.DENY,
                    reason=(
                        f"La llamada a «{spec.name}» contiene una instrucción de "
                        f"{descripcion}, que no puede ejecutarse."
                    ),
                    policy="comandos destructivos",
                )
    return None


# --------------------------------------------------------------------------- #
# Política: jaula de rutas
# --------------------------------------------------------------------------- #


def default_allowed_roots() -> tuple[Path, ...]:
    """Carpetas del usuario sobre las que se permite operar por omisión.

    Se limita a las carpetas personales. El resto del disco —archivos de
    programa, directorios del sistema, otros perfiles de usuario— queda fuera
    salvo que el usuario lo añada de forma explícita.

    Returns:
        Las carpetas existentes, ya resueltas.
    """
    inicio = Path.home()
    candidatas = (
        inicio / "Documents",
        inicio / "Documentos",
        inicio / "Downloads",
        inicio / "Descargas",
        inicio / "Desktop",
        inicio / "Escritorio",
        inicio / "Pictures",
        inicio / "Imágenes",
        inicio / "Music",
        inicio / "Música",
        inicio / "Videos",
        inicio / "Vídeos",
    )
    return tuple(ruta.resolve() for ruta in candidatas if ruta.is_dir())


def _looks_like_path(value: str) -> bool:
    """Determina si una cadena aparenta ser una ruta del sistema de archivos."""
    if not value or len(value) > 4096:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", value):
        return True
    return value.startswith(("\\\\", "/", "~", "./", ".\\", "../", "..\\"))


def _is_inside(candidate: Path, roots: Sequence[Path]) -> bool:
    """Comprueba si una ruta queda bajo alguna de las carpetas permitidas."""
    try:
        resuelta = candidate.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(resuelta == raiz or resuelta.is_relative_to(raiz) for raiz in roots)


def path_jail_policy(
    allowed_roots: Sequence[Path] | None = None,
    categories: frozenset[str] = frozenset({"files"}),
) -> Policy:
    """Construye una política que confina las rutas a las carpetas permitidas.

    Se aplica solo a las categorías indicadas —de forma predeterminada, las
    herramientas de archivos— para no entorpecer llamadas legítimas que
    mencionan rutas del sistema sin escribir en ellas, como abrir un programa
    instalado en «Archivos de programa».

    Args:
        allowed_roots: Carpetas permitidas. Si se omite, se toman las carpetas
            personales del usuario.
        categories: Categorías de herramientas sujetas a la restricción.

    Returns:
        La política, lista para registrarse en el guardia.
    """
    raices = tuple(allowed_roots) if allowed_roots is not None else None

    def policy(spec: ToolSpec, arguments: dict[str, Any]) -> Verdict | None:
        if spec.category not in categories:
            return None

        permitidas = raices if raices is not None else default_allowed_roots()

        for texto in _text_arguments(arguments):
            if not _looks_like_path(texto):
                continue
            if not _is_inside(Path(texto), permitidas):
                return Verdict(
                    decision=Decision.DENY,
                    reason=(
                        f"La ruta «{texto}» queda fuera de las carpetas sobre las "
                        "que tengo permiso para operar."
                    ),
                    policy="jaula de rutas",
                )
        return None

    return policy


# --------------------------------------------------------------------------- #
# Guardia
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Guard:
    """Evaluador de riesgo de las llamadas a herramientas."""

    policies: list[Policy] = field(default_factory=list)

    @classmethod
    def with_default_policies(cls) -> Guard:
        """Crea un guardia con el conjunto de políticas recomendado."""
        return cls(policies=[dangerous_command_policy, path_jail_policy()])

    def add_policy(self, policy: Policy) -> None:
        """Incorpora una política adicional."""
        self.policies.append(policy)

    def evaluate(self, spec: ToolSpec, arguments: dict[str, Any]) -> Verdict:
        """Resuelve si una llamada puede ejecutarse.

        Args:
            spec: Herramienta que el modelo quiere invocar.
            arguments: Argumentos propuestos.

        Returns:
            El veredicto más restrictivo entre el riesgo declarado y el de
            todas las políticas aplicables.
        """
        veredicto = Verdict(
            decision=_FROM_RISK[spec.risk],
            reason=f"«{spec.name}» está declarada con riesgo {spec.risk}.",
        )

        for policy in self.policies:
            propuesto = policy(spec, arguments)
            if propuesto is None:
                continue
            if _SEVERITY[propuesto.decision] > _SEVERITY[veredicto.decision]:
                veredicto = propuesto

        return veredicto
