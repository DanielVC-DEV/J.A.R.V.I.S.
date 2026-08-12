# J.A.R.V.I.S. — Arquitectura Técnica Definitiva (v1 / MVP)

**Documento de diseño previo al código.**
Fecha: agosto 2026 · Estado: propuesta para aprobación

---

## 0. Resumen ejecutivo

Tu planteamiento es sólido y las fases están bien ordenadas. Propongo **cuatro cambios estructurales** respecto a lo que planteaste, y el resto lo mantengo:

| # | Cambio | Motivo |
|---|--------|--------|
| 1 | **El núcleo es un servicio sin interfaz.** CLI, GUI y voz son clientes intercambiables del mismo núcleo. | Sin esto, la Fase 7 (GUI) obliga a reescribir el núcleo. Con esto, la GUI es solo un cliente más. |
| 2 | **`tools/` solo envuelve; nunca implementa.** La lógica real vive en `computer/`, `vision/`, `memory/`, `web/`. | En tu árbol, `tools/system_tools.py` y `computer/system.py` se solapaban. Esta regla elimina la ambigüedad. |
| 3 | **La seguridad no es la Fase 8: es la Fase 1.** El *guard* se construye con el primer tool, aunque empiece vacío. | Añadir permisos a 40 tools ya escritas es una refactorización dolorosa. Añadirlos a 3 es trivial. |
| 4 | **Para hacer clic, el árbol de accesibilidad (UI Automation) va antes que la visión.** La visión es el respaldo, no la vía principal. | UIA da coordenadas exactas y nombres reales de botones. La visión por píxeles falla con resoluciones, temas y escalado DPI. |

Y una decisión de producto importante que conviene tomar ahora, no en la Fase 10: **la API key no viaja en el instalador.** El usuario final la introduce en la GUI y se guarda cifrada en el Administrador de Credenciales de Windows (DPAPI). Esto condiciona el diseño de `config/` desde el día 1.

---

## 1. Arquitectura general

### 1.1 Principio rector

```
┌─────────────────────────────────────────────────────────┐
│  CLIENTES (intercambiables)                             │
│  CLI (Fase 1) · Voz (Fase 2) · GUI PySide6 (Fase 7)     │
└──────────────────────────┬──────────────────────────────┘
                           │  contrato único: submit(texto) → eventos
┌──────────────────────────▼──────────────────────────────┐
│  NÚCLEO (headless, sin dependencias de UI)              │
│                                                          │
│   Orquestador ──► Proveedor LLM ──► decide tool          │
│        │                                                 │
│        ▼                                                 │
│   Guard de seguridad  ── ¿SAFE / CONFIRM / BLOCKED?      │
│        │                                                 │
│        ▼                                                 │
│   Registro de Tools ──► ejecuta ──► resultado ──► LLM    │
│        │                                                 │
│        └──► Log de auditoría (JSONL)                     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  CAPACIDADES (Python puro, no saben que existe un LLM)  │
│  computer/ · vision/ · memory/ · web/ · files/           │
└─────────────────────────────────────────────────────────┘
```

**La regla de oro:** las capas de abajo nunca importan las de arriba. `computer/volume.py` no sabe qué es un LLM ni qué es una ventana de PySide6. Se puede probar con un `pytest` normal, sin API key y sin micrófono. Esto es lo que hace que el proyecto sea testeable y escalable.

### 1.2 El bucle del agente

```
usuario: "JARVIS, abre Chrome y súbeme el volumen"
   │
   ├─► LLM recibe: mensajes + catálogo de tools
   │
   ├─◄ LLM responde: [tool_use: open_program(name="chrome"),
   │                   tool_use: set_volume(level=80)]
   │
   ├─► Guard: open_program → SAFE  → ejecutar
   │   Guard: set_volume   → SAFE  → ejecutar
   │
   ├─► Resultados devueltos al LLM como tool_result
   │
   └─◄ LLM: "Listo. Chrome abierto y volumen al 80%."
```

El bucle se repite mientras el LLM siga pidiendo tools, con un **límite duro de iteraciones** (p. ej. 8) para evitar bucles infinitos que quemen tokens.

### 1.3 Comunicación por eventos, no por `print()`

El núcleo **no imprime nada**. Emite eventos tipados:

