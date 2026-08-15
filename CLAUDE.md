# JARVIS — guía para el asistente de programación

Este archivo lo lee automáticamente Claude Code al abrir el proyecto. Contiene
las reglas y decisiones que no se deducen leyendo el código, para no tener que
volver a explicarlas en cada sesión.

**Idioma: responde siempre en español.** El código, los comentarios, los
mensajes de error y la documentación también están en español.

---

## 1. Qué es esto

Asistente de escritorio para Windows que oye, entiende, controla el sistema,
usa internet, trabaja con archivos, recuerda lo autorizado y responde por voz.
El objetivo final es un `JARVIS-Setup.exe` instalable en otro equipo sin tocar
el código fuente.

La identidad del asistente es propia: **no** imita la personalidad ni la voz
del personaje de Iron Man.

## 2. Cómo se trabaja aquí

- **Una etapa cada vez.** Nada de entregar el proyecto entero de golpe. Cada
  etapa se prueba antes de pasar a la siguiente.
- **Medir antes de ajustar.** Ninguna constante se elige a ojo. Los umbrales de
  este proyecto (relevancia de memoria, aceptación de coincidencias, espera
  ante límite de tokens) salieron de medir el caso real y están documentados
  con la medición al lado. Si hay que cambiar uno, primero se mide.
- **Un error que no dice cómo salir de él obliga a adivinar.** Todo mensaje de
  fallo debe nombrar la variable, la ruta o la acción concreta que lo resuelve.
- Comentarios profesionales que expliquen el funcionamiento y el porqué. Nunca
  comentarios temporales del tipo «esto es para probar».
- Archivos cortos y con una responsabilidad clara. Sin duplicación.
- Antes de dar por terminado un cambio: `ruff check .` y `pytest`.

## 3. Reglas de arquitectura

Son cuatro y no se rompen sin discutirlo antes.

1. **Núcleo sin interfaz.** El orquestador emite eventos tipados
   (`core/events.py`). La consola, el bucle de voz y la ventana Qt son tres
   clientes que consumen los mismos eventos. Ningún cliente toca el núcleo, y
   añadir un cuarto no debe obligar a modificar el orquestador.
2. **Las herramientas se declaran, no se describen a mano.** El decorador
   `@tool` de `core/registry.py` genera el esquema JSON desde las anotaciones
   de tipo y el docstring estilo Google. No se escribe ningún esquema a mano.
   El riesgo por defecto es `CONFIRM`: olvidarse de indicarlo cae del lado
   seguro.
3. **El modelo nunca toca el sistema directamente.** Todo pasa por el registro
   de herramientas y por el `Guard`. Las políticas del guard solo pueden
   **endurecer** un veredicto, nunca relajarlo.
4. **El proveedor de IA es intercambiable.** `ai/provider.py` define tipos
   neutros (`Message`, `TextBlock`, `ToolUse`, `ToolResultBlock`) y cada
   proveedor traduce. Cambiar de Anthropic a Groq, OpenRouter u Ollama es
   cuestión de `.env`, no de código.

## 4. Seguridad — restricciones obligatorias

- **Las claves de API nunca van escritas en el código.** Solo `.env` o
  variables de entorno. `.env` no se sube al repositorio; `.env.example`
  documenta las variables sin valores reales.
- **Tres niveles de riesgo**: `Risk.SAFE` (se ejecuta), `Risk.CONFIRM` (pide
  autorización al usuario), `Risk.BLOCKED` (no se ejecuta).
- **Nunca ejecutar órdenes destructivas sin autorización explícita.** El guard
  mantiene una lista de patrones peligrosos (`format`, `del /s`, `diskpart`,
  `vssadmin delete`, `rm -rf`…).
- **Borrar significa papelera de reciclaje** (`send2trash`), jamás borrado
  permanente.
- **La jaula de rutas** solo se ensancha desde configuración
  (`JARVIS_ALLOWED_PATHS`). El modelo no puede ampliarla por su cuenta.
- **Direcciones web**: solo `http`/`https`, y se resuelve el nombre antes de
  pedir la página para rechazar direcciones privadas, de bucle local o
  reservadas. Lo que no se resuelve se considera no fiable.
- **La memoria es del usuario.** Debe poder verla, borrar una entrada,
  desactivarla y vaciarla entera.
- Todo lo que se ejecuta queda registrado en el diario de auditoría
  (`security/audit.py`), que nunca lanza excepción al escribir.

## 5. Estructura

```
main.py                  elige cliente: consola, --voz, --gui
src/jarvis/
  ai/          proveedores de LLM, tipos neutros, prompt de identidad
  config/      ajustes (pydantic-settings, prefijo JARVIS_) y rutas
  core/        registro de herramientas, orquestador, eventos
  security/    riesgo, guard con políticas, auditoría
  computer/    aplicaciones, ventanas, entrada, archivos, audio, sistema
               + matching.py (coincidencia difusa compartida)
  voice/       captura, VAD, transcripción, palabra de activación, TTS
  memory/      SQLite con migraciones por PRAGMA user_version
  web/         seguridad de URL, extracción de texto, cliente
  tools/       las 33 herramientas expuestas al modelo
  ui/          cli.py, voice_loop.py, gui/
tests/         405 pruebas
docs/          ARQUITECTURA.md, PALABRA_DE_ACTIVACION.md
scripts/       instalar.ps1, diagnostico.py, probar_voz.py
```

