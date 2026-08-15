"""Pruebas de la voz de salida y del bucle de voz.

El sintetizador y el reproductor se sustituyen por postizos, de modo que se
comprueba la preparación del texto y el encadenado completo —transcribir,
pensar, responder— sin red, sin micrófono y sin altavoces.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from jarvis.ai.provider import LLMResponse, TextBlock, ToolUse
from jarvis.config.settings import Provider, Settings
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.registry import ToolRegistry, tool
from jarvis.security.audit import AuditLog
from jarvis.security.guard import Guard
from jarvis.security.risk import Risk
from jarvis.ui.voice_loop import VoiceSession
from jarvis.voice.audio import SAMPLE_RATE, AudioClip
from jarvis.voice.tts import (
    TRUNCATION_NOTICE,
    EdgeSpeaker,
    clean_for_speech,
    shorten_for_speech,
)
from jarvis.voice.transcriber import Transcription, TranscriptionError
from jarvis.voice.vad import UtteranceDetector


# --------------------------------------------------------------------------- #
# Preparación del texto que se pronuncia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("**Listo.**", "Listo."),
        ("Abriendo `Chrome`.", "Abriendo Chrome."),
        ("# Resumen\nTodo bien.", "Resumen Todo bien."),
        ("- Uno\n- Dos", "Uno Dos"),
        ("Mira [la documentación](https://ejemplo.com).", "Mira la documentación."),
        ("Ve a https://ejemplo.com ahora.", "Ve a un enlace ahora."),
        ("Texto   con     espacios", "Texto con espacios"),
    ],
)
def test_formatting_is_removed_before_speaking(crudo: str, esperado: str) -> None:
    """«asterisco asterisco listo» no es una respuesta aceptable."""
    assert clean_for_speech(crudo) == esperado


def test_code_blocks_are_not_read_aloud() -> None:
    texto = "Este es el código:\n```python\nprint('hola')\n```\nYa está."
    limpio = clean_for_speech(texto)
    assert "print" not in limpio
    assert "Ya está." in limpio


def test_text_without_formatting_is_untouched() -> None:
    assert clean_for_speech("Volumen ajustado al 70%.") == "Volumen ajustado al 70%."


def test_an_empty_text_stays_empty() -> None:
    assert clean_for_speech("   \n  ") == ""
    assert clean_for_speech("```solo código```") == ""


# --------------------------------------------------------------------------- #
# Recorte de las respuestas largas
# --------------------------------------------------------------------------- #


def test_a_short_answer_is_spoken_whole() -> None:
    assert shorten_for_speech("Volumen al 70%.", 350) == "Volumen al 70%."


def test_a_long_answer_is_cut_at_a_sentence() -> None:
    """Una voz que se interrumpe a mitad de palabra suena a fallo."""
    texto = (
        "La RTX 5070 cuesta unos 600 euros. "
        "El precio varía según la tienda. "
        "Hay unidades disponibles en tres comercios. "
        "También existe una versión Ti algo más cara."
    )
    dicho = shorten_for_speech(texto, 80)

    assert dicho.startswith("La RTX 5070 cuesta unos 600 euros.")
    assert dicho.endswith(TRUNCATION_NOTICE)
    assert "versión Ti" not in dicho


def test_the_listener_is_told_there_is_more() -> None:
    """Sin aviso, el usuario creería que el asistente ya terminó."""
    largo = "Frase completa. " * 40
    assert TRUNCATION_NOTICE in shorten_for_speech(largo, 100)


def test_a_single_endless_sentence_is_cut_by_words() -> None:
    texto = "palabra " * 200
    dicho = shorten_for_speech(texto, 60)
    assert len(dicho) < 120
    assert "palabr…" not in dicho  # nunca a mitad de palabra


def test_truncation_can_be_disabled() -> None:
    largo = "Frase. " * 100
    assert shorten_for_speech(largo, 0) == largo


# --------------------------------------------------------------------------- #
# Sintetizador
# --------------------------------------------------------------------------- #


class _ReproductorPostizo:
    def __init__(self) -> None:
        self.reproducidos: list[bytes] = []
        self.detenciones = 0

    def play_mp3(self, data: bytes) -> bool:
        self.reproducidos.append(data)
        return True

    def stop(self) -> None:
        self.detenciones += 1


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "provider": Provider.OPENAI,
        "base_url": "groq",
        "model": "modelo",
        "api_key": "clave",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _hablante(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> tuple:
    reproductor = _ReproductorPostizo()
    hablante = EdgeSpeaker(_settings(**overrides), player=reproductor)
    monkeypatch.setattr(hablante, "synthesise", lambda texto: b"audio-mp3")
    return hablante, reproductor


def test_a_long_response_is_shortened_before_speaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La pantalla ya muestra el texto entero; el oído no lo necesita."""
    dicho: list[str] = []
    reproductor = _ReproductorPostizo()
    hablante = EdgeSpeaker(_settings(tts_max_chars=60), player=reproductor)
    monkeypatch.setattr(
        hablante, "synthesise", lambda t: (dicho.append(t), b"mp3")[1]
    )

    hablante.speak("Primera frase corta. " + "Relleno larguísimo. " * 20)

    assert len(dicho[0]) < 200
    assert TRUNCATION_NOTICE in dicho[0]


def test_a_response_is_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    hablante, reproductor = _hablante(monkeypatch)
    assert hablante.speak("**Listo.**") is True
    assert reproductor.reproducidos == [b"audio-mp3"]


def test_speech_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    hablante, reproductor = _hablante(monkeypatch, tts_enabled=False)
    assert hablante.speak("Listo.") is False
    assert reproductor.reproducidos == []


