"""Comprobación del subsistema de voz.

Verifica por separado cada eslabón de la cadena —micrófono, calibración,
detección de la intervención, transcripción y palabra de activación— sin
implicar al modelo de lenguaje. Así, cuando la voz falle, se sabrá si el
problema está en el audio o en el asistente.

Uso, desde la raíz del proyecto y con el entorno activo::

    python scripts/probar_voz.py

    python scripts/probar_voz.py --dispositivos   # solo enumera micrófonos
    python scripts/probar_voz.py --activacion     # prueba «hey JARVIS»
    python scripts/probar_voz.py --activacion --modelo alexa
"""

from __future__ import annotations

import sys
import traceback


def _titulo(texto: str) -> None:
    print(f"\n{'=' * 70}\n{texto}\n{'=' * 70}")


def _dispositivos() -> int:
    """Enumera los micrófonos disponibles."""
    from jarvis.voice.recorder import list_input_devices

    _titulo("MICRÓFONOS DISPONIBLES")
    dispositivos = list_input_devices()

    if not dispositivos:
        print("  No se encontró ningún dispositivo de entrada.")
        print("  Comprueba que haya un micrófono conectado y que Windows")
        print("  permita el acceso: Configuración → Privacidad → Micrófono.")
        return 1

    for indice, nombre, canales in dispositivos:
        print(f"  [{indice:>2}] {nombre}  ({canales} canal(es))")

    print("\n  Se usa el predeterminado del sistema salvo que definas")
    print("  JARVIS_MIC_DEVICE con uno de estos índices.")
    return 0


def _barra(valor: float, ancho: int = 20, maximo: float = 1.0) -> str:
    """Dibuja una barra de progreso con caracteres de bloque."""
    llenos = int(min(1.0, valor / maximo) * ancho)
    return "█" * llenos + "░" * (ancho - llenos)


def _activacion(settings: object, modelo: str) -> int:
    """Comprueba la detección de la palabra de activación.

    Muestra en vivo el nivel de entrada y la confianza del modelo. La
    combinación de ambos distingue las dos causas posibles de que no active:
    si el nivel no se mueve al hablar, el problema es el micrófono; si el
    nivel sube pero la confianza no, es el reconocimiento.
    """
    from jarvis.voice.audio import rms
    from jarvis.voice.recorder import Microphone
    from jarvis.voice.wake_word import WakeWordDetector

    _titulo("PALABRA DE ACTIVACIÓN")
    print("  Cargando el modelo (la primera vez se descarga)…")

    umbral = settings.wake_word_threshold  # type: ignore[attr-defined]
    detector = WakeWordDetector(wake_word=modelo, threshold=umbral)

    print(f"  Modelo: «{modelo}»")
    print(f"  Umbral: {umbral}")

    microfono = Microphone(device=settings.mic_device)  # type: ignore[attr-defined]

    frase = "hey JARVIS" if "jarvis" in modelo else modelo.replace("_", " ")
    print(f'\n  Di «{frase}» varias veces, pronunciándolo a la inglesa.')
    print("  Observa las barras: el nivel debe subir al hablar.")
    print("  Ctrl+C para terminar.\n")

    activaciones = 0
    mejor_confianza = 0.0
    mejor_nivel = 0.0

    try:
        with microfono.open_stream() as flujo:
            while True:
                bloque = microfono.read(flujo)
                nivel = rms(bloque)
                mejor_nivel = max(mejor_nivel, nivel)

                if detector.push(bloque):
                    activaciones += 1
                    print(
                        f"\r  ✓ ACTIVADO  (confianza "
                        f"{detector.last_score:.2f})            "
                    )

                mejor_confianza = max(mejor_confianza, detector.last_score)

                sys.stdout.write(
                    f"\r  nivel {_barra(nivel, maximo=0.2)} {nivel:.3f}   "
                    f"activación {_barra(detector.last_score)} "
                    f"{detector.last_score:.2f}  "
                )
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n")

    _titulo("RESULTADO")
    print(f"  Activaciones          : {activaciones}")
    print(f"  Confianza máxima      : {mejor_confianza:.3f}  (umbral {umbral})")
    print(f"  Nivel de audio máximo : {mejor_nivel:.3f}")

    if activaciones > 0:
        print("\n  La palabra de activación funciona.")
        return 0

    print("\n  No hubo activaciones. El diagnóstico depende de las cifras:")

    if mejor_nivel < 0.01:
        print("\n  >>> El NIVEL DE AUDIO apenas se movió.")
        print("      El micrófono predeterminado no está captando tu voz.")
        print("      Prueba otro de la lista definiendo JARVIS_MIC_DEVICE")
        print("      en el archivo .env, y vuelve a ejecutar.")
    elif mejor_confianza >= umbral * 0.5:
        print("\n  >>> La CONFIANZA se acercó al umbral: es cuestión de ajuste.")
        print(f"      Baja JARVIS_WAKE_WORD_THRESHOLD a {mejor_confianza * 0.8:.2f}")
        print("      en el archivo .env y vuelve a probar.")
    else:
        print("\n  >>> El audio llega bien pero el modelo no reconoce la frase.")
        print("      openWakeWord solo está entrenado en inglés, y «hey JARVIS»")
        print("      pronunciado a la española suena muy distinto.")
        print("\n      Comprueba si es eso con un modelo mejor entrenado:")
        print("        python scripts/probar_voz.py --activacion --modelo alexa")
        print("\n      Si «alexa» sí activa, el problema es la pronunciación y")
        print("      no la instalación. En ese caso tienes el atajo de teclado")
        print(f"      ({settings.hotkey}) como alternativa.")  # type: ignore[attr-defined]

    return 1