**Separación deliberada entre lo que depende del sistema operativo y lo que
no.** Las aplicaciones separan el indexado de la coincidencia; la voz separa la
captura del VAD; la interfaz separa Qt del formato. Así lo difícil se puede
probar sin Windows y sin entorno gráfico.

## 6. El presupuesto de tokens manda

El plan gratuito de Groq da **8000 tokens por minuto**. El catálogo de
herramientas es un coste **fijo que se paga en cada vuelta** del bucle: 33
herramientas ≈ 2736 tokens por vuelta. Una orden que necesite tres vueltas
agota el minuto.

Consecuencias prácticas:

- **Añadir una herramienta cuesta tokens en todas las órdenes**, no solo en las
  que la usan. Antes de añadir una, valorar si cabe en otra existente.
- Las descripciones de las herramientas se mantienen cortas a propósito. No
  «mejorarlas» alargándolas.
- `JARVIS_TOOL_CATEGORIES` permite cargar solo las categorías necesarias.
- El reintento ante error 429 detecta si el límite es de tokens por minuto y
  espera de verdad (`MIN_TOKEN_QUOTA_WAIT = 6.0`). Reintentar rápido empeora el
  problema: consume la cuota tres veces en lugar de una.
- El prompt del sistema incluye la regla anti-repetición: nunca repetir una
  herramienta que ya se ejecutó con éxito en el mismo turno.

## 7. Detalles que costaron encontrarlos

Están resueltos. Anotados para no reintroducirlos.

- **Escribir texto en Windows va por portapapeles**, no por pulsaciones
  simuladas: las pulsaciones estropean tildes y ñ. El portapapeles se restaura
  en un `finally`.
- **Un solo flujo de audio por sesión de voz.** El controlador MME falla
  (`PortAudioError -9999`) si se abre y cierra rápido. La calibración ocurre
  *dentro* del flujo ya abierto.
- **openWakeWord solo entiende inglés.** El modelo `hey_jarvis` da 0.05 con
  pronunciación española frente a 0.9 de `alexa` en inglés. La solución
  documentada en `docs/PALABRA_DE_ACTIVACION.md` es entrenar un modelo propio
  con grafías fonéticas (yarbis, yarviss, jarviss, dyarvis).
- **Normalización NFD, no NFKD.** NFKD convierte `™` en «TM» y ensucia las
  comparaciones.
- **Las palabras vacías del español rompían la memoria.** «el» coincidía dentro
  de «cumpleaños» como subcadena. Con `_STOP_WORDS` la separación pasó de
  71–83 frente a 65–75 (indistinguible) a 88–96 frente a 33–37. De ahí sale
  `RELEVANCE_THRESHOLD = 60.0`.
- **Puntuación por palabra compuesta** (`0.5·máximo + 0.5·media`) porque «vs
  code» quedaba en 65.5, por debajo del umbral de 70.
- **Whisper transcribía «eyjarvís».** Se corrige pasando `stt_vocabulary` como
  `prompt` de la transcripción.
- **Las cabeceras de autenticación van por petición**, no en el cliente httpx:
  si se inyecta un cliente desde fuera, las cabeceras del cliente se pierden.
- **El fallo de certificado llega envuelto en `ConnectError`**, así que esa
  comprobación va antes que las de tipo.
- **`open_program` no admite rutas.** Para carpetas existe `open_folder`.
- **El primer turno contra Ollama en frío puede superar los 60 s** de
  `REQUEST_TIMEOUT_SECONDS` (pensado para APIs en la nube) mientras el modelo
  se carga en memoria. Se resuelve solo probando de nuevo, sin tocar código:
  con el modelo ya cargado, `llama3.1` de 8B respondió y llamó a una
  herramienta con normalidad. Si molesta, se puede precalentar con
  `ollama run <modelo>` antes de abrir JARVIS.

## 8. Estado por etapas

| Etapa | Contenido | Estado |
|-------|-----------|--------|
| 1 | Núcleo de texto, registro, guard, 3 herramientas | hecha |
| 2 | Control de Windows (apps, ventanas, entrada, audio, sistema) | hecha |
| 3 | Archivos e internet | hecha |
| 4 | Voz completa: STT, activación, TTS | hecha |
| 5 | Visión (capturas de pantalla) | **pendiente** — la cuenta de Groq no tiene modelo con visión; haría falta Gemini u otro proveedor |
| 6 | Memoria persistente | hecha |
| 7 | Interfaz gráfica | hecha |
| 8 | Endurecimiento: reglas dinámicas, visor del diario | hecha |
| 9 | Optimización y backend local con Ollama | hecha |
| 10 | PyInstaller `--onedir` + instalador Inno Setup | pendiente |

**Lo siguiente:** etapa 10. Empaquetar con PyInstaller `--onedir` y un
instalador Inno Setup, para tener un `JARVIS-Setup.exe` instalable sin tocar
el código fuente.

## 9. Órdenes habituales

```bat
pip install -e ".[dev,gui]"     :: instalación de desarrollo
pytest                          :: 398 pruebas
ruff check .                    :: linter
python main.py                  :: consola
python main.py --gui --verbose  :: ventana
python main.py --voz            :: micrófono
python scripts/diagnostico.py   :: comprobar configuración y conexión
python scripts/probar_voz.py    :: comprobar la cadena de voz
```

Python 3.11 o 3.12 (3.13+ no está soportado por las dependencias de voz).
