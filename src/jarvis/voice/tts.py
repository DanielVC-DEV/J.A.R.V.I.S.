"""Síntesis de voz para las respuestas del asistente.

Emplea el servicio de voces neuronales de Microsoft Edge, gratuito y de
calidad muy superior a la síntesis integrada en Windows. Requiere conexión,
lo cual es aceptable porque el modelo de lenguaje también la necesita.

El módulo separa dos responsabilidades que conviene no mezclar:

* **Síntesis**: convertir texto en audio. Depende del servicio.
* **Reproducción**: sacar ese audio por los altavoces. Depende del sistema.

Esa separación permite silenciar al asistente sin tocar la síntesis, y
sustituir el reproductor —para la interfaz gráfica, o para poder interrumpirlo
cuando el usuario vuelva a hablar— sin rehacer nada.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Protocol, runtime_checkable

from jarvis.config.settings import Settings

__all__ = [
    "EdgeSpeaker",
    "Speaker",
    "SpeechError",
    "clean_for_speech",
    "shorten_for_speech",
]

_logger = logging.getLogger(__name__)

#: Voces recomendadas en español. Se documentan aquí para no tener que
#: buscarlas: el catálogo completo son cientos de voces con nombres opacos.
SUGGESTED_VOICES = {
    "es-ES-AlvaroNeural": "España, masculina, tono sereno",
    "es-ES-ElviraNeural": "España, femenina",
    "es-MX-JorgeNeural": "México, masculina",
    "es-CL-LorenzoNeural": "Chile, masculina",
    "es-CL-CatalinaNeural": "Chile, femenina",
}


class SpeechError(RuntimeError):
    """No se pudo sintetizar o reproducir el habla."""


@runtime_checkable
class Speaker(Protocol):
    """Interfaz de todo motor de voz."""

    def speak(self, text: str) -> bool:
        """Pronuncia un texto y espera a que termine.

        Args:
            text: Texto a pronunciar.

        Returns:
            ``True`` si llegó a reproducirse algo.
        """
        ...

    def stop(self) -> None:
        """Interrumpe la reproducción en curso."""
        ...


# --------------------------------------------------------------------------- #
# Preparación del texto
# --------------------------------------------------------------------------- #

#: Elementos de formato que el modelo puede emitir y que no deben pronunciarse.
_MARKDOWN = re.compile(r"[*_`#>]+")
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_LIST_BULLET = re.compile(r"^\s*[-•]\s+", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Prepara un texto para ser pronunciado.

    El modelo puede devolver asteriscos, viñetas, enlaces o bloques de código.
    Leídos en voz alta resultan absurdos —«asterisco asterisco listo asterisco
    asterisco»—, de modo que se retiran antes de sintetizar.

    Args:
        text: Texto tal como lo produjo el modelo.

    Returns:
        El texto listo para pronunciar, vacío si no quedaba nada legible.
    """
    limpio = _CODE_BLOCK.sub(" ", text)
    limpio = _LINK.sub(r"\1", limpio)
    limpio = _URL.sub("un enlace", limpio)
    limpio = _LIST_BULLET.sub("", limpio)
    limpio = _MARKDOWN.sub("", limpio)
    return _WHITESPACE.sub(" ", limpio).strip()


#: Final de frase, para poder cortar por donde el oído lo espera.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s")

#: Coletilla que se añade al recortar. Sin ella, el usuario creería que el
#: asistente terminó de responder cuando en realidad quedaba texto.
TRUNCATION_NOTICE = "Te lo dejo completo en pantalla."