```python
@dataclass
class Event: ...

class ThinkingStarted(Event): ...
class ToolCalled(Event):        name: str; args: dict
class ConfirmationRequired(Event): name: str; args: dict; reason: str
class TokenStreamed(Event):     text: str
class ResponseCompleted(Event): text: str
class ErrorOccurred(Event):     message: str
```

La CLI convierte eventos en `print()`. La GUI los convierte en señales de Qt. El motor de voz convierte `ResponseCompleted` en audio. **Un solo núcleo, tres presentaciones, cero duplicación.**

---

## 2. Stack tecnológico definitivo

### 2.1 Base

| Componente | Elección | Por qué esta y no otra |
|---|---|---|
| Lenguaje | **Python 3.11.x (64-bit)** | No uses 3.13 todavía. Todo el stack nativo (CTranslate2, onnxruntime, pywin32, comtypes) tiene ruedas precompiladas maduras para 3.11. Ahorra horas de errores de compilación. |
| Gestor de entorno | `venv` + `pip` | Suficiente. `uv` si quieres velocidad, pero añade una pieza más al instalador. |
| Linter/formateador | **Ruff** | Reemplaza a flake8, isort, black y pylint en una sola herramienta. Configurado en `pyproject.toml`. |
| Tipado | `mypy` en modo permisivo | Los type hints son *obligatorios* en las tools: de ahí se genera el esquema JSON automáticamente. |
| Tests | `pytest` | Con `pytest-mock` para simular el LLM. |
| Control de versiones | Git + `.gitignore` estricto | `.env`, `*.db`, `dist/`, `build/`, modelos descargados. |

### 2.2 Cerebro (LLM) — modo online, con capa de abstracción

Elegiste **API en la nube**, que es lo correcto para el MVP: el *function calling* nativo es fiable, la latencia es baja y no dependes de tu GPU para pensar (la reservas para Whisper).

```python
# ai/provider.py — la interfaz que todo backend debe cumplir
class LLMProvider(Protocol):
    def chat(self,
             messages: list[Message],
             tools: list[ToolSchema]) -> LLMResponse: ...
```

- **Implementación 1 (MVP):** `AnthropicProvider` — SDK `anthropic`, tool use nativo, soporte multimodal para la Fase 5 (visión) sin cambiar de modelo.
- **Implementación 2 (Fase 9):** `OllamaProvider` — misma interfaz, backend local. Se enchufa sin tocar el orquestador.

> Guarda el nombre del modelo en configuración, nunca hardcodeado. Los modelos cambian; tu código no debería.

### 2.3 Voz

| Etapa | Tecnología | Notas |
|---|---|---|
| Captura de audio | `sounddevice` | Más limpio que PyAudio, sin dependencias de compilación en Windows. |
| Detección de habla (VAD) | **Silero VAD** (vía `torch` o el ONNX suelto) | Crítico: evita transcribir silencio. Recorta el audio a los segmentos con voz antes de pasarlo a Whisper. |
| Palabra de activación | **openWakeWord** | 🎯 **Ventaja clave: trae un modelo preentrenado `hey_jarvis`** (verificado en el repositorio oficial). No necesitas entrenar nada. Corre en CPU con consumo mínimo. Matiz: activa con **"hey JARVIS"**, no con "JARVIS" a secas; si quieres solo la palabra suelta, hay que entrenar un modelo propio o usar Picovoice Porcupine (mejor precisión, licencia de pago para distribución). |
| Speech-to-Text | **faster-whisper** (CTranslate2) | Con tu GPU NVIDIA: modelo `large-v3-turbo` en `float16`, transcripción casi instantánea. Fija `language="es"` para no perder tiempo autodetectando. |
| Text-to-Speech | **edge-tts** | Voces neuronales gratuitas. Para una identidad propia y elegante en español: `es-ES-AlvaroNeural` o `es-MX-JorgeNeural`. Requiere internet (aceptable: el LLM también). |
| Reproducción | `pygame.mixer` o `just_playback` | `just_playback` si quieres *barge-in* (interrumpir a JARVIS hablando). |

**Aviso de instalación:** `faster-whisper` en GPU necesita las DLL de **cuDNN 9** y **cuBLAS** accesibles en el `PATH`. Es el punto de fricción número uno del proyecto. Solución limpia: instalar `nvidia-cudnn-cu12` y `nvidia-cublas-cu12` por pip dentro del venv, y añadir sus carpetas al `PATH` en tiempo de ejecución desde el código. Lo dejaremos resuelto en la Fase 2.

