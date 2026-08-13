# Entrenar «Jarvis» como palabra de activación

## El problema, medido

El modelo preentrenado `hey_jarvis` de openWakeWord no reconoce la
pronunciación española. Las cifras del diagnóstico son concluyentes:

| Modelo | Nivel de audio | Confianza máxima | Activaciones |
|---|---|---|---|
| `alexa` | 0,143 | **0,915** | 2 |
| `hey_jarvis` | 0,114 | 0,054 | 0 |

El audio llega igual de bien en ambos casos y el motor funciona: `alexa`
activa con total claridad. Lo que falla es el reconocimiento de esa frase
concreta.

Bajar el umbral no es una salida. Con 0,054 estaríamos tan cerca del ruido de
fondo que cualquier sonido activaría el asistente.

## Por qué no basta con entrenar sin más

openWakeWord genera sus ejemplos de entrenamiento con voz sintética, y **todos
sus modelos de síntesis son ingleses**. Entrenar la palabra «Jarvis» con el
procedimiento habitual produciría otro modelo de pronunciación inglesa: el
mismo problema con otro nombre.

## El rodeo: entrenar la grafía, no la palabra

El sistema de entrenamiento no sabe qué es «Jarvis». Recibe un texto, lo
convierte en voz sintética inglesa y aprende ese sonido.

Eso puede aprovecharse: **si se le da una grafía inglesa que suene como la
pronunciación española, el modelo aprende el sonido correcto.**

En español, «Jarvis» se pronuncia aproximadamente *yár-bis* o *dyár-bis*.
Escrito para que un sintetizador inglés lo pronuncie así:

```
yarbis
yarviss
jarviss
dyarvis
```

El cuaderno de entrenamiento admite varias grafías a la vez, de modo que el
modelo cubre las distintas formas en que puedes decirlo.

**Antes de entrenar, comprueba cómo lo dices tú.** Graba tu voz diciendo
«Jarvis» y escúchalo. La grafía que elijas debe reproducir ese sonido leída
por un angloparlante.

## Procedimiento

1. Abre el cuaderno de entrenamiento automático de openWakeWord en Google
   Colab (está enlazado desde el [repositorio oficial][repo], en la sección
   de entrenamiento). Requiere una cuenta de Google; el entorno con GPU es
   gratuito.

2. Indica como palabra objetivo las grafías fonéticas, no «jarvis».

3. Deja los demás parámetros como vienen. La generación de ejemplos y el
   entrenamiento tardan menos de una hora.

4. Descarga el archivo `.onnx` resultante.

5. Guárdalo en el proyecto:

   ```
   src\jarvis\assets\models\jarvis.onnx
   ```

6. Apunta a él en el archivo `.env`:

   ```
   JARVIS_WAKE_WORD=src/jarvis/assets/models/jarvis.onnx
   ```

7. Compruébalo:

   ```cmd
   python scripts\probar_voz.py --activacion
   ```

El código acepta indistintamente un nombre de modelo preentrenado o la ruta de
un archivo `.onnx` o `.tflite`, así que no hay que tocar nada más.

## Ajustar el umbral

Una palabra suelta produce más falsos positivos que una frase de dos, porque
hay más sonidos cotidianos que se le parecen. Conviene ser exigente:

```
JARVIS_WAKE_WORD_THRESHOLD=0.6
```

El diagnóstico muestra la confianza de cada intento. Ajusta con datos:

* Activa cuando no debe → sube el umbral.
* No activa al llamarlo → bájalo, pero solo si la confianza rondaba el valor
  actual. Si se queda en 0,05, el problema es el modelo y no el umbral.

## Mientras tanto

El atajo de teclado (`F9` mantenida pulsada) funciona desde ya y no depende de
ningún modelo. Es la alternativa mientras entrenas, y conviene conservarlo
después: hay situaciones —una sala compartida, una llamada en curso— en las
que tener el micrófono siempre abierto no es aceptable.

## Si el rodeo no funciona

**Picovoice Porcupine** tiene modelos entrenados en español y permite crear
palabras personalizadas desde su consola web. Sería fiable de inmediato.

La reserva es la licencia: no está claro que su capa gratuita permita
redistribuir la aplicación, que es el objetivo de la Fase 10. Para uso
personal no hay problema; antes de distribuir habría que revisar sus
condiciones o volver a un modelo propio.

[repo]: https://github.com/dscripka/openWakeWord