def test_an_empty_response_is_not_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    hablante, reproductor = _hablante(monkeypatch)
    assert hablante.speak("```solo código```") is False
    assert reproductor.reproducidos == []


def test_a_synthesis_failure_does_not_break_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La acción ya se ejecutó: no poder hablarla no debe tumbar nada."""
    from jarvis.voice.tts import SpeechError

    reproductor = _ReproductorPostizo()
    hablante = EdgeSpeaker(_settings(), player=reproductor)

    def falla(texto: str) -> bytes:
        raise SpeechError("el servicio no responde")

    monkeypatch.setattr(hablante, "synthesise", falla)
    assert hablante.speak("Listo.") is False


# --------------------------------------------------------------------------- #
# Bucle de voz
# --------------------------------------------------------------------------- #


class _TranscriptorPostizo:
    def __init__(self, texto: str = "", falla: bool = False) -> None:
        self.texto = texto
        self.falla = falla
        self.recibidos: list[AudioClip] = []

    def transcribe(self, clip: AudioClip) -> Transcription:
        self.recibidos.append(clip)
        if self.falla:
            raise TranscriptionError("el servicio no responde")
        return Transcription(text=self.texto, language="es")


class _HablantePostizo:
    def __init__(self) -> None:
        self.dicho: list[str] = []
        self.detenciones = 0

    def speak(self, text: str) -> bool:
        self.dicho.append(text)
        return True

    def stop(self) -> None:
        self.detenciones += 1


class _ProveedorPostizo:
    def __init__(self, respuestas: list[LLMResponse]) -> None:
        self.respuestas = list(respuestas)

    def chat(self, system: str, messages: Any, tools: Any) -> LLMResponse:
        if not self.respuestas:
            return LLMResponse(blocks=(TextBlock("Listo."),))
        return self.respuestas.pop(0)


@pytest.fixture
def registro() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(risk=Risk.SAFE, category="system", registry=reg)
    def subir_volumen(nivel: int) -> str:
        """Sube el volumen.

        Args:
            nivel: Nivel deseado.
        """
        return f"Volumen al {nivel}%."

    return reg


def _sesion(
    registro: ToolRegistry,
    tmp_path: Any,
    transcriptor: _TranscriptorPostizo,
    hablante: _HablantePostizo,
    respuestas: list[LLMResponse],
) -> VoiceSession:
    return VoiceSession(
        orchestrator=Orchestrator(
            provider=_ProveedorPostizo(respuestas),
            registry=registro,
            guard=Guard.with_default_policies(),
            audit=AuditLog(path=tmp_path / "audit.jsonl"),
        ),
        microphone=None,  # type: ignore[arg-type]  # no se usa en estas pruebas
        transcriber=transcriptor,
        speaker=hablante,
        utterance=UtteranceDetector(),
    )


def _clip(segundos: float = 1.0) -> AudioClip:
    return AudioClip(np.zeros(int(SAMPLE_RATE * segundos), dtype=np.int16))


def test_a_spoken_order_runs_a_tool_and_is_answered_aloud(
    registro: ToolRegistry, tmp_path: Any
) -> None:
    transcriptor = _TranscriptorPostizo("sube el volumen a 70")
    hablante = _HablantePostizo()
    sesion = _sesion(
        registro,
        tmp_path,
        transcriptor,
        hablante,
        [
            LLMResponse(blocks=(ToolUse("t1", "subir_volumen", {"nivel": 70}),)),
            LLMResponse(blocks=(TextBlock("Volumen al 70%."),)),
        ],
    )

    sesion._handle(_clip())

    assert hablante.dicho == ["Volumen al 70%."]


def test_an_empty_clip_is_ignored(registro: ToolRegistry, tmp_path: Any) -> None:
    transcriptor = _TranscriptorPostizo("algo")
    hablante = _HablantePostizo()
    sesion = _sesion(registro, tmp_path, transcriptor, hablante, [])

    sesion._handle(AudioClip(np.zeros(0, dtype=np.int16)))

    assert transcriptor.recibidos == []
    assert hablante.dicho == []


def test_an_unintelligible_clip_does_not_reach_the_model(
    registro: ToolRegistry, tmp_path: Any
) -> None:
    """El silencio produce puntuación suelta; no debe consumir una petición."""
    transcriptor = _TranscriptorPostizo("  ...  ")
    hablante = _HablantePostizo()
    sesion = _sesion(registro, tmp_path, transcriptor, hablante, [])

    sesion._handle(_clip())

    assert hablante.dicho == []


def test_a_transcription_failure_does_not_break_the_session(
    registro: ToolRegistry, tmp_path: Any
) -> None:
    transcriptor = _TranscriptorPostizo(falla=True)
    hablante = _HablantePostizo()
    sesion = _sesion(registro, tmp_path, transcriptor, hablante, [])

    sesion._handle(_clip())  # no debe propagarse

    assert hablante.dicho == []


def test_the_detectors_are_cleared_between_turns(
    registro: ToolRegistry, tmp_path: Any
) -> None:
    """Los restos del turno anterior provocarían activaciones fantasma."""
    from jarvis.voice.wake_word import WakeWordDetector

    class ModeloMudo:
        def predict(self, chunk: Any) -> dict[str, float]:
            return {"x": 0.0}

    sesion = _sesion(
        registro, tmp_path, _TranscriptorPostizo("hola"), _HablantePostizo(), []
    )
    sesion.wake_word = WakeWordDetector(model=ModeloMudo())
    sesion.utterance.push(np.zeros(480, dtype=np.int16))

    sesion._reset_detectors()

    assert sesion.utterance.clip.is_empty
    assert not sesion.wake_word.in_cooldown