### 2.4 Control de Windows

| Capacidad | Tecnología | Justificación |
|---|---|---|
| Volumen / silencio | **`pycaw`** | Control real del mixer de Windows (Core Audio API). Muy superior a simular teclas de volumen: puedes leer el nivel actual y fijar un valor exacto. |
| Multimedia (play/pausa/siguiente) | `pynput` enviando teclas virtuales | Aquí sí son las teclas correctas: son globales del sistema. |
| Teclado y ratón | `pyautogui` + `pynput` | `pyautogui` para lo general. Para videojuegos y apps con DirectInput, `pydirectinput` (Fase 5). |
| Ventanas | `pygetwindow` (MVP) → `uiautomation`/`pywinauto` (Fase 5) | `pygetwindow` lista, enfoca, minimiza. UIA lee el árbol real de controles. |
| Info del sistema | `psutil` + `wmi` | CPU, RAM, disco, batería, procesos. |
| Abrir programas | **Resolver propio en 4 niveles** (ver abajo) | Es más complejo de lo que parece. Merece su propio apartado. |

#### El resolvedor de aplicaciones (una pieza que conviene hacer bien)

`os.system("chrome")` no funciona. La estrategia robusta consulta cuatro fuentes en orden:

1. **Alias del usuario** en SQLite — lo que JARVIS aprendió ("mi proyecto" → `D:\Proyectos\JARVIS`).
2. **Índice del Menú Inicio** — recorre los `.lnk` de `%ProgramData%\Microsoft\Windows\Start Menu` y `%AppData%\...`, se construye una vez al arrancar y se cachea. Cubre el 80 % de los casos.
3. **Registro `App Paths`** — `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths` contiene las rutas registradas por los instaladores.
4. **Apps UWP/Store** — vía `shell:AppsFolder` (Discord de la Store, Calculadora, Configuración...).

Con coincidencia difusa (`rapidfuzz`) para que "crome", "chrome" y "google chrome" resuelvan a lo mismo. **Si hay ambigüedad, JARVIS pregunta en vez de adivinar.**

### 2.5 Visión

| Etapa | Tecnología |
|---|---|
| Captura | **`mss`** — captura de pantalla ~10× más rápida que `pyautogui`, soporta multimonitor. |
| Preproceso | `Pillow` — redimensionar a ≤1568 px de lado largo antes de enviar. Reduce coste y latencia sin perder legibilidad. |
| Análisis | Modelo multimodal vía el mismo `LLMProvider`. |
| Localización de elementos | **`uiautomation` primero, visión después.** |

> **Esto es lo más importante del apartado de visión.** Para "abre la configuración de Minecraft", pedirle al modelo coordenadas de píxel es frágil: falla con distinto escalado DPI, tema oscuro o resolución. El árbol de UI Automation devuelve el nombre y el rectángulo exacto de cada control. Diseño correcto: **UIA para clicar en aplicaciones nativas de Windows; visión para juegos, Electron y todo lo que no exponga árbol de accesibilidad.**

### 2.6 Memoria

**SQLite (stdlib) + FTS5.** Nada de bases vectoriales en el MVP: la búsqueda de texto completo de SQLite es suficiente para preferencias y hechos, y no añade dependencias. Si la Fase 6 lo pide, se añade `sqlite-vec` sin migrar de motor.

Esquema inicial:

```sql
facts(id, key, value, category, created_at, source)   -- "proyecto_python" → "D:/Proyectos/JARVIS"
app_aliases(id, alias, target_path, use_count)        -- alimenta el resolvedor
conversations(id, started_at, ended_at)
messages(id, conversation_id, role, content, created_at)
audit_log(id, tool, args_json, result, risk, approved, created_at)
```

**La memoria es controlable por el usuario desde el día 1**, como pediste: `list_memory()`, `forget(key)`, `clear_all_memory()` (nivel CONFIRM) y un interruptor global en la configuración.

Regla de privacidad que conviene fijar ahora: **JARVIS solo recuerda lo que el usuario le pide recordar explícitamente, o lo que él propone recordar y el usuario acepta.** Nunca guarda en silencio.

### 2.7 Interfaz gráfica

