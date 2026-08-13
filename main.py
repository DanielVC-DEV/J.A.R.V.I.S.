"""Punto de entrada de JARVIS.

Deliberadamente mínimo: elige el cliente y le cede el control. Toda la lógica
reside en el paquete ``jarvis``, de modo que el mismo núcleo pueda arrancarse
más adelante desde la interfaz gráfica o desde el ejecutable empaquetado sin
duplicar la preparación.

Uso::

    python main.py              # consola de texto
    python main.py --voz        # escuchando por micrófono
    python main.py --verbose    # con detalle de herramientas y tokens
"""

from __future__ import annotations

import sys


def main() -> int:
    """Arranca el cliente indicado en la línea de órdenes."""
    if "--voz" in sys.argv:
        from jarvis.ui.voice_app import main as voice_main

        return voice_main()

    from jarvis.ui.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
