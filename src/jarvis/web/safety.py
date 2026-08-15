"""Comprobación de las direcciones que el asistente puede visitar.

Un asistente que descarga cualquier URL que le indiquen es un riesgo de
seguridad concreto y conocido: bastaría con pedirle que lea
``http://localhost:8080/admin`` o una dirección de la red local para que
actuara como puente hacia servicios que solo son accesibles desde el equipo
del usuario. El navegador de una persona no haría eso porque la persona sabe
lo que está pidiendo; el asistente no.

Por eso solo se admiten direcciones públicas y los protocolos habituales de
la web. La comprobación se hace **resolviendo el nombre**, no mirando el
texto: un dominio corriente puede apuntar a una dirección interna.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["UnsafeUrlError", "check_url"]

#: Protocolos admitidos. Se excluyen ``file``, ``ftp`` y demás: leer archivos
#: locales corresponde a las herramientas de archivos, que sí están
#: confinadas a las carpetas del usuario.
ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeUrlError(ValueError):
    """La dirección no puede visitarse por motivos de seguridad."""


def _is_private(host: str) -> bool:
    """Determina si un nombre de equipo apunta a una dirección no pública.

    Args:
        host: Nombre o dirección tal como aparece en la URL.

    Returns:
        ``True`` si apunta a una dirección interna, de bucle local, de enlace
        local o reservada. Un nombre que no resuelve se considera interno: no
        poder comprobarlo no es motivo para confiar.
    """
    try:
        direcciones = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return True

    for familia in direcciones:
        try:
            direccion = ipaddress.ip_address(familia[4][0])
        except ValueError:
            return True
        if (
            direccion.is_private
            or direccion.is_loopback
            or direccion.is_link_local
            or direccion.is_reserved
            or direccion.is_multicast
            or direccion.is_unspecified
        ):
            return True

    return False


def check_url(url: str) -> str:
    """Comprueba que una dirección puede visitarse y la devuelve normalizada.

    Args:
        url: Dirección propuesta por el modelo o dicha por el usuario.

    Returns:
        La dirección, con el protocolo añadido si faltaba.

    Raises:
        UnsafeUrlError: Si el protocolo no está admitido o la dirección apunta
            a la red interna.
    """
    texto = url.strip()
    if not texto:
        raise UnsafeUrlError("No se indicó ninguna dirección.")

    # Es habitual que el modelo escriba «ejemplo.com» sin protocolo.
    if "://" not in texto:
        texto = f"https://{texto}"

    partes = urlparse(texto)

    if partes.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"Solo puedo abrir direcciones web ({', '.join(sorted(ALLOWED_SCHEMES))}), "
            f"y esta usa «{partes.scheme}»."
        )

    if not partes.hostname:
        raise UnsafeUrlError(f"«{url}» no es una dirección válida.")

    if _is_private(partes.hostname):
        raise UnsafeUrlError(
            f"«{partes.hostname}» apunta a la red interna de este equipo. "
            "Solo puedo consultar direcciones públicas de Internet."
        )

    return texto