**PySide6** (licencia LGPL, compatible con distribución) + `qasync` para integrar el bucle de eventos de asyncio con el de Qt, más `pystray` o el `QSystemTrayIcon` nativo para la bandeja del sistema.

Regla crítica: **el núcleo corre en un `QThread` separado**. Si el LLM tarda 3 segundos, la ventana no puede congelarse.

### 2.8 Configuración y secretos

Tres niveles, y esta separación importa para la distribución:

| Nivel | Dónde | Contenido |
|---|---|---|
| Secretos | **`keyring`** → Administrador de Credenciales de Windows (DPAPI) | API keys. Cifradas por el sistema operativo, ligadas a la cuenta de usuario. |
| Preferencias | `%APPDATA%\JARVIS\settings.json` | Modelo, voz, wake word activada, nivel de permisos. |
| Desarrollo | `.env` (solo en tu máquina, en `.gitignore`) | Comodidad durante el desarrollo. |

Validado con **`pydantic-settings`**: si falta la API key, el error es claro y accionable, no un `KeyError` a los 200 ms de arrancar.

### 2.9 Empaquetado y distribución

| Paso | Herramienta | Notas |
|---|---|---|
| Ejecutable | **PyInstaller 6.x, modo `--onedir`** | **No uses `--onefile`.** Arranca lento (descomprime en temp en cada ejecución) y dispara falsos positivos de antivirus con mucha más frecuencia. |
| Instalador | **Inno Setup 6** | Gratuito, maduro, script declarativo. Genera `JARVIS-Setup.exe`, accesos directos, entrada en "Agregar o quitar programas", inicio automático opcional y desinstalador correcto. |
| Modelos de IA | **Descarga en el primer arranque, no empaquetados** | Whisper `large-v3-turbo` pesa ~1.6 GB. Un instalador de 2 GB es inaceptable; uno de 80 MB que descarga lo que necesita, no. |
| Firma de código | Certificado (opcional, de pago) | Sin firmar, Windows SmartScreen mostrará una advertencia. Es aceptable en v1; anótalo como deuda conocida. |

---

## 3. Estructura de carpetas definitiva

Cambio principal frente a tu propuesta: **layout `src/`**. Evita que Python importe accidentalmente el directorio de trabajo en vez del paquete instalado, y es lo que PyInstaller espera.

```
JARVIS/
├── pyproject.toml              # dependencias, Ruff, pytest, metadatos
├── requirements.txt            # generado desde pyproject (para el instalador)
├── README.md
├── .env.example
├── .gitignore
├── main.py                     # punto de entrada fino: solo arranca la app
│
├── src/jarvis/
│   │
│   ├── core/                   # ◄── EL NÚCLEO (sin dependencias de UI)
│   │   ├── orchestrator.py     # el bucle del agente
│   │   ├── events.py           # eventos tipados núcleo → cliente
│   │   ├── registry.py         # decorador @tool + generación de esquemas
│   │   └── session.py          # estado de conversación, historial, contexto
│   │
│   ├── ai/
│   │   ├── provider.py         # Protocol LLMProvider
│   │   ├── anthropic_provider.py
│   │   ├── ollama_provider.py  # (Fase 9)
│   │   └── prompts.py          # system prompt = la personalidad de JARVIS
│   │
│   ├── tools/                  # ◄── SOLO ENVUELVE. Nunca implementa.
│   │   ├── system_tools.py     # volumen, info del sistema, procesos
│   │   ├── app_tools.py        # abrir/cerrar/enfocar aplicaciones
│   │   ├── input_tools.py      # teclado, ratón
│   │   ├── file_tools.py       # leer, crear, mover (dentro de la jaula)
│   │   ├── web_tools.py        # búsqueda, fetch
│   │   ├── vision_tools.py     # captura y análisis de pantalla
│   │   └── memory_tools.py     # recordar, consultar, olvidar
│   │
│   ├── computer/               # ◄── IMPLEMENTACIÓN real de Windows
│   │   ├── audio.py            # pycaw
│   │   ├── apps.py             # el resolvedor de 4 niveles
│   │   ├── windows.py          # gestión de ventanas
│   │   ├── input.py            # teclado y ratón
│   │   └── system.py           # psutil, wmi
│   │
│   ├── vision/
│   │   ├── capture.py          # mss
│   │   ├── uia.py              # árbol de accesibilidad (vía preferente)
│   │   └── analyzer.py         # análisis multimodal (respaldo)
│   │
│   ├── voice/
│   │   ├── recorder.py         # sounddevice + Silero VAD
│   │   ├── stt.py              # faster-whisper
│   │   ├── tts.py              # edge-tts
│   │   └── wake_word.py        # openWakeWord ("hey_jarvis")
│   │
│   ├── memory/
│   │   ├── database.py         # esquema y migraciones
│   │   └── manager.py          # API de alto nivel
│   │
│   ├── security/
│   │   ├── guard.py            # evaluador de riesgo (el interceptor)
│   │   ├── policies.py         # jaula de rutas, denylist de comandos
│   │   └── audit.py            # log JSONL de todo lo ejecutado
│   │
│   ├── config/
│   │   ├── settings.py         # pydantic-settings
│   │   └── paths.py            # rutas de %APPDATA%, logs, modelos
│   │
│   ├── ui/
│   │   ├── cli.py              # cliente Fase 1
│   │   ├── main_window.py      # cliente Fase 7
│   │   ├── settings_dialog.py
│   │   └── tray.py
│   │
│   └── assets/
│       ├── icons/  sounds/  models/
│
├── tests/
│   ├── test_registry.py
│   ├── test_guard.py
│   ├── test_apps_resolver.py
│   └── conftest.py             # LLM simulado, sin llamadas reales
│
└── packaging/
    ├── jarvis.spec             # PyInstaller
    └── installer.iss           # Inno Setup
```

