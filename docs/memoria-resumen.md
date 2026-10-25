# Calidad del resumen de la memoria entre sesiones

`calidad_resumen.py` tiene 6 conversaciones **sintéticas**, todas inventadas:

- 2 técnicas: Kafka y Spring.
- 1 de agenda, con un turno privado.
- 1 de CI, con una dirección de correo.
- 1 técnica que también consulta la memoria.
- 1 que solo prueba la memoria.

Cada conversación pasa por el mismo camino que en JARVIS: registro del turno, contenido enviado al modelo, resumen, limpieza y términos. Después se comprueba:

- **Tema y términos técnicos:** aparecen los que fija la sesión.
- **Sin ruido meta:** sin «en una conversación anterior», «en esta sesión», «el usuario», «JARVIS», «resumen»…
- **Sin datos privados:** ni direcciones ni texto de turnos privados.
- **Gramática básica (heurística):** cada frase empieza en mayúscula y acaba en punto, sin palabras repetidas, sin otro alfabeto y sin inglés.
- **Sesión de pruebas:** la que solo prueba la memoria no se guarda.

Los datos de abajo son con Ollama `qwen2.5:3b`, el resumidor principal. Otro modelo local con `JARVIS_OLLAMA_MODELO` en el `.env` (vacío = `qwen2.5:3b`). Cambia también el de las respuestas locales, y `calidad_resumen.py` usa el mismo, así que conviene repetir esta evaluación antes de cambiarlo.

```powershell
venv\Scripts\python.exe calidad_resumen.py                    # ahora
venv\Scripts\python.exe calidad_resumen.py --prompt-anterior  # el prompt de antes (sin el resto de cambios)
venv\Scripts\python.exe calidad_resumen.py --claude           # con Claude Haiku (de pago)
venv\Scripts\python.exe calidad_resumen.py --guardados        # recuentos sobre los resúmenes guardados, sin su texto
```

## Antes y después

| | Antes | Después |
|---|---|---|
| Sesiones aprobadas (las 6 comprobaciones) | **0/6** en 3 ejecuciones | **4/6** en 5 de 5 ejecuciones (5/6 en otras pruebas) |
| Ruido meta | 5/5 resúmenes | 0 |
| Direcciones o texto privado | 0 | 0 |
| Gramática básica | bien | bien |
| Sesión que solo prueba la memoria | se guardaba | no se guarda |
| Lo que aún falla | — | «particiones» (Kafka) y «logprob» (CI): el modelo pequeño omite a veces un detalle en palabras corrientes |

Ejemplo de la sesión de Kafka:

- **Antes:** «En esta sesión, se discutieron problemas de rebalanceos en un grupo de consumidores de Kafka causados por intervalos de procesamiento de lotes que superaban `max.poll.interval.ms`. […] El usuario también fue recomendado a probar con 3 particiones…»
- **Después:** «Preguntaste por qué tu consumer group de Kafka hace rebalanceos cada pocos minutos. Decidiste […] subir max.poll.interval.ms a 10 minutos y […] max.poll.records a 100. Tienes pendiente probar con 3 particiones mañana y revisar el lag del consumer group.»

Ejemplo de la sesión de CI:

- **Antes:** inventaba una sigla, «el CI (Control de Ingreso)», y hablaba de «JARVIS» y de «el usuario».
- **Después:** «Preguntaste qué test fallaba en Whisper […]. Tienes pendiente confirmar el envío de este aviso con tu voz.», más una línea «Términos: CI, test_whisper_confianza.».

## Qué cambió

1. **Turnos sobre la propia memoria fuera del resumen.** Se excluyen las preguntas como «¿te acuerdas de…?», los turnos con la tool `recordar` y frases como «prueba de la memoria», «¿funciona tu memoria?» o «¿qué recuerdas de mí?». Una sesión que solo tiene esos turnos queda vacía y no se guarda. Antes, la respuesta de `recordar` («en una sesión anterior hablamos de…») entraba en el resumen. «La memoria RAM funciona mal» no es una prueba y sí se guarda.
2. **Prompt en segunda persona.** «Preguntaste», «decidiste», «tienes pendiente»: se lee bien cuando `recordar` lo devuelve otro día. Además prohíbe hablar de la sesión, del usuario o de JARVIS, inventar o explicar siglas. Se probaron 4 variantes. Las que imponían un formato rígido («Tema: …») o daban un ejemplo concreto lo empeoraban: el modelo de 3B copiaba el ejemplo de Kafka en la sesión de agenda.
3. **Limpieza determinista.** Quita una muletilla inicial («En esta sesión, …»), pone mayúscula inicial y cambia `<email>` por «una dirección».
4. **Términos técnicos garantizados.** Si el modelo se deja un identificador técnico, se añade una última línea «Términos: …». Cuenta como identificador lo que lleva punto, guion bajo, `@`, mayúsculas internas o va todo en mayúsculas, y los términos del vocabulario de Whisper. Nunca entran nombres de personas. `recordar` busca por esas palabras.

## Límites

- **Gramática:** es una heurística, no un corrector. «el anotación» no se detecta.
- **Lo que el modelo omite o interpreta:** no se puede garantizar con un modelo de 3B. A veces dice «decidiste» de algo que solo se propuso. Con Claude Haiku como resumidor sería más fiel, pero cada sesión costaría dinero.
- **Resúmenes ya guardados:** `--guardados` encuentra ruido meta en 2 de los 3 actuales. No se han tocado.
