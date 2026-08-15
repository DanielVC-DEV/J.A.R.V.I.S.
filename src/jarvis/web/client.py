"""Búsqueda en Internet y lectura de páginas.

Dos capacidades que el asistente necesita para responder sobre cualquier cosa
que no esté en el equipo ni en lo que el modelo sepa de antemano.

La búsqueda usa DuckDuckGo, que no exige clave ni registro. La lectura de
páginas se hace con el mismo cliente HTTP que el resto del proyecto.

Toda dirección pasa antes por ``jarvis.web.safety``: el asistente no debe
poder convertirse en un puente hacia los servicios internos del equipo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from jarvis.web.extract import extract_text, extract_title
from jarvis.web.safety import UnsafeUrlError, check_url

__all__ = ["MAX_PAGE_CHARS", "SearchResult", "WebError", "fetch_page", "search"]

_logger = logging.getLogger(__name__)

#: Espera máxima. Una página que tarde más ya no sirve en una conversación.
TIMEOUT_SECONDS = 20.0

#: Longitud máxima del texto extraído de una página. El presupuesto de tokens
#: de un turno es limitado y una página larga lo consumiría entero.
MAX_PAGE_CHARS = 4_000

#: Los servidores rechazan o degradan las peticiones sin identificación.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


class WebError(RuntimeError):
    """No se pudo completar la consulta a Internet."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Un resultado de búsqueda."""

    title: str
    url: str
    snippet: str = ""

    def describe(self) -> str:
        """Redacta el resultado de forma compacta para el modelo."""
        resumen = f"\n  {self.snippet}" if self.snippet else ""
        return f"{self.title}\n  {self.url}{resumen}"


def search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Busca en Internet.

    Args:
        query: Términos de búsqueda.
        max_results: Número máximo de resultados.

    Returns:
        Los resultados encontrados.

    Raises:
        WebError: Si falta la dependencia o el servicio no responde.
    """
    if not query.strip():
        raise WebError("No se indicó qué buscar.")

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise WebError(
            "Falta el paquete «ddgs», necesario para buscar en Internet. "
            'Instala el proyecto con «pip install -e .».'
        ) from exc

    try:
        with DDGS() as buscador:
            crudos = list(buscador.text(query, max_results=max_results))
    except Exception as exc:  # noqa: BLE001 - la causa varía mucho
        raise WebError(
            f"No se pudo completar la búsqueda: {exc}. Puede ser un problema "
            "de conexión o un límite temporal del buscador."
        ) from exc

    resultados: list[SearchResult] = []
    for crudo in crudos:
        titulo = str(crudo.get("title") or "").strip()
        enlace = str(crudo.get("href") or crudo.get("url") or "").strip()
        if not titulo or not enlace:
            continue
        resultados.append(
            SearchResult(
                title=titulo,
                url=enlace,
                snippet=" ".join(str(crudo.get("body") or "").split())[:280],
            )
        )

    return resultados


def fetch_page(
    url: str,
    max_chars: int = MAX_PAGE_CHARS,
    client: httpx.Client | None = None,
) -> tuple[str, str, bool]:
    """Descarga una página y devuelve su texto legible.

    Args:
        url: Dirección de la página.
        max_chars: Longitud máxima del texto devuelto.
        client: Cliente HTTP alternativo, para las pruebas.

    Returns:
        Una tupla ``(título, texto, recortado)``.

    Raises:
        WebError: Si la dirección no es admisible o la página no se pudo leer.
    """
    try:
        destino = check_url(url)
    except UnsafeUrlError as exc:
        raise WebError(str(exc)) from exc

    http = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        respuesta = http.get(destino)
    except httpx.TimeoutException as exc:
        raise WebError(
            f"La página no respondió en {TIMEOUT_SECONDS:.0f} segundos."
        ) from exc
    except httpx.RequestError as exc:
        raise WebError(f"No se pudo acceder a «{destino}»: {exc}") from exc

    if respuesta.status_code >= httpx.codes.BAD_REQUEST:
        raise WebError(
            f"La página respondió con un error {respuesta.status_code}."
        )

    tipo = respuesta.headers.get("content-type", "")
    if "html" not in tipo and "text" not in tipo:
        raise WebError(
            f"«{destino}» no es una página de texto ({tipo or 'tipo desconocido'}). "
            "Solo puedo leer páginas web."
        )

    texto, recortado = extract_text(respuesta.text, max_chars)

    if not texto.strip():
        raise WebError(
            "La página no tiene texto legible. Puede que su contenido se "
            "genere con guiones que yo no ejecuto."
        )

    return extract_title(respuesta.text) or destino, texto, recortado
