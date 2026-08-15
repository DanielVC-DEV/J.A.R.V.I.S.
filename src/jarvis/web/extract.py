"""Extracción del texto legible de una página web.

Una página corriente trae mucho más que su contenido: guiones, hojas de
estilo, menús, avisos de cookies y pies de página. Enviárselo todo al modelo
desperdiciaría el presupuesto de tokens del turno y enterraría lo que importa.

Se emplea el analizador de HTML de la biblioteca estándar en lugar de una
dependencia externa. El objetivo aquí no es reconstruir la página con
fidelidad sino quedarse con la prosa, y para eso basta con descartar lo que
nunca es contenido y respetar los saltos de bloque.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["extract_text", "extract_title"]

#: Elementos cuyo contenido nunca es texto para el lector.
_SKIP = frozenset(
    {"script", "style", "noscript", "svg", "canvas", "template", "iframe"}
)

#: Elementos que separan bloques de texto. Sin ellos, los párrafos quedarían
#: pegados unos a otros y el modelo leería frases que nadie escribió.
_BLOCK = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "main", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "hr",
        "blockquote", "pre", "table", "ul", "ol", "nav",
    }
)

_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]{2,}")


class _TextExtractor(HTMLParser):
    """Recorre el HTML acumulando únicamente el texto legible."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._omitiendo = 0
        self._titulo: list[str] = []
        self._en_titulo = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP:
            self._omitiendo += 1
        elif tag == "title":
            self._en_titulo = True
        elif tag in _BLOCK:
            self._partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._omitiendo = max(0, self._omitiendo - 1)
        elif tag == "title":
            self._en_titulo = False
        elif tag in _BLOCK:
            self._partes.append("\n")

    def handle_data(self, data: str) -> None:
        if self._en_titulo:
            self._titulo.append(data)
            return
        if self._omitiendo:
            return
        if data.strip():
            self._partes.append(data)

    @property
    def text(self) -> str:
        """Texto acumulado, con los espacios en blanco normalizados."""
        crudo = "".join(self._partes)
        lineas = [linea.strip() for linea in crudo.splitlines()]
        unido = "\n".join(linea for linea in lineas if linea)
        return _BLANK_LINES.sub("\n\n", _SPACES.sub(" ", unido)).strip()

    @property
    def title(self) -> str:
        return " ".join("".join(self._titulo).split())


def _parse(html: str) -> _TextExtractor:
    """Analiza el HTML, tolerando el que esté mal formado.

    Las páginas reales incumplen la especificación con frecuencia. Un fallo
    del analizador no debe impedir aprovechar lo que sí se leyó.
    """
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001 - se conserva lo acumulado hasta el fallo
        pass
    return extractor


def extract_text(html: str, max_chars: int = 4_000) -> tuple[str, bool]:
    """Extrae el texto legible de una página.

    Args:
        html: Código HTML de la página.
        max_chars: Longitud máxima devuelta.

    Returns:
        Una tupla ``(texto, recortado)``.
    """
    texto = _parse(html).text

    if len(texto) > max_chars:
        return texto[:max_chars], True
    return texto, False


def extract_title(html: str) -> str:
    """Extrae el título de una página, o una cadena vacía si no lo tiene."""
    return _parse(html).title
