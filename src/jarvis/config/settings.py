"""Carga y validación de la configuración del asistente.

La configuración procede de tres orígenes, consultados en este orden de
prioridad:

1. Variables de entorno con el prefijo ``JARVIS_``.
2. El archivo ``.env`` del directorio de trabajo (solo durante el desarrollo).
3. El Administrador de Credenciales de Windows, exclusivamente para la clave
   de API.

Esta separación es deliberada de cara a la distribución: la aplicación
instalada no incluye ningún ``.env``. El usuario final introduce su clave en
la interfaz y esta se almacena cifrada por el sistema operativo mediante
``keyring``.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "KNOWN_ENDPOINTS",
    "ConfigurationError",
    "Provider",
    "Settings",
    "SttBackend",
    "delete_api_key",
    "load_settings",
    "store_api_key",
]

#: Identificadores empleados al guardar la clave en el almacén de credenciales.
KEYRING_SERVICE = "JARVIS"
KEYRING_USERNAME = "api_key"

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class Provider(StrEnum):
    """Familia de API con la que se habla."""

    ANTHROPIC = "anthropic"
    """API nativa de Anthropic."""

    OPENAI = "openai"
    """Cualquier servicio que exponga el formato de OpenAI.

    Lo hacen Groq, OpenRouter, Ollama, Cerebras, Mistral y otros, de modo que
    una sola implementación los cubre todos.
    """


#: Atajos para los servicios compatibles más habituales. Permiten escribir
#: ``JARVIS_BASE_URL=groq`` en lugar de la dirección completa.
KNOWN_ENDPOINTS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

#: Modelo empleado cuando el usuario no indica ninguno. Solo se conoce uno por
#: omisión para la API nativa; en los servicios compatibles el catálogo varía
#: demasiado como para suponer nada, así que se exige indicarlo.
_DEFAULT_MODELS: dict[Provider, str] = {
    Provider.ANTHROPIC: "claude-sonnet-5",
}


class SttBackend(StrEnum):
    """Motor empleado para transcribir la voz."""

    REMOTE = "remote"
    """Servicio remoto compatible con OpenAI, como el Whisper de Groq."""

    LOCAL = "local"
    """faster-whisper ejecutándose en el propio equipo."""


#: Modelo de transcripción por omisión de cada motor. Los identificadores
#: difieren: el servicio remoto usa el nombre publicado en su catálogo y la
#: biblioteca local el del repositorio de modelos.
_DEFAULT_STT_MODELS: dict[SttBackend, str] = {
    SttBackend.REMOTE: "whisper-large-v3-turbo",
    SttBackend.LOCAL: "large-v3-turbo",
}


class ConfigurationError(RuntimeError):
    """La configuración es incompleta o inválida.

    El mensaje está redactado para que el usuario sepa exactamente qué hacer,
    en lugar de exponer una traza interna.
    """


# --------------------------------------------------------------------------- #
# Almacén de credenciales
# --------------------------------------------------------------------------- #


def _keyring() -> Any:
    """Importa ``keyring`` de forma perezosa.

    Se importa bajo demanda porque durante las pruebas y en sistemas que no son
    Windows puede no estar disponible, y su ausencia no debe impedir cargar la
    configuración desde variables de entorno.
    """
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _api_key_from_keyring() -> str | None:
    """Recupera la clave de API del almacén de credenciales del sistema.

    Returns:
        La clave almacenada, o ``None`` si no existe o el almacén no está
        disponible. Nunca lanza excepciones: la ausencia de clave es una
        situación esperada que resuelve la capa superior.
    """
    keyring = _keyring()
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:  # noqa: BLE001 - el almacén puede fallar de muchas formas
        return None


def store_api_key(api_key: str) -> None:
    """Guarda la clave de API cifrada en el almacén de credenciales.

    Args:
        api_key: Clave a almacenar.

    Raises:
        ConfigurationError: Si el almacén de credenciales no está disponible.
    """
    keyring = _keyring()
    if keyring is None:
        raise ConfigurationError(
            "El almacén de credenciales no está disponible. Instala el paquete "
            "«keyring» o define JARVIS_API_KEY en el archivo .env."
        )
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
    load_settings.cache_clear()


def delete_api_key() -> None:
    """Elimina la clave de API del almacén de credenciales, si existe."""
    keyring = _keyring()
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:  # noqa: BLE001 - no existir es un desenlace aceptable
        pass
    load_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


class Settings(BaseSettings):
    """Configuración efectiva de la aplicación."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    provider: Provider = Field(
        default=Provider.ANTHROPIC,
        description="Familia de API con la que se habla.",
    )

    api_key: SecretStr | None = Field(
        default=None,
        description="Clave de la API del proveedor de IA.",
    )

    base_url: str = Field(
        default="",
        description=(
            "Dirección del servicio compatible. Admite un atajo conocido "
            "(«groq», «ollama»...) o una URL completa. Se ignora con la API "
            "nativa de Anthropic."
        ),
    )

    model: str = Field(
        default="",
        description=(
            "Identificador del modelo empleado como cerebro del asistente. "
            "Si se omite, se usa el modelo por omisión del proveedor. Se "
            "prefiere uno equilibrado: en un asistente de voz la latencia "
            "percibida pesa tanto como la capacidad de razonamiento."
        ),
    )

    max_tokens: int = Field(
        default=1024,
        ge=64,
        description=(
            "Límite de tokens por respuesta. Las respuestas del asistente son "
            "breves por diseño; un límite bajo acota el gasto."
        ),
    )

    max_tool_iterations: int = Field(
        default=8,
        ge=1,
        le=25,
        description=(
            "Número máximo de vueltas del bucle de herramientas en un mismo "
            "turno. Evita que un fallo de razonamiento derive en un bucle "
            "infinito que consuma tokens."
        ),
    )

    log_level: str = Field(default="INFO")

    # -- Voz ---------------------------------------------------------------- #

    stt_backend: SttBackend = Field(
        default=SttBackend.REMOTE,
        description="Motor de transcripción: «remote» o «local».",
    )

    stt_model: str = Field(
        default="",
        description="Modelo de transcripción. Si se omite, el propio del motor.",
    )

    stt_language: str = Field(
        default="es",
        description=(
            "Idioma esperado. Fijarlo evita que el modelo lo deduzca, lo que "
            "ahorra tiempo y elimina los saltos a otra lengua en frases cortas."
        ),
    )

    stt_base_url: str = Field(
        default="",
        description=(
            "Dirección del servicio de transcripción. Si se omite, se reutiliza "
            "la del modelo de lenguaje cuando es compatible."
        ),
    )

    stt_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Clave del servicio de transcripción. Si se omite, se reutiliza la "
            "del modelo de lenguaje."
        ),
    )

    stt_device: str = Field(
        default="auto",
        description="Dispositivo del motor local: «auto», «cuda» o «cpu».",
    )

    stt_vocabulary: str = Field(
        default=(
            "JARVIS. Chrome, Discord, Spotify, Steam, Visual Studio Code, "
            "Windows, Python."
        ),
        description=(
            "Vocabulario de referencia para la transcripción. Whisper lo usa "
            "para reconocer nombres propios que de otro modo escribiría a "
            "oído: sin él, «JARVIS» se transcribe como «eyjarvís». Añade aquí "
            "los nombres de los programas que uses."
        ),
    )

    mic_device: int | None = Field(
        default=None,
        description=(
            "Índice del micrófono. Si se omite, el predeterminado del sistema. "
            "Ejecuta «python scripts/probar_voz.py» para ver los disponibles."
        ),
    )

    wake_word_enabled: bool = Field(
        default=True,
        description="Si el asistente debe escuchar su nombre de continuo.",
    )

    wake_word: str = Field(
        default="hey_jarvis",
        description="Modelo de palabra de activación de openWakeWord.",
    )

    wake_word_threshold: float = Field(
        default=0.5,
        ge=0.05,
        le=0.99,
        description=(
            "Confianza mínima para dar por buena una activación. Subirlo "
            "reduce los disparos accidentales; bajarlo, las repeticiones."
        ),
    )

    tts_enabled: bool = Field(
        default=True,
        description="Si el asistente responde en voz alta.",
    )

    tts_voice: str = Field(
        default="es-CL-LorenzoNeural",
        description=(
            "Voz neuronal empleada al hablar. Véase SUGGESTED_VOICES en "
            "jarvis.voice.tts para las alternativas en español."
        ),
    )

    tts_rate: str = Field(
        default="+0%",
        description=(
            "Ajuste de la velocidad del habla, como «+15%» o «-10%». Un "
            "asistente que confirma acciones se agradece algo más rápido."
        ),
    )

    hotkey: str = Field(
        default="f9",
        description=(
            "Tecla que se mantiene pulsada para hablar. Alternativa a la "
            "palabra de activación, útil cuando no conviene escuchar siempre."
        ),
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> str:
        """Acepta el nivel de registro sin distinguir mayúsculas."""
        level = str(value).strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(
                f"Nivel de registro no reconocido: '{value}'. "
                f"Valores admitidos: {', '.join(sorted(_LOG_LEVELS))}."
            )
        return level

    @field_validator("model", "base_url", "stt_model", "stt_base_url", mode="before")
    @classmethod
    def _normalise_optional_text(cls, value: object) -> object:
        """Recorta los espacios sobrantes de los valores opcionales.

        Copiar una clave o una dirección desde el navegador arrastra con
        frecuencia un salto de línea, y el servicio lo rechaza con un error
        desconcertante.
        """
        return value.strip() if isinstance(value, str) else value

    @field_validator("provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        """Acepta el nombre del proveedor sin distinguir mayúsculas."""
        if isinstance(value, str):
            texto = value.strip().lower()
            if texto not in set(Provider):
                admitidos = ", ".join(sorted(Provider))
                raise ValueError(
                    f"Proveedor no reconocido: '{value}'. Admitidos: {admitidos}."
                )
            return texto
        return value

    # -- Resolución de valores derivados ------------------------------------ #

    def resolved_model(self) -> str:
        """Devuelve el modelo efectivo.

        Returns:
            El identificador indicado por el usuario o, en su defecto, el
            predeterminado del proveedor.

        Raises:
            ConfigurationError: Si el proveedor no tiene modelo por omisión y
                el usuario no indicó ninguno.
        """
        if self.model:
            return self.model

        predeterminado = _DEFAULT_MODELS.get(self.provider)
        if predeterminado:
            return predeterminado

        raise ConfigurationError(
            f"El proveedor «{self.provider}» no tiene un modelo por omisión. "
            "Indica JARVIS_MODEL en el archivo .env.\n"
            "Ejecuta «python scripts/diagnostico.py» para ver qué modelos "
            "admite tu cuenta."
        )

    def resolved_base_url(self) -> str:
        """Devuelve la dirección efectiva del servicio.

        Traduce los atajos conocidos —«groq», «ollama»— a su dirección
        completa.

        Returns:
            La dirección del servicio, sin barra final.

        Raises:
            ConfigurationError: Si el proveedor la requiere y no se indicó.
        """
        if self.provider is Provider.ANTHROPIC:
            return ""

        destino = KNOWN_ENDPOINTS.get(self.base_url.lower(), self.base_url)
        if not destino:
            atajos = ", ".join(sorted(KNOWN_ENDPOINTS))
            raise ConfigurationError(
                "Falta JARVIS_BASE_URL. Indica una dirección completa o uno de "
                f"estos atajos: {atajos}."
            )
        return destino.rstrip("/")

    def resolved_stt_model(self) -> str:
        """Devuelve el modelo de transcripción efectivo."""
        return self.stt_model or _DEFAULT_STT_MODELS[self.stt_backend]

    def resolved_stt_base_url(self) -> str:
        """Devuelve la dirección efectiva del servicio de transcripción.

        Si no se indica una propia, se reutiliza la del modelo de lenguaje
        cuando este ya apunta a un servicio compatible. Es el caso habitual:
        quien usa Groq para pensar puede usarlo también para oír, con la misma
        clave.

        Returns:
            La dirección del servicio, sin barra final.

        Raises:
            ConfigurationError: Si no hay ninguna dirección deducible.
        """
        if self.stt_base_url:
            destino = KNOWN_ENDPOINTS.get(self.stt_base_url.lower(), self.stt_base_url)
            return destino.rstrip("/")

        if self.provider is Provider.OPENAI:
            return self.resolved_base_url()

        raise ConfigurationError(
            "Falta JARVIS_STT_BASE_URL. El modelo de lenguaje no usa un "
            "servicio compatible, así que la transcripción necesita el suyo "
            "propio (por ejemplo «groq»)."
        )

    def resolved_stt_api_key(self) -> str:
        """Devuelve la clave del servicio de transcripción.

        Returns:
            La clave propia si se indicó, la del modelo de lenguaje en caso
            contrario, o una cadena vacía si no hay ninguna. Un servicio local
            puede no necesitarla.
        """
        if self.stt_api_key is not None:
            return self.stt_api_key.get_secret_value()
        if self.api_key is not None:
            return self.api_key.get_secret_value()
        return ""

    # -- Utilidades -------------------------------------------------------- #

    def require_api_key(self) -> str:
        """Devuelve la clave de API, o falla con un mensaje accionable.

        Returns:
            La clave en texto plano, lista para el cliente de la API.

        Raises:
            ConfigurationError: Si no hay ninguna clave configurada.
        """
        if self.api_key is None:
            raise ConfigurationError(
                "No hay ninguna clave de API configurada.\n"
                "Durante el desarrollo: copia «.env.example» como «.env» y "
                "rellena JARVIS_API_KEY.\n"
                "En la aplicación instalada: introduce la clave en la pantalla "
                "de configuración."
            )
        return self.api_key.get_secret_value()

    def has_api_key(self) -> bool:
        """Indica si hay una clave disponible, sin exponer su valor."""
        return self.api_key is not None

    def __repr__(self) -> str:
        """Representación segura: nunca revela el valor de la clave."""
        estado = "configurada" if self.has_api_key() else "ausente"
        return (
            f"Settings(provider={self.provider!s}, model={self.model or '(por omisión)'!r}, "
            f"api_key=<{estado}>, max_tokens={self.max_tokens}, "
            f"log_level={self.log_level!r})"
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Construye la configuración efectiva de la aplicación.

    La clave de API se busca primero en el entorno y, si no aparece, en el
    almacén de credenciales del sistema. El resultado se memoriza: la
    configuración se lee una sola vez por proceso.

    Returns:
        La configuración validada.

    Raises:
        ConfigurationError: Si algún valor presente es inválido. La ausencia de
            clave de API no se considera un error en este punto: se señala al
            usarla, mediante ``require_api_key``.
    """
    try:
        settings = Settings()
    except Exception as exc:  # noqa: BLE001 - frontera hacia el usuario
        raise ConfigurationError(f"Configuración inválida: {exc}") from exc

    if settings.api_key is None:
        stored = _api_key_from_keyring()
        if stored:
            settings = settings.model_copy(update={"api_key": SecretStr(stored)})

    return settings