def shorten_for_speech(text: str, max_chars: int) -> str:
    """Acorta un texto largo para pronunciarlo sin agotar la paciencia.

    Una respuesta de tres párrafos con direcciones web es insufrible dicha en
    voz alta, y además redundante: la pantalla ya la muestra entera. Se
    pronuncian las primeras frases completas y se avisa de que hay más.

    El corte se hace en un final de frase, nunca a mitad de palabra: una voz
    que se interrumpe de golpe suena a fallo, no a resumen.

    Args:
        text: Texto ya preparado para pronunciar.
        max_chars: Longitud máxima. Cero o menos desactiva el recorte.

    Returns:
        El texto a pronunciar.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    frases = _SENTENCE_END.split(text)
    acumulado: list[str] = []
    total = 0

    for frase in frases:
        if acumulado and total + len(frase) > max_chars:
            break
        acumulado.append(frase)
        total += len(frase) + 1

    dicho = " ".join(acumulado).strip()

    # Si la primera frase ya excede el límite, se corta por palabras: es
    # preferible una frase incompleta a un monólogo.
    if not acumulado or len(dicho) > max_chars * 1.5:
        dicho = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"

    return f"{dicho} {TRUNCATION_NOTICE}"


# --------------------------------------------------------------------------- #
# Reproducción
# --------------------------------------------------------------------------- #


class _Player:
    """Reproductor de audio comprimido en memoria.

    Decodifica el MP3 que devuelve el servicio y lo entrega al dispositivo de
    salida. Se mantiene aparte del sintetizador para poder detener la
    reproducción con independencia de cómo se generó el audio.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()

    def stop(self) -> None:
        """Solicita la interrupción de la reproducción en curso."""
        self._stop.set()

    def play_mp3(self, data: bytes) -> bool:
        """Decodifica y reproduce audio MP3.

        Args:
            data: Contenido del archivo MP3.

        Returns:
            ``True`` si se reprodujo hasta el final.

        Raises:
            SpeechError: Si faltan las dependencias de audio.
        """
        self._stop.clear()

        try:
            import miniaudio
            import sounddevice as sd
        except ImportError as exc:
            raise SpeechError(
                "Faltan las dependencias de audio para hablar. Instala el "
                'proyecto con «pip install -e ".[dev]"».'
            ) from exc

        try:
            decodificado = miniaudio.decode(data)
        except Exception as exc:  # noqa: BLE001 - frontera con la biblioteca
            raise SpeechError(f"No se pudo decodificar el audio: {exc}") from exc

        import numpy as np

        muestras = np.asarray(decodificado.samples, dtype=np.int16)
        if decodificado.nchannels > 1:
            muestras = muestras.reshape(-1, decodificado.nchannels)

        try:
            sd.play(muestras, decodificado.sample_rate)
            # Se espera en tramos cortos para poder atender una interrupción
            # en lugar de quedar bloqueado hasta el final del audio.
            while sd.get_stream().active:
                if self._stop.is_set():
                    sd.stop()
                    return False
                sd.sleep(50)
        except Exception as exc:  # noqa: BLE001 - depende del sistema de audio
            raise SpeechError(f"No se pudo reproducir el audio: {exc}") from exc

        return True


# --------------------------------------------------------------------------- #
# Sintetizador
# --------------------------------------------------------------------------- #


class EdgeSpeaker:
    """Motor de voz basado en el servicio de voces neuronales de Edge."""

    def __init__(self, settings: Settings, player: Any | None = None) -> None:
        """Prepara el sintetizador.

        Args:
            settings: Configuración de la aplicación.
            player: Reproductor alternativo. Se admite para las pruebas.
        """
        self._voice = settings.tts_voice
        self._rate = settings.tts_rate
        self._enabled = settings.tts_enabled
        self._max_chars = settings.tts_max_chars
        self._player = player if player is not None else _Player()

    @property
    def enabled(self) -> bool:
        """Indica si el asistente tiene la voz activada."""
        return self._enabled

    def stop(self) -> None:
        """Interrumpe la reproducción en curso."""
        self._player.stop()

    def synthesise(self, text: str) -> bytes:
        """Convierte texto en audio sin reproducirlo.

        Args:
            text: Texto ya preparado para pronunciar.

        Returns:
            El audio en formato MP3.

        Raises:
            SpeechError: Si falta la dependencia o el servicio falla.
        """
        try:
            import edge_tts
        except ImportError as exc:
            raise SpeechError(
                "Falta el paquete «edge-tts». Instala el proyecto con "
                '«pip install -e ".[dev]"», o desactiva la voz con '
                "JARVIS_TTS_ENABLED=false."
            ) from exc

        async def _recoger() -> bytes:
            comunicacion = edge_tts.Communicate(text, self._voice, rate=self._rate)
            trozos = [
                trozo["data"]
                async for trozo in comunicacion.stream()
                if trozo["type"] == "audio"
            ]
            return b"".join(trozos)

        try:
            return asyncio.run(_recoger())
        except Exception as exc:  # noqa: BLE001 - red, voz inexistente, etc.
            raise SpeechError(
                f"No se pudo sintetizar el habla con la voz «{self._voice}»: "
                f"{exc}"
            ) from exc

    def speak(self, text: str) -> bool:
        """Pronuncia un texto y espera a que termine.

        Un fallo al hablar no interrumpe la sesión: el asistente ya ejecutó lo
        que se le pidió y la respuesta sigue estando en pantalla. Se registra
        el problema y se continúa.

        Args:
            text: Texto tal como lo produjo el modelo.

        Returns:
            ``True`` si llegó a reproducirse algo.
        """
        if not self._enabled:
            return False

        preparado = shorten_for_speech(clean_for_speech(text), self._max_chars)
        if not preparado:
            return False

        try:
            audio = self.synthesise(preparado)
            if not audio:
                return False
            return self._player.play_mp3(audio)
        except SpeechError as exc:
            _logger.warning("No se pudo hablar: %s", exc)
            return False
