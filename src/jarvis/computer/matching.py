"""Coincidencia difusa entre lo que dice el usuario y lo que hay en el equipo.

Este problema aparece cada vez que una orden nombra algo: una aplicación
—«abre crome»—, una ventana —«pásate a la de Chrome»— y más adelante un
archivo o un proyecto. La transcripción de voz comete erratas, el usuario
abrevia y los nombres reales son largos.

La lógica es común a todos esos casos, de modo que vive aquí una sola vez, sin
depender de qué se esté buscando ni del sistema operativo. Eso permite
comprobarla por completo con datos inventados.

Se prefiere una gradación explícita —coincidencia exacta, palabra completa,
prefijo, subcadena, similitud— a una métrica opaca, porque su comportamiento
puede razonarse y verificarse caso por caso.

Ante varias opciones equivalentes **no se adivina**: se informa de la
ambigüedad para que el asistente pregunte.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

__all__ = [
    "ACCEPT_THRESHOLD",
    "AMBIGUITY_GAP",
    "MAX_CANDIDATES",
    "AmbiguousMatchError",
    "Match",
    "MatchError",
    "NoMatchError",
    "normalise",
    "rank",
    "resolve",
    "score",
]

T = TypeVar("T")

#: Puntuación mínima para considerar que un término identifica algo. Por
#: debajo se admite que no se encontró nada, en lugar de actuar sobre algo que
#: el usuario no pidió.
ACCEPT_THRESHOLD = 70.0

#: Distancia mínima entre la mejor coincidencia y la siguiente para darla por
#: buena. Si dos opciones puntúan casi igual, se pregunta.
AMBIGUITY_GAP = 5.0

#: Número máximo de alternativas que se ofrecen al desambiguar.
MAX_CANDIDATES = 4


class MatchError(RuntimeError):
    """Error genérico al resolver un nombre."""


class NoMatchError(MatchError):
    """Ninguna opción se corresponde con el término buscado."""


class AmbiguousMatchError(MatchError):
    """Varias opciones coinciden con solvencia parecida.

    Attributes:
        query: Término que resultó ambiguo.
        candidates: Alternativas ordenadas de mejor a peor.
    """

    def __init__(self, query: str, candidates: list, names: list[str]) -> None:
        self.query = query
        self.candidates = candidates
        self.names = names
        super().__init__(
            f"«{query}» coincide con varias opciones: {', '.join(names)}. "
            "Conviene preguntar al usuario cuál quiere."
        )


@dataclass(frozen=True, slots=True)
class Match:
    """Un elemento junto con su puntuación frente a un término."""

    item: object
    score: float


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #


def normalise(text: str) -> str:
    """Reduce un texto a una forma comparable.

    Pasa a minúsculas, elimina las tildes y la puntuación, y normaliza los
    espacios. Sin este paso, «configuración» y «configuracion» serían términos
    distintos, y la transcripción de voz produce ambas formas indistintamente.

    Se emplea la descomposición canónica (NFD) y no la de compatibilidad
    (NFKD): esta última traduciría el símbolo «™» a las letras «TM», que se
    colarían en el nombre y falsearían la comparación.

    Args:
        text: Texto a normalizar.

    Returns:
        El texto reducido a minúsculas sin acentos ni signos.
    """
    descompuesto = unicodedata.normalize("NFD", text)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes)
    return " ".join(limpio.lower().split())


def _ratio(a: str, b: str) -> float:
    """Similitud entre dos cadenas, entre 0 y 1."""
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# Puntuación
# --------------------------------------------------------------------------- #


def _score_word(query_word: str, word: str) -> float:
    """Puntúa una palabra suelta contra otra."""
    if query_word == word:
        return 100.0
    if word.startswith(query_word):
        return 92.0
    if query_word in word:
        return 78.0
    return _ratio(query_word, word) * 90.0


def _score_term(query: str, term: str) -> float:
    """Puntúa un término de búsqueda contra una denominación concreta.

    Se combinan dos lecturas complementarias:

    * **Consulta completa contra denominación completa.** Cubre el caso
      habitual, en el que el usuario nombra la cosa tal cual o comete una
      errata: «crome» debe encontrar «Chrome».
    * **Palabra a palabra.** Cubre las abreviaturas y las omisiones, donde la
      consulta no aparece literalmente: «vs code» debe encontrar «Visual
      Studio Code». Se pondera a partes iguales la mejor palabra y el promedio
      de todas, de modo que acertar una palabra no baste si las demás fallan.

    Args:
        query: Término normalizado que dijo el usuario.
        term: Denominación normalizada del candidato.

    Returns:
        Una puntuación entre 0 y 100.
    """
    if not query or not term:
        return 0.0

    if query == term:
        return 100.0

    palabras = term.split()
    tokens = query.split()

    if query in palabras:
        directa = 95.0
    elif term.startswith(query):
        directa = 92.0
    elif any(palabra.startswith(query) for palabra in palabras):
        directa = 88.0
    elif query in term:
        directa = 78.0
    else:
        directa = _ratio(query, term) * 75.0

    parciales = [
        max((_score_word(token, palabra) for palabra in palabras), default=0.0)
        for token in tokens
    ]
    por_palabras = (
        0.5 * max(parciales) + 0.5 * (sum(parciales) / len(parciales))
        if parciales
        else 0.0
    )

    # Ante dos coincidencias equivalentes se prefiere el nombre más escueto:
    # «Google Chrome» antes que «Chrome Remote Desktop».
    exceso = max(0, len(term) - len(query))
    return max(0.0, max(directa, por_palabras) - min(6.0, exceso * 0.5))


def score(query: str, terms: Sequence[str]) -> float:
    """Puntúa un término de búsqueda contra varias denominaciones.

    Args:
        query: Término tal como lo dijo el usuario.
        terms: Denominaciones del candidato: su nombre y sus alias.

    Returns:
        La mejor puntuación obtenida, entre 0 y 100.
    """
    normalizado = normalise(query)
    return max(
        (_score_term(normalizado, normalise(termino)) for termino in terms),
        default=0.0,
    )


# --------------------------------------------------------------------------- #
# Resolución
# --------------------------------------------------------------------------- #


def rank(
    query: str,
    items: Sequence[T],
    terms_of: Callable[[T], Sequence[str]],
) -> list[Match]:
    """Ordena unos elementos por su afinidad con el término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        items: Candidatos entre los que buscar.
        terms_of: Función que devuelve las denominaciones de un candidato.

    Returns:
        Las coincidencias con puntuación positiva, de mejor a peor. En caso de
        empate se antepone el nombre más corto, para que el resultado sea
        estable entre ejecuciones.
    """
    coincidencias = [
        Match(item=item, score=score(query, terms_of(item))) for item in items
    ]
    positivas = [m for m in coincidencias if m.score > 0]
    positivas.sort(key=lambda m: (-m.score, len(terms_of(m.item)[0])))  # type: ignore[arg-type]
    return positivas


def resolve(
    query: str,
    items: Sequence[T],
    terms_of: Callable[[T], Sequence[str]],
    *,
    what: str = "opción",
) -> T:
    """Determina a qué elemento se refiere un término de búsqueda.

    Args:
        query: Término tal como lo dijo el usuario.
        items: Candidatos entre los que buscar.
        terms_of: Función que devuelve las denominaciones de un candidato.
        what: Nombre de lo que se busca, para los mensajes de error.

    Returns:
        El elemento identificado.

    Raises:
        NoMatchError: Si ninguna candidata alcanza el umbral.
        AmbiguousMatchError: Si varias puntúan de forma equivalente.
    """
    coincidencias = rank(query, items, terms_of)

    if not coincidencias or coincidencias[0].score < ACCEPT_THRESHOLD:
        raise NoMatchError(f"No encontré ninguna {what} que corresponda a «{query}».")

    mejor = coincidencias[0]
    empatadas = [
        m
        for m in coincidencias
        if m.score >= ACCEPT_THRESHOLD and mejor.score - m.score < AMBIGUITY_GAP
    ]

    if len(empatadas) > 1:
        seleccion = empatadas[:MAX_CANDIDATES]
        raise AmbiguousMatchError(
            query,
            [m.item for m in seleccion],
            [terms_of(m.item)[0] for m in seleccion],  # type: ignore[arg-type]
        )

    return mejor.item  # type: ignore[return-value]
