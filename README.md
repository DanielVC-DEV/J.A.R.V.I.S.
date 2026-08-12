# J.A.R.V.I.S.

Asistente de inteligencia artificial de escritorio para Windows, controlado por
lenguaje natural. Interpreta órdenes del usuario, decide qué herramienta
necesita y la ejecuta sobre el sistema bajo un esquema de permisos explícito.

> **Estado:** Fase 1 en curso — núcleo de texto.
> La arquitectura completa está descrita en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).

---

## Requisitos

* Windows 10 u 11 (64 bits)
* Python 3.11.x (64 bits)
* Git

## Puesta en marcha

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[dev]"
```

> Si PowerShell bloquea el script de activación:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

Después, copia `.env.example` como `.env` y completa los valores.

## Comprobación del entorno

```powershell
pytest          # el conjunto de pruebas debe pasar por completo
ruff check .    # sin errores de estilo
```

Las pruebas no requieren clave de API ni conexión a Internet.

---

## Arquitectura en una frase

El núcleo carece de interfaz: la consola, la voz y la ventana gráfica son
clientes intercambiables que se comunican con él mediante eventos tipados.

```
Cliente (CLI / voz / GUI)
      │  submit(texto)  ▲  eventos
      ▼                 │
   Orquestador ──► Proveedor LLM ──► decide herramienta
      │
      ▼
   Guardia de seguridad ──► Registro ──► ejecuta ──► auditoría
      │
      ▼
   Capacidades (Windows, visión, memoria, web)
```

**Regla de dependencias:** las capas inferiores nunca importan las superiores.
`computer/audio.py` desconoce por completo la existencia de un modelo de
lenguaje o de una ventana de Qt, y por eso puede probarse de forma aislada.

**Regla de los envoltorios:** `tools/` expone herramientas al modelo pero no
implementa nada. La lógica real vive en `computer/`, `vision/`, `memory/` y
`web/`.

---

## Cómo se declara una herramienta

Una sola declaración produce el esquema JSON, la validación de argumentos y la
clasificación de riesgo. No se escribe ningún esquema JSON a mano.

```python
from jarvis.core.registry import tool
from jarvis.security.risk import Risk


@tool(risk=Risk.SAFE, category="system")
def set_volume(level: int) -> str:
    """Ajusta el volumen maestro del sistema.

    Args:
        level: Nivel de volumen entre 0 y 100.
    """
    audio.set_master_volume(level)
    return f"Volumen ajustado al {level}%."
```

El registro deriva de ahí el esquema que recibe el modelo:

```json
{
  "name": "set_volume",
  "description": "Ajusta el volumen maestro del sistema.",
  "input_schema": {
    "type": "object",
    "properties": {
      "level": {
        "type": "integer",
        "description": "Nivel de volumen entre 0 y 100."
      }
    },
    "required": ["level"]
  }
}
```

Consideraciones de diseño relevantes:

* El decorador **devuelve la función intacta**, de modo que sigue siendo
  invocable y comprobable sin pasar por el registro.
* El riesgo por omisión es `CONFIRM`, no `SAFE`: una herramienta cuyo riesgo se
  olvidó declarar pedirá confirmación en lugar de ejecutarse en silencio.
* Los errores **no se propagan como excepciones**. Se devuelven en un
  `ToolResult` redactado para que el modelo pueda leerlo y corregirse solo.

---

## Niveles de riesgo

| Nivel | Comportamiento |
|---|---|
| `SAFE` | Se ejecuta automáticamente. |
| `CONFIRM` | Requiere confirmación explícita del usuario. |
| `BLOCKED` | Nunca se ejecuta de forma automática. |

El valor del decorador es el *riesgo estático*. El guardia de seguridad puede
elevarlo en tiempo de ejecución según los argumentos: borrar un archivo
temporal y borrar un directorio del sistema son la misma herramienta con
consecuencias opuestas.

---

## Estructura del proyecto

```
src/jarvis/
├── core/        orquestación, registro de herramientas, eventos
├── ai/          proveedores de modelos y personalidad
├── tools/       envoltorios expuestos al modelo (sin lógica propia)
├── computer/    interacción real con Windows
├── security/    riesgo, políticas y auditoría
├── config/      configuración y secretos
└── ui/          clientes: consola, ventana gráfica, bandeja
```

Los paquetes `vision/`, `voice/` y `memory/` se incorporarán en sus fases
correspondientes.

---

## Convenciones

* Formato y análisis estático con **Ruff**; tipado verificado con **mypy**.
* Anotaciones de tipo obligatorias en las herramientas: de ellas se deriva el
  esquema JSON.
* Docstrings en estilo Google. En las herramientas, el docstring **es** la
  descripción que lee el modelo.
* Los secretos nunca se escriben en el código ni se versionan.