---

## 4. Los tres contratos que definen el proyecto

Si estos tres están bien, el resto es rellenar. Los defino aquí para que los apruebes antes de escribir código.

### 4.1 Definición de una tool

Una sola declaración genera el esquema JSON para el LLM, la validación de argumentos y la política de seguridad. **Nunca se escribe un esquema JSON a mano.**

```python
@tool(risk=Risk.SAFE, category="system")
def set_volume(level: int) -> str:
    """Ajusta el volumen maestro del sistema.

    Args:
        level: Nivel de volumen entre 0 y 100.
    """
    audio.set_master_volume(level)
    return f"Volumen ajustado al {level}%."
```

El decorador introspecciona la firma y el docstring, y produce el `input_schema` que la API espera. Añadir una tool nueva = escribir una función. Nada más.

### 4.2 El guard de seguridad

```python
class Risk(Enum):
    SAFE      = "safe"       # se ejecuta sin preguntar
    CONFIRM   = "confirm"    # requiere confirmación explícita del usuario
    BLOCKED   = "blocked"    # nunca se ejecuta automáticamente
```

El guard se interpone **entre la decisión del LLM y la ejecución**, y evalúa dos cosas:

1. **Riesgo estático** — el declarado en el decorador.
2. **Riesgo dinámico** — según los argumentos reales. `delete_file("temp.txt")` y `delete_file("C:\\Windows\\System32")` son la misma tool con riesgos opuestos.

Reglas dinámicas del MVP:

- **Jaula de rutas:** las operaciones de archivo solo actúan dentro de carpetas permitidas (por defecto: Documentos, Descargas, Escritorio y las rutas que el usuario añada). Fuera de ahí → BLOCKED.
- **Escalado por volumen:** más de N archivos afectados → sube a CONFIRM con recuento exacto ("Esta acción eliminará 2.340 archivos. ¿Continúo?").
- **Denylist de comandos:** `format`, `del /s`, `rd /s`, `diskpart`, `reg delete`, `shutdown`, `vssadmin delete`, ejecución de PowerShell sin restricciones → BLOCKED.
- **Auditoría total:** toda llamada a tool se registra en JSONL, aprobada o no. Sin excepciones.

### 4.3 La personalidad

Vive en `ai/prompts.py` como un archivo de texto editable, no incrustada en el código. Directrices:

- Respuestas breves. Confirmar y actuar, no explicar lo que va a hacer.
- Trato profesional, sereno, sin florituras ni emojis.
- Identidad propia: no imita diálogos ni la voz del personaje de la película.
- Cuando usa Internet, **lo indica y cita la fuente**. Cuando responde desde su propio conocimiento, no inventa certeza.
- Ante ambigüedad, pregunta. Nunca adivina sobre acciones destructivas.

---

## 5. Qué instalar en Windows (antes de escribir la primera línea)

### 5.1 Software base

