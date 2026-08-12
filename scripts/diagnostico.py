"""Diagnóstico de la conexión con el proveedor de IA.

Aísla el origen de un fallo probando por separado cada capa: la configuración,
la autenticación, el catálogo de modelos, una petición mínima y una petición
con todas las herramientas. Así se distingue un problema de clave de uno de
modelo, y uno de modelo de uno de esquema de herramientas.

Funciona con cualquiera de los proveedores admitidos.

Uso, desde la raíz del proyecto y con el entorno activo::

    python scripts/diagnostico.py
"""

from __future__ import annotations

import json
import traceback
from typing import Any


def _titulo(texto: str) -> None:
    print(f"\n{'=' * 70}\n{texto}\n{'=' * 70}")


def _detalle(exc: BaseException) -> str:
    """Extrae toda la información disponible de un error."""
    partes = [f"{type(exc).__name__}: {exc}"]

    cuerpo = getattr(exc, "body", None)
    if cuerpo is not None:
        partes.append(f"cuerpo: {json.dumps(cuerpo, ensure_ascii=False, default=str)}")

    respuesta = getattr(exc, "response", None)
    texto = getattr(respuesta, "text", None) if respuesta is not None else None
    if texto:
        partes.append(f"respuesta: {texto[:800]}")

    return "\n  ".join(partes)


def _modelos_anthropic(settings: Any) -> list[str]:
    """Enumera los modelos accesibles con la API nativa."""
    import anthropic

    cliente = anthropic.Anthropic(api_key=settings.require_api_key())
    return [modelo.id for modelo in cliente.models.list(limit=40)]


def _modelos_compatibles(settings: Any) -> list[str]:
    """Enumera los modelos accesibles en un servicio compatible."""
    import httpx

    cabeceras = {}
    if settings.has_api_key():
        cabeceras["Authorization"] = f"Bearer {settings.require_api_key()}"

    respuesta = httpx.get(
        f"{settings.resolved_base_url()}/models", headers=cabeceras, timeout=30
    )
    respuesta.raise_for_status()
    datos = respuesta.json().get("data") or []
    return [str(m.get("id")) for m in datos if m.get("id")]


def main() -> int:  # noqa: C901 - un diagnóstico lineal se lee mejor entero
    _titulo("1. CONFIGURACIÓN")

    try:
        from jarvis.config.settings import Provider, load_settings

        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO al cargar la configuración:\n  {_detalle(exc)}")
        return 1

    clave = settings.api_key.get_secret_value() if settings.has_api_key() else ""
    print(f"  proveedor     : {settings.provider}")
    print(f"  max_tokens    : {settings.max_tokens}")
    print(f"  clave presente: {bool(clave)}")
    if clave:
        print(f"  clave         : {clave[:10]}…{clave[-4:]} ({len(clave)} car.)")

    try:
        modelo = settings.resolved_model()
        print(f"  modelo        : {modelo!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  modelo        : SIN RESOLVER — {exc}")
        modelo = ""

    try:
        destino = settings.resolved_base_url()
        print(f"  dirección     : {destino or '(API nativa)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  dirección     : SIN RESOLVER — {exc}")
        return 1

    es_local = "localhost" in destino or "127.0.0.1" in destino
    if not clave and not es_local:
        print("\n  FALLO: no hay clave configurada. Revisa el archivo .env.")
        return 1

    _titulo("2. MODELOS DISPONIBLES PARA TU CUENTA")

    disponibles: list[str] = []
    try:
        if settings.provider is Provider.ANTHROPIC:
            disponibles = _modelos_anthropic(settings)
        else:
            disponibles = _modelos_compatibles(settings)
        for identificador in sorted(disponibles):
            print(f"  {identificador}")
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo obtener la lista:\n  {_detalle(exc)}")

    if disponibles and modelo and modelo not in disponibles:
        print(
            f"\n  >>> El modelo configurado ({modelo!r}) NO figura en la lista.\n"
            "      Es con toda probabilidad la causa del error.\n"
            "      Copia uno de arriba a JARVIS_MODEL en el archivo .env."
        )

    if not modelo:
        print("\n  >>> Elige uno de la lista y ponlo en JARVIS_MODEL (.env).")
        return 1

    _titulo("3. PETICIÓN MÍNIMA (sin herramientas)")

    from jarvis.ai.factory import create_provider
    from jarvis.ai.provider import Message

    ok_minima = False
    try:
        proveedor = create_provider(settings)
        respuesta = proveedor.chat(
            "Eres un asistente de prueba.",
            [Message.user("Responde solo: ok")],
            [],
        )
        print(f"  CORRECTO. Respuesta: {respuesta.text!r}")
        print(f"  tokens: {respuesta.input_tokens} entrada, {respuesta.output_tokens} salida")
        ok_minima = True
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO:\n  {_detalle(exc)}")

    _titulo("4. PETICIÓN CON EL CATÁLOGO DE HERRAMIENTAS")

    try:
        import jarvis.tools  # noqa: F401  # registra las herramientas
        from jarvis.core.registry import registry

        catalogo = registry.schemas()
        print(f"  herramientas en el catálogo: {len(catalogo)}")

        respuesta = proveedor.chat(
            "Eres JARVIS. Usa las herramientas disponibles.",
            [Message.user("¿Cómo está mi PC?")],
            catalogo,
        )
        if respuesta.wants_tools:
            elegidas = ", ".join(t.name for t in respuesta.tool_uses)
            print(f"  CORRECTO. El modelo eligió: {elegidas}")
            for peticion in respuesta.tool_uses:
                print(f"    {peticion.name}({peticion.arguments})")
        else:
            print(f"  El modelo respondió sin usar herramientas: {respuesta.text!r}")
            print(
                "    >>> Con esa pregunta debería haber usado get_system_info. "
                "Si se repite,\n        conviene probar otro modelo o afinar "
                "los docstrings."
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO:\n  {_detalle(exc)}")
        if ok_minima:
            print(
                "\n  >>> La petición simple funciona y la del catálogo no: el "
                "problema está\n      en el esquema de alguna herramienta. Se "
                "vuelca a continuación."
            )
            print(json.dumps(catalogo, indent=2, ensure_ascii=False))

    _titulo("FIN")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - el diagnóstico nunca debe fallar en seco
        traceback.print_exc()
        raise SystemExit(1) from None
