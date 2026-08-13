"""Pruebas de la detección de intervenciones habladas.

El audio se genera de forma sintética, de modo que las pruebas no dependen de
un micrófono ni de grabaciones almacenadas, y cada caso —ruido, habla, pausa,
golpe— puede reproducirse con exactitud.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.voice.audio import SAMPLE_RATE, AudioClip, rms
from jarvis.voice.vad import UtteranceDetector, UtteranceState

FRAME_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)


def _ruido(nivel: float = 0.001, frames: int = 1) -> np.ndarray:
    """Genera ruido de fondo constante y reproducible."""
    generador = np.random.default_rng(seed=1234)
    muestras = generador.normal(0, nivel * 32768, FRAME_SIZE * frames)
    return muestras.astype(np.int16)


def _habla(nivel: float = 0.15, frames: int = 1) -> np.ndarray:
    """Genera un tono que simula el habla, con energía muy superior al ruido."""
    total = FRAME_SIZE * frames
    t = np.arange(total) / SAMPLE_RATE
    onda = np.sin(2 * np.pi * 180 * t) * nivel * 32768
    return onda.astype(np.int16)


def _trocear(muestras: np.ndarray) -> list[np.ndarray]:
    """Parte una señal en bloques del tamaño que espera el detector."""
    return [
        muestras[i : i + FRAME_SIZE]
        for i in range(0, len(muestras) - FRAME_SIZE + 1, FRAME_SIZE)
    ]


@pytest.fixture
def detector() -> UtteranceDetector:
    det = UtteranceDetector(frame_ms=FRAME_MS)
    det.calibrate(AudioClip(_ruido(frames=20)))
    return det


# --------------------------------------------------------------------------- #
# Calibración
# --------------------------------------------------------------------------- #


def test_the_threshold_adapts_to_the_ambient_noise() -> None:
    """Una sala silenciosa y otra con un ventilador exigen umbrales distintos."""
    silenciosa = UtteranceDetector()
    ruidosa = UtteranceDetector()

    silenciosa.calibrate(AudioClip(_ruido(nivel=0.001, frames=20)))
    ruidosa.calibrate(AudioClip(_ruido(nivel=0.02, frames=20)))

    assert ruidosa.threshold > silenciosa.threshold * 3


def test_a_muted_microphone_does_not_lower_the_threshold_to_zero() -> None:
    """Sin suelo mínimo, cualquier soplo pasaría por voz."""
    detector = UtteranceDetector()
    detector.calibrate(AudioClip(np.zeros(FRAME_SIZE * 20, dtype=np.int16)))

    assert detector.threshold >= detector.floor_threshold
    assert detector.push(_ruido(nivel=0.0005)) is UtteranceState.WAITING


# --------------------------------------------------------------------------- #
# Curso normal de una intervención
# --------------------------------------------------------------------------- #


def test_silence_alone_keeps_the_detector_waiting(
    detector: UtteranceDetector,
) -> None:
    for frame in _trocear(_ruido(frames=10)):
        assert detector.push(frame) is UtteranceState.WAITING


def test_speech_is_detected(detector: UtteranceDetector) -> None:
    estado = UtteranceState.WAITING
    for frame in _trocear(_habla(frames=10)):
        estado = detector.push(frame)
    assert estado is UtteranceState.SPEAKING


def test_a_pause_after_speech_finishes_the_utterance(
    detector: UtteranceDetector,
) -> None:
    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)

    estado = UtteranceState.SPEAKING
    for frame in _trocear(_ruido(frames=40)):
        estado = detector.push(frame)
        if estado is UtteranceState.FINISHED:
            break

    assert estado is UtteranceState.FINISHED


def test_a_short_pause_between_sentences_does_not_cut_the_user(
    detector: UtteranceDetector,
) -> None:
    """Respirar entre dos frases no debe interpretarse como haber terminado."""
    for frame in _trocear(_habla(frames=15)):
        detector.push(frame)

    # Una pausa de 300 ms, muy por debajo del umbral de 700 ms.
    for frame in _trocear(_ruido(frames=10)):
        assert detector.push(frame) is not UtteranceState.FINISHED

    for frame in _trocear(_habla(frames=15)):
        assert detector.push(frame) is UtteranceState.SPEAKING


def test_the_captured_clip_contains_the_speech(detector: UtteranceDetector) -> None:
    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)
    for frame in _trocear(_ruido(frames=40)):
        if detector.push(frame) is UtteranceState.FINISHED:
            break

    clip = detector.clip
    assert clip.duration_seconds > 0.5
    assert rms(clip.samples) > detector.threshold


def test_trailing_silence_is_trimmed(detector: UtteranceDetector) -> None:
    """Enviar el silencio final al servicio es pagar por esperar."""
    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)
    for frame in _trocear(_ruido(frames=40)):
        if detector.push(frame) is UtteranceState.FINISHED:
            break

    completo = 60 * FRAME_MS / 1000
    assert detector.clip.duration_seconds < completo * 0.8


# --------------------------------------------------------------------------- #
# Ruidos que no son órdenes
# --------------------------------------------------------------------------- #


def test_a_brief_noise_is_discarded(detector: UtteranceDetector) -> None:
    """Un golpe en la mesa no debe transcribirse como si fuera una orden."""
    for frame in _trocear(_habla(frames=3)):  # 90 ms, bajo el mínimo de 250
        detector.push(frame)

    estado = UtteranceState.SPEAKING
    for frame in _trocear(_ruido(frames=40)):
        estado = detector.push(frame)

    assert estado is not UtteranceState.FINISHED


def test_a_noise_does_not_prevent_the_real_utterance(
    detector: UtteranceDetector,
) -> None:
    """Tras descartar el golpe, el detector debe seguir atento."""
    for frame in _trocear(_habla(frames=3)):
        detector.push(frame)
    for frame in _trocear(_ruido(frames=30)):
        detector.push(frame)

    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)

    estado = UtteranceState.SPEAKING
    for frame in _trocear(_ruido(frames=40)):
        estado = detector.push(frame)
        if estado is UtteranceState.FINISHED:
            break

    assert estado is UtteranceState.FINISHED
    assert detector.clip.duration_seconds > 0.4


# --------------------------------------------------------------------------- #
# Límites
# --------------------------------------------------------------------------- #


def test_waiting_expires_if_nobody_speaks() -> None:
    detector = UtteranceDetector(frame_ms=FRAME_MS, start_timeout_ms=300)
    detector.calibrate(AudioClip(_ruido(frames=20)))

    estado = UtteranceState.WAITING
    for frame in _trocear(_ruido(frames=30)):
        estado = detector.push(frame)

    assert estado is UtteranceState.TIMED_OUT


def test_an_endless_utterance_is_cut_off() -> None:
    """Un micrófono en un ambiente ruidoso no debe grabar indefinidamente."""
    detector = UtteranceDetector(frame_ms=FRAME_MS, max_utterance_ms=600)
    detector.calibrate(AudioClip(_ruido(frames=20)))

    estado = UtteranceState.WAITING
    for frame in _trocear(_habla(frames=60)):
        estado = detector.push(frame)
        if estado is UtteranceState.FINISHED:
            break

    assert estado is UtteranceState.FINISHED


def test_frames_after_the_end_are_ignored(detector: UtteranceDetector) -> None:
    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)
    for frame in _trocear(_ruido(frames=40)):
        if detector.push(frame) is UtteranceState.FINISHED:
            break

    duracion = detector.clip.duration_seconds
    for frame in _trocear(_habla(frames=10)):
        assert detector.push(frame) is UtteranceState.FINISHED
    assert detector.clip.duration_seconds == duracion


def test_reset_prepares_a_new_utterance_but_keeps_the_threshold(
    detector: UtteranceDetector,
) -> None:
    """Recalibrar en cada turno malgastaría medio segundo cada vez."""
    umbral = detector.threshold
    for frame in _trocear(_habla(frames=20)):
        detector.push(frame)

    detector.reset()

    assert detector.state is UtteranceState.WAITING
    assert detector.threshold == umbral
    assert detector.clip.is_empty


# --------------------------------------------------------------------------- #
# Codificación
# --------------------------------------------------------------------------- #


def test_the_clip_encodes_as_a_valid_wav() -> None:
    import io
    import wave

    clip = AudioClip(_habla(frames=30))
    with wave.open(io.BytesIO(clip.to_wav_bytes()), "rb") as archivo:
        assert archivo.getnchannels() == 1
        assert archivo.getsampwidth() == 2
        assert archivo.getframerate() == SAMPLE_RATE
        assert archivo.getnframes() == clip.samples.size


def test_an_empty_clip_reports_zero_duration() -> None:
    vacio = AudioClip(np.zeros(0, dtype=np.int16))
    assert vacio.is_empty
    assert vacio.duration_seconds == 0.0


def test_frames_are_joined_in_order() -> None:
    a, b = _habla(frames=2), _ruido(frames=3)
    unido = AudioClip.from_frames([a, b])
    assert unido.samples.size == a.size + b.size
    assert np.array_equal(unido.samples[: a.size], a)