| Software | Versión | Nota |
|---|---|---|
| **Python** | 3.11.9 (64-bit) | Marca **"Add python.exe to PATH"** en el instalador. |
| **Git** | Última | Para control de versiones. |
| **VS Code** | Última | Extensiones: Python, Pylance, Ruff. |
| **Microsoft C++ Build Tools** | Última | Algunas dependencias nativas lo requieren. Solo la carga "Desarrollo para escritorio con C++". |

### 5.2 Para la GPU (necesario en la Fase 2, conviene dejarlo listo)

| Software | Nota |
|---|---|
| Driver NVIDIA actualizado | Desde GeForce Experience o la web de NVIDIA. |
| CUDA Toolkit 12.x | Necesario para CTranslate2 en GPU. |
| cuDNN 9 | El punto de fricción típico. Se puede resolver por pip dentro del venv (`nvidia-cudnn-cu12`), lo cual es más limpio que la instalación global. |

Comprueba tu GPU con: `nvidia-smi`

### 5.3 Para la Fase 10 (aún no)

- **Inno Setup 6** — generación del instalador.

### 5.4 Preparación del entorno

```powershell
cd "C:\Users\Usuario\Downloads\D. Documentos\CODIGOS\J.A.R.V.I.S"

git init
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
```

> Si PowerShell bloquea el script de activación:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### 5.5 Dependencias de la Fase 1 (solo estas — el resto llega con su fase)

```
anthropic
pydantic>=2.9
pydantic-settings>=2.6
python-dotenv>=1.0
keyring>=25.0
pycaw
comtypes>=1.4
psutil>=6.0
pywin32
rapidfuzz>=3.10
rich>=13.9          # CLI legible durante el desarrollo
```

> Instala sin fijar versión y luego congela con `pip freeze > requirements.lock.txt`. Fijar versiones a ciegas antes de instalar es la causa más común de conflictos de resolución.

Desarrollo: `ruff`, `pytest`, `pytest-mock`, `mypy`.

**Deliberadamente ausentes en la Fase 1:** whisper, edge-tts, PySide6, pyautogui, mss. Instalar 30 paquetes para probar 3 funciones multiplica las formas de fallar sin aportar nada.

---

## 6. El primer objetivo exacto a programar

### Fase 1 — "Núcleo de texto con 3 tools"

**Definición en una frase:** una aplicación de consola donde escribes una orden en español, el modelo decide qué herramienta usar, el guard la autoriza, se ejecuta sobre Windows de verdad, y JARVIS responde en texto.

**Sin voz. Sin GUI. Sin visión. Sin memoria persistente.** Solo el esqueleto que sostendrá todo lo demás.

#### Alcance cerrado

**Se construye:**

1. `config/settings.py` — carga de la API key desde `keyring` con respaldo en `.env`, validada con Pydantic.
2. `core/registry.py` — el decorador `@tool` y la generación automática de esquemas.
3. `core/events.py` — los eventos tipados.
4. `security/guard.py` — evaluación de riesgo (los tres niveles funcionando, aunque las reglas dinámicas lleguen después) y `security/audit.py` con el log JSONL.
5. `ai/provider.py` + `ai/anthropic_provider.py` — la abstracción y su primera implementación.
6. `ai/prompts.py` — la personalidad, versión 1.
7. `core/orchestrator.py` — el bucle del agente con límite de iteraciones.
8. `computer/audio.py`, `computer/system.py`, `computer/apps.py` — las implementaciones reales.
9. `tools/system_tools.py` + `tools/app_tools.py` — los envoltorios de las tres tools.
10. `ui/cli.py` — el cliente de consola con `rich`.
11. `tests/` — pruebas del registro, del guard y del resolvedor, sin llamar a la API.

**Las tres tools:**

| Tool | Riesgo | Qué hace |
|---|---|---|
| `get_system_info()` | SAFE | CPU, RAM, disco, batería, uptime. Es la más simple: sirve para validar el bucle completo sin efectos secundarios. |
| `set_volume(level: int)` | SAFE | Volumen maestro real vía pycaw. Valida el control efectivo de Windows. |
| `open_program(name: str)` | SAFE | El resolvedor de 4 niveles. Es la más compleja y la que más valor demuestra. |

#### Criterios de aceptación

Fase 1 terminada cuando estas seis frases funcionen de principio a fin:

