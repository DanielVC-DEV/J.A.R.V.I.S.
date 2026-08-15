"""Pruebas del acceso a Internet.

Las peticiones se simulan con un transporte de ``httpx`` y la resolución de
nombres con un sustituto, de modo que nada sale a la red.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from jarvis.web.client import WebError, fetch_page
from jarvis.web.extract import extract_text, extract_title
from jarvis.web.safety import UnsafeUrlError, check_url


# --------------------------------------------------------------------------- #
# Direcciones admisibles
# --------------------------------------------------------------------------- #


@pytest.fixture
def red_publica(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hace que todo nombre resuelva a una dirección pública."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def _resuelve_a(monkeypatch: pytest.MonkeyPatch, direccion: str) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (direccion, 0))],
    )


def test_a_public_address_is_allowed(red_publica: None) -> None:
    assert check_url("https://ejemplo.com/pagina") == "https://ejemplo.com/pagina"


def test_a_missing_scheme_is_added(red_publica: None) -> None:
    """El modelo escribe «ejemplo.com» sin protocolo con frecuencia."""
    assert check_url("ejemplo.com").startswith("https://")


@pytest.mark.parametrize(
    "direccion",
    ["127.0.0.1", "192.168.1.10", "10.0.0.5", "172.16.0.1", "169.254.1.1", "::1"],
)
def test_internal_addresses_are_refused(
    monkeypatch: pytest.MonkeyPatch, direccion: str
) -> None:
    """El asistente no debe servir de puente hacia servicios internos."""
    _resuelve_a(monkeypatch, direccion)
    with pytest.raises(UnsafeUrlError, match="red interna"):
        check_url("http://parece-normal.com/admin")


def test_a_name_that_does_not_resolve_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No poder comprobarlo no es motivo para confiar."""

    def falla(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror("no resuelve")

    monkeypatch.setattr(socket, "getaddrinfo", falla)
    with pytest.raises(UnsafeUrlError):
        check_url("https://inexistente.invalido")


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://servidor/x"])
def test_other_protocols_are_refused(red_publica: None, url: str) -> None:
    """Leer archivos locales corresponde a las herramientas confinadas."""
    with pytest.raises(UnsafeUrlError, match="direcciones web"):
        check_url(url)


def test_an_empty_url_is_refused() -> None:
    with pytest.raises(UnsafeUrlError, match="ninguna dirección"):
        check_url("   ")


# --------------------------------------------------------------------------- #
# Extracción del texto
# --------------------------------------------------------------------------- #


def test_scripts_and_styles_are_discarded() -> None:
    html = """
    <html><head><title>Mi página</title>
    <style>body { color: red; }</style>
    <script>alert('hola');</script>
    </head><body><p>El contenido de verdad.</p></body></html>
    """
    texto, _ = extract_text(html)
    assert "El contenido de verdad." in texto
    assert "color: red" not in texto
    assert "alert" not in texto


def test_the_title_is_extracted() -> None:
    assert extract_title("<html><head><title>Mi página</title></head></html>") == (
        "Mi página"
    )


def test_blocks_are_separated() -> None:
    """Sin separar los bloques, el modelo leería frases que nadie escribió."""
    texto, _ = extract_text("<p>Primera frase.</p><p>Segunda frase.</p>")
    assert "Primera frase." in texto
    assert "Segunda frase." in texto
    assert "Primera frase.Segunda" not in texto


def test_entities_are_decoded() -> None:
    texto, _ = extract_text("<p>Ma&ntilde;ana &amp; pasado</p>")
    assert "Mañana & pasado" in texto


def test_long_pages_are_truncated() -> None:
    html = "<p>" + ("palabra " * 5000) + "</p>"
    texto, recortado = extract_text(html, max_chars=500)
    assert recortado
    assert len(texto) == 500


def test_malformed_html_still_yields_text() -> None:
    """Las páginas reales incumplen la especificación con frecuencia."""
    texto, _ = extract_text("<p>Contenido<div><span>más<p>y más")
    assert "Contenido" in texto
    assert "más" in texto


def test_blank_space_is_normalised() -> None:
    texto, _ = extract_text("<p>Uno</p>\n\n\n\n<p>Dos</p>")
    assert "\n\n\n" not in texto


# --------------------------------------------------------------------------- #
# Lectura de páginas
# --------------------------------------------------------------------------- #


def _cliente(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _pagina(html: str, tipo: str = "text/html", estado: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(estado, text=html, headers={"content-type": tipo})

    return handler


def test_a_page_is_read(red_publica: None) -> None:
    handler = _pagina(
        "<html><head><title>RTX 5070</title></head>"
        "<body><p>Precio aproximado: 600 euros.</p></body></html>"
    )
    titulo, texto, recortado = fetch_page("https://ejemplo.com", client=_cliente(handler))

    assert titulo == "RTX 5070"
    assert "600 euros" in texto
    assert not recortado


def test_a_non_html_response_is_refused(red_publica: None) -> None:
    handler = _pagina("%PDF-1.4", tipo="application/pdf")
    with pytest.raises(WebError, match="no es una página de texto"):
        fetch_page("https://ejemplo.com/doc.pdf", client=_cliente(handler))


def test_an_error_page_is_reported(red_publica: None) -> None:
    handler = _pagina("<h1>No encontrado</h1>", estado=404)
    with pytest.raises(WebError, match="404"):
        fetch_page("https://ejemplo.com/nada", client=_cliente(handler))


def test_a_page_without_readable_text_is_reported(red_publica: None) -> None:
    """Muchas páginas generan su contenido con guiones que no ejecutamos."""
    handler = _pagina("<html><body><script>render();</script></body></html>")
    with pytest.raises(WebError, match="texto legible"):
        fetch_page("https://ejemplo.com", client=_cliente(handler))


def test_an_internal_address_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La comprobación ocurre antes de abrir ninguna conexión."""
    _resuelve_a(monkeypatch, "127.0.0.1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debería haberse conectado")

    with pytest.raises(WebError, match="red interna"):
        fetch_page("http://localhost:8080/admin", client=_cliente(handler))


def test_a_timeout_is_explained(red_publica: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    with pytest.raises(WebError, match="no respondió"):
        fetch_page("https://ejemplo.com", client=_cliente(handler))
