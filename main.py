"""Punto de entrada de JARVIS.

Deliberadamente mínimo: se limita a ceder el control al cliente de consola.
Toda la lógica reside en el paquete ``jarvis``, de modo que el mismo núcleo
pueda arrancarse más adelante desde la interfaz gráfica o desde el ejecutable
empaquetado sin duplicar la preparación.
"""

from __future__ import annotations

from jarvis.ui.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