def _modelo_elegido(settings: object) -> str:
    """Devuelve el modelo de activación indicado en la línea de órdenes.

    Poder cambiarlo permite aislar la causa de un fallo: si un modelo distinto
    sí responde, el problema es la pronunciación y no la instalación.
    """
    if "--modelo" in sys.argv:
        posicion = sys.argv.index("--modelo") + 1
        if posicion < len(sys.argv):
            return sys.argv[posicion]
    return settings.wake_word  # type: ignore[attr-defined]


def main() -> int:  # noqa: C901 - un diagnóstico lineal se lee mejor entero
    from jarvis.config.settings import load_settings

    settings = load_settings()

    if "--dispositivos" in sys.argv:
        return _dispositivos()

    if _dispositivos() != 0:
        return 1

    if "--activacion" in sys.argv:
        return _activacion(settings, _modelo_elegido(settings))

    from jarvis.voice.recorder import Microphone
    from jarvis.voice.vad import UtteranceDetector

    microfono = Microphone(device=settings.mic_device)
    detector = UtteranceDetector()

    # -- Calibración -------------------------------------------------------- #

    _titulo("CALIBRACIÓN DEL RUIDO DE FONDO")
    print("  Guarda silencio durante un segundo…")

    umbral = microfono.calibrate(detector)
    print(f"  Umbral de energía: {umbral:.5f}")

    if umbral <= detector.floor_threshold:
        print("  El entorno es muy silencioso; se aplicó el umbral mínimo.")
    elif umbral > 0.05:
        print("  AVISO: el ruido de fondo es alto. Puede costar detectar el habla.")

    # -- Captura ------------------------------------------------------------ #

    _titulo("CAPTURA DE UNA INTERVENCIÓN")
    print("  Habla ahora. Di algo como «abre el bloc de notas».")
    print("  La grabación termina sola cuando te calles.\n")

    clip = microfono.record_utterance(detector)

    if clip.is_empty:
        print("  No se detectó ninguna intervención.")
        print("  Comprueba que el micrófono elegido sea el correcto y que")
        print("  Windows no lo tenga silenciado.")
        return 1

    print(f"  Capturados {clip.duration_seconds:.1f} segundos.")

    # -- Transcripción ------------------------------------------------------ #

    _titulo("TRANSCRIPCIÓN")
    print(f"  Motor: {settings.stt_backend}")
    print(f"  Modelo: {settings.resolved_stt_model()}")

    if str(settings.stt_backend) == "remote":
        print(f"  Servicio: {settings.resolved_stt_base_url()}")
    print("  Transcribiendo…")

    from jarvis.voice.factory import create_transcriber

    transcriptor = create_transcriber(settings)
    resultado = transcriptor.transcribe(clip)

    if resultado.is_empty:
        print("\n  No se reconoció nada aprovechable.")
        print("  Suele indicar que se grabó silencio o ruido, no voz.")
        return 1

    print(f'\n  Transcripción: "{resultado.text}"')
    print(f"  Idioma detectado: {resultado.language}")

    _titulo("CADENA DE VOZ VERIFICADA")
    print("  Micrófono, calibración, detección y transcripción funcionan.")
    print("\n  Siguiente paso:")
    print("    python scripts/probar_voz.py --activacion   # prueba «hey JARVIS»")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        raise SystemExit(130) from None
    except Exception:  # noqa: BLE001 - el diagnóstico nunca debe fallar en seco
        traceback.print_exc()
        raise SystemExit(1) from None
