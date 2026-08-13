"""Pruebas de la palabra de activación y del atajo de teclado.

El modelo de detección se sustituye por uno postizo con puntuaciones
predefinidas, de modo que se comprueba el reagrupamiento de muestras, el
umbral y el tiempo de espera sin descargar ningún modelo ni necesitar
micrófono.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.voice.hotkey import DEFAULT_HOTKEY, HotkeyError, PushToTalk, parse_key
from jarvis.voice.wake_word import CHUNK_SAMPLES, WakeWordDetector


class _ModeloPostizo:
    """Sustituto de openWakeWord que devuelve puntuaciones predefinidas."""

    def __init__(self, puntuaciones: list[float]) -> None:
        self.puntuaciones = list(puntuaciones)
        self.bloques: list[np.ndarray] = []

    def predict(self, chunk: np.ndarray) -> dict[str, float]:
        self.bloques.append(chunk)
        valor = self.puntuaciones.pop(0) if self.puntuaciones else 0.0
        return {"hey_jarvis": valor}


def _detector(puntuaciones: list[float], **kwargs: object) -> WakeWordDetector:
    return WakeWordDetector(model=_ModeloPostizo(puntuaciones), **kwargs)  # type: ignore[arg-type]


def _bloque(muestras: int) -> np.ndarray:
    return np.zeros(muestras, dtype=np.int16)


# --------------------------------------------------------------------------- #
# Reagrupamiento de muestras
# --------------------------------------------------------------------------- #


def test_frames_are_regrouped_to_the_expected_size() -> None:
    """El grabador entrega 30 ms y el modelo espera 80: hay que acumular."""
    modelo = _ModeloPostizo([0.0] * 10)
    detector = WakeWordDetector(model=modelo)

    for _ in range(10):
        detector.push(_bloque(480))  # 30 ms a 16 kHz

    assert modelo.bloques, "el modelo nunca llegó a evaluarse"
    assert all(b.size == CHUNK_SAMPLES for b in modelo.bloques)


def test_a_partial_frame_does_not_trigger_an_evaluation() -> None:
    modelo = _ModeloPostizo([0.9])
    detector = WakeWordDetector(model=modelo)

    assert detector.push(_bloque(480)) is False
    assert modelo.bloques == []


def test_a_large_frame_produces_several_evaluations() -> None:
    modelo = _ModeloPostizo([0.0, 0.0, 0.0])
    detector = WakeWordDetector(model=modelo)

    detector.push(_bloque(CHUNK_SAMPLES * 3))
    assert len(modelo.bloques) == 3


def test_leftover_samples_are_kept_for_the_next_frame() -> None:
    """Descartar el resto perdería fonemas justo en el límite del bloque."""
    modelo = _ModeloPostizo([0.0, 0.0])
    detector = WakeWordDetector(model=modelo)

    detector.push(_bloque(CHUNK_SAMPLES + 400))
    assert len(modelo.bloques) == 1

    detector.push(_bloque(CHUNK_SAMPLES - 400))
    assert len(modelo.bloques) == 2


# --------------------------------------------------------------------------- #
# Umbral
# --------------------------------------------------------------------------- #


def test_a_confident_score_activates() -> None:
    detector = _detector([0.95])
    assert detector.push(_bloque(CHUNK_SAMPLES)) is True
    assert detector.last_score == 0.95


def test_a_low_score_does_not_activate() -> None:
    detector = _detector([0.2])
    assert detector.push(_bloque(CHUNK_SAMPLES)) is False


def test_the_threshold_is_configurable() -> None:
    """Subirlo reduce los disparos accidentales; bajarlo, las repeticiones."""
    exigente = _detector([0.6], threshold=0.8)
    permisivo = _detector([0.6], threshold=0.4)

    assert exigente.push(_bloque(CHUNK_SAMPLES)) is False
    assert permisivo.push(_bloque(CHUNK_SAMPLES)) is True


# --------------------------------------------------------------------------- #
# Tiempo de espera entre activaciones
# --------------------------------------------------------------------------- #


def test_a_single_utterance_activates_only_once() -> None:
    """La palabra sigue en la ventana del modelo y dispararía varias veces."""
    detector = _detector([0.95, 0.95, 0.95, 0.95])

    activaciones = sum(detector.push(_bloque(CHUNK_SAMPLES)) for _ in range(4))
    assert activaciones == 1


def test_the_wait_expires_after_enough_audio() -> None:
    detector = _detector([0.95] + [0.0] * 30 + [0.95], cooldown_ms=200)

    assert detector.push(_bloque(CHUNK_SAMPLES)) is True
    assert detector.in_cooldown

    # 200 ms a 16 kHz son 3200 muestras: se superan con margen.
    for _ in range(30):
        detector.push(_bloque(CHUNK_SAMPLES))

    assert not detector.in_cooldown
    assert detector.push(_bloque(CHUNK_SAMPLES)) is True


def test_reset_clears_the_wait() -> None:
    detector = _detector([0.95, 0.95])
    detector.push(_bloque(CHUNK_SAMPLES))
    assert detector.in_cooldown

    detector.reset()

    assert not detector.in_cooldown
    assert detector.last_score == 0.0
    assert detector.push(_bloque(CHUNK_SAMPLES)) is True


# --------------------------------------------------------------------------- #
# Robustez
# --------------------------------------------------------------------------- #


def test_a_model_failure_does_not_break_the_loop() -> None:
    """El detector corre de continuo: un fallo puntual no debe tumbarlo."""

    class ModeloRoto:
        def predict(self, chunk: np.ndarray) -> dict[str, float]:
            raise RuntimeError("el modelo se atragantó")

    detector = WakeWordDetector(model=ModeloRoto())
    assert detector.push(_bloque(CHUNK_SAMPLES)) is False


def test_an_empty_prediction_is_handled() -> None:
    class ModeloMudo:
        def predict(self, chunk: np.ndarray) -> dict[str, float]:
            return {}

    detector = WakeWordDetector(model=ModeloMudo())
    assert detector.push(_bloque(CHUNK_SAMPLES)) is False
    assert detector.last_score == 0.0


# --------------------------------------------------------------------------- #
# Modelos propios
# --------------------------------------------------------------------------- #


def test_a_missing_custom_model_is_reported(tmp_path: object) -> None:
    """Una ruta equivocada debe decirlo, no fallar dentro de la biblioteca."""
    from jarvis.voice.wake_word import WakeWordError

    detector = WakeWordDetector.__new__(WakeWordDetector)
    detector.wake_word = str(tmp_path) + "/no-existe.onnx"  # type: ignore[misc]

    with pytest.raises(WakeWordError, match="No se encontró el modelo"):
        detector._load_model()


# --------------------------------------------------------------------------- #
# Atajo de teclado
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("escrito", "esperado"),
    [
        ("f9", ("special", "f9")),
        ("F9", ("special", "f9")),
        ("  ctrl  ", ("special", "ctrl")),
        ("Key.space", ("special", "space")),
        ("a", ("char", "a")),
        ("Ñ", ("char", "ñ")),
    ],
)
def test_key_names_are_interpreted(escrito: str, esperado: tuple[str, str]) -> None:
    assert parse_key(escrito) == esperado


def test_an_empty_key_is_rejected() -> None:
    with pytest.raises(HotkeyError, match="vacío"):
        parse_key("   ")


class _TeclaEspecial:
    def __init__(self, name: str) -> None:
        self.name = name


class _TeclaCaracter:
    def __init__(self, char: str) -> None:
        self.char = char


def test_a_special_key_is_recognised() -> None:
    atajo = PushToTalk(key="f9")
    assert atajo._matches(_TeclaEspecial("f9"))
    assert not atajo._matches(_TeclaEspecial("f10"))
    assert not atajo._matches(_TeclaCaracter("f"))


def test_a_character_key_is_recognised() -> None:
    atajo = PushToTalk(key="j")
    assert atajo._matches(_TeclaCaracter("j"))
    assert atajo._matches(_TeclaCaracter("J"))
    assert not atajo._matches(_TeclaCaracter("k"))


def test_a_key_without_a_character_is_ignored() -> None:
    """Las teclas modificadoras llegan con char a nulo."""
    atajo = PushToTalk(key="j")
    assert not atajo._matches(_TeclaCaracter(None))  # type: ignore[arg-type]


def test_the_default_key_is_usable() -> None:
    assert parse_key(DEFAULT_HOTKEY) == ("special", "f9")


def test_a_callback_failure_does_not_silence_the_hotkey() -> None:
    """Una excepción sin capturar dejaría el atajo muerto el resto de la sesión."""

    def falla() -> None:
        raise RuntimeError("algo salió mal")

    PushToTalk._invoke(falla)  # no debe propagarse
