# Instalación de JARVIS en otro equipo

> **Estado del proyecto:** en desarrollo (Fase 2 de 10).
> Todavía no existe un instalador. Poner JARVIS en otro equipo requiere, por
> ahora, preparar un entorno de desarrollo. El objetivo de descargar un
> `JARVIS-Setup.exe` y usarlo sin más corresponde a la Fase 10.

---

## 1. Requisitos del sistema

| Requisito | Detalle |
|---|---|
| Sistema operativo | Windows 10 u 11, 64 bits |
| Python | **3.11.x**, 64 bits. No uses 3.13 ni posterior |
| Git | Para clonar el proyecto |
| Conexión a internet | El modelo y la transcripción funcionan por API |
| Micrófono | Solo para la voz; el modo de texto no lo necesita |

**No hace falta:** tarjeta de crédito (Groq tiene capa gratuita), GPU, CUDA ni
cuDNN. Todo eso solo entra en juego si eliges transcribir en local, que es
opcional.

### Instalar Python 3.11

```cmd
winget install Python.Python.3.11
```

Cierra la consola y abre una nueva. Comprueba:

```cmd
py -3.11 --version
```

Si prefieres el instalador gráfico, descárgalo de python.org y marca
**«Add python.exe to PATH»**.

---

## 2. Instalación automática

Desde la carpeta del proyecto, en **PowerShell**:

```powershell
.\scripts\instalar.ps1
```

El script crea el entorno virtual, instala las dependencias, prepara el
archivo de configuración y ejecuta las pruebas para confirmar que todo quedó
correcto.

Si PowerShell bloquea la ejecución:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

## 3. Instalación manual

Equivale a lo que hace el script, paso a paso.

```cmd
git clone <url-del-repositorio> JARVIS
cd JARVIS

py -3.11 -m venv .venv
.venv\Scripts\activate.bat

python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Comprobación:

```cmd
pytest
ruff check .
```

---

## 4. Configuración

El archivo `.env` **no viaja en el repositorio**: contiene secretos y está
excluido a propósito. Hay que crearlo en cada equipo.

```cmd
copy .env.example .env
notepad .env
```

Consigue una clave gratuita en [console.groq.com](https://console.groq.com/keys)
—inicia sesión con Google o GitHub, no pide tarjeta— y complétalo así:

```
JARVIS_PROVIDER=openai
JARVIS_BASE_URL=groq
JARVIS_API_KEY=gsk_tu_clave
JARVIS_MODEL=openai/gpt-oss-120b
```

Para verificar la configuración y ver qué modelos admite tu cuenta:

```cmd
python scripts\diagnostico.py
```

---

## 5. Uso

```cmd
python main.py
```

Añade `--verbose` para ver qué herramienta se ejecuta, cuánto tarda y cuántos
tokens consume.

---

## 6. Extras opcionales

### Transcripción en el propio equipo

Solo si prefieres que tu voz no salga del ordenador, o quieres trabajar sin
conexión. Requiere una GPU NVIDIA para ser rápida.

```cmd
pip install -e ".[local-stt]"
```

Y en `.env`:

```
JARVIS_STT_BACKEND=local
```

Pesa más de un gigabyte y descarga el modelo en el primer arranque. Si CUDA no
está bien instalado, JARVIS lo detecta y recurre a la CPU con un aviso, en
lugar de fallar.

### Modelo de lenguaje en local con Ollama

Gratis, sin límites y sin internet. Instala [Ollama](https://ollama.com),
descarga un modelo compatible con herramientas y ajusta:

```
JARVIS_PROVIDER=openai
JARVIS_BASE_URL=ollama
JARVIS_MODEL=el-modelo-que-descargaste
```

---

## 7. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `No module named 'jarvis'` | Falta `pip install -e .`, o el entorno no está activo. El prompt debe empezar por `(.venv)` |
| `No suitable Python runtime found` | No está instalado Python 3.11 |
| `No hay ninguna clave de API configurada` | Falta el archivo `.env` en la raíz del proyecto, junto a `main.py` |
| `La clave de API fue rechazada` | La clave está mal copiada, o `JARVIS_PROVIDER` no corresponde al servicio de esa clave |
| `El servicio no reconoce el modelo` | Ejecuta el diagnóstico y copia un identificador de la lista |
| El volumen no cambia | pycaw no encuentra el dispositivo de audio predeterminado |
| Error de PortAudio | Windows está bloqueando el micrófono. Configuración → Privacidad → Micrófono |

Si algo no encaja, `python scripts\diagnostico.py` comprueba cada capa por
separado e indica cuál falla.

---

## 8. Lo que traerá la Fase 10

Cuando el proyecto esté terminado, instalarlo en otro equipo consistirá en:

1. Descargar `JARVIS-Setup.exe`.
2. Ejecutarlo.
3. Introducir la clave de API en la pantalla de configuración.

Sin Python, sin consola, sin `pip`. El instalador se generará con PyInstaller
e Inno Setup, y los modelos se descargarán en el primer arranque para que el
archivo de instalación siga siendo pequeño.