```
> ¿cómo está mi PC?
  → get_system_info() → "CPU al 12%, 18 GB de 32 libres, disco C: al 68%."

> súbeme el volumen al 70
  → set_volume(70) → "Volumen al 70%."

> baja un poco el volumen
  → el modelo lee el nivel actual y decide el nuevo → funciona sin valor explícito

> abre Chrome
  → open_program("chrome") → Chrome se abre

> abre crome
  → coincidencia difusa → Chrome se abre igual

> abre el bloc de notas y súbeme el volumen
  → dos tools encadenadas en un solo turno
```

Y además:

- Una orden imposible ("bórrame el disco C") se rechaza con elegancia, no con un *stack trace*.
- Sin API key configurada, el mensaje de error explica qué hacer.
- `audit.jsonl` contiene una entrada por cada tool ejecutada.
- `ruff check .` sin errores; `pytest` en verde.

#### Por qué este alcance y no otro

`get_system_info` valida el bucle sin riesgo. `set_volume` valida el control real del sistema operativo. `open_program` valida la parte difícil (resolución difusa, ambigüedad, errores). Entre las tres se ejercita **todo el camino** —configuración, LLM, esquemas, guard, ejecución, auditoría, respuesta— con la superficie mínima. Cuando funcione, añadir la tool número 20 será escribir una función de diez líneas.

---

## 7. Plan de fases ajustado

Mantengo tus 10 fases con dos correcciones de orden:

| Fase | Contenido | Cambio respecto a tu plan |
|---|---|---|
| **1** | Núcleo + 3 tools + guard + auditoría | ✅ Seguridad adelantada desde la Fase 8 |
| **2** | Voz: wake word → VAD → Whisper → edge-tts | = |
| **3** | Control del PC: teclado, ratón, ventanas, archivos | = |
| **4** | Catálogo completo de tools + tools compuestas ("prepara mi entorno") | = |
| **5** | Visión: captura → UIA → análisis multimodal → clic | UIA añadido como vía preferente |
| **6** | Memoria SQLite + control de usuario | = |
| **7** | GUI PySide6 + bandeja del sistema | Sin reescritura: la GUI es otro cliente |
| **8** | Endurecimiento: reglas dinámicas, jaula de rutas, visor de logs | Ahora es *refuerzo*, no construcción desde cero |
| **9** | Optimización + backend Ollama local | Ollama movido aquí |
| **10** | PyInstaller + Inno Setup + primer arranque guiado | = |

---

## 8. Riesgos identificados

| Riesgo | Impacto | Mitigación |
|---|---|---|
| DLL de cuDNN/CUDA no encontradas | Whisper no arranca en GPU | Instalar por pip en el venv y ajustar el `PATH` desde el código. Respaldo automático a CPU con modelo `small`. |
| Antivirus marca el .exe | Los usuarios no pueden instalarlo | `--onedir` en vez de `--onefile`; a futuro, firma de código. |
| Latencia total percibida | Se siente lento y poco natural | Presupuesto objetivo: <1.5 s de fin de habla a inicio de respuesta. TTS en streaming: empezar a hablar antes de terminar de generar. |
| El LLM inventa llamadas a tools | Errores confusos | Validación estricta con Pydantic + mensaje de error devuelto al modelo para que se corrija solo. |
| Coste de tokens | Facturas sorpresa | Contador de tokens visible, historial recortado por ventana deslizante, límite de gasto configurable. |
| API key en el ejecutable distribuido | Fuga de credenciales | La key nunca se empaqueta: el usuario final introduce la suya y se guarda con DPAPI. |

---

## 9. Decisiones que necesito de ti antes de programar

1. **¿Apruebas el layout `src/jarvis/` y la regla "tools solo envuelve"?**
2. **¿El nombre del paquete es `jarvis`?** (Afecta a todos los imports; cambiarlo después es molesto.)
3. **¿Repositorio en GitHub o Git solo local por ahora?**
4. **¿Alguna de las 3 tools de la Fase 1 la cambiarías por otra que te resulte más útil?**

En cuanto confirmes, el siguiente paso es crear el esqueleto de carpetas, `pyproject.toml`, `.gitignore`, el `@tool` de `core/registry.py` y sus tests — y probar ese registro aislado antes de tocar la API.

---

*Documento vivo. Se actualiza al cerrar cada fase.*
