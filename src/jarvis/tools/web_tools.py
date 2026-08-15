"""Herramientas de acceso a Internet.

Envoltorios sobre ``jarvis.web``. Las respuestas incluyen siempre la
dirección de origen: la personalidad del asistente le exige distinguir lo que
sabe de lo que consultó, y para citar la fuente necesita tenerla a mano.
"""

from __future__ import annotations

from jarvis.core.registry import tool
from jarvis.security.risk import Risk
from jarvis.web import client

__all__ = ["read_web_page", "search_web"]


@tool(risk=Risk.SAFE, category="web")
def search_web(query: str, max_results: int = 5) -> str:
    """Busca en Internet información actual o que no conozcas.

    Devuelve títulos, direcciones y un resumen. Para leer una página entera,
    usa después read_web_page con su dirección.

    Args:
        query: Términos de búsqueda.
        max_results: Número de resultados, entre 1 y 8.
    """
    resultados = client.search(query, max_results=max(1, min(8, max_results)))

    if not resultados:
        return f"No encontré resultados para «{query}»."

    lineas = "\n\n".join(r.describe() for r in resultados)
    return f"Resultados de búsqueda para «{query}»:\n\n{lineas}"


@tool(risk=Risk.SAFE, category="web")
def read_web_page(url: str) -> str:
    """Lee el contenido de una página web y devuelve su texto.

    Args:
        url: Dirección de la página.
    """
    titulo, texto, recortado = client.fetch_page(url)

    aviso = (
        f"\n\n[Recortado a los primeros {client.MAX_PAGE_CHARS} caracteres.]"
        if recortado
        else ""
    )
    return f"{titulo}\n{url}\n\n{texto}{aviso}"
