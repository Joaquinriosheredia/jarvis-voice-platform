# Informe de latencia

Datos reales de `logs/turnos.jsonl`: 42 turnos, del 16 al 17 de octubre de 2026. Todos se sintetizaron con Piper. 41 pasaron por Claude y 1 por la ruta de memoria. El informe solo usa los campos numéricos del log, nunca el texto de los turnos. Para regenerarlo:

```powershell
venv\Scripts\python.exe replay.py --latencia
```

| Etapa | n | p50 | p95 | Máximo |
|---|---:|---:|---:|---:|
| Transcripción (Whisper) | 42 | 567 ms | 2435 ms | 9321 ms |
| Router | 42 | 0 ms | 0 ms | 1 ms |
| Modelo (Claude, todas las iteraciones) | 41 | 1399 ms | 2720 ms | 4005 ms |
| — primer token (TTFT) | 41 | 611 ms | 904 ms | 1186 ms |
| Tools (solo los turnos con tools) | 17 | 0 ms | 2274 ms | 2274 ms |
| TTS hasta el primer audio | 42 | 139 ms | 380 ms | 435 ms |
| **Total, hasta que empieza la voz** | 42 | **2306 ms** | **5785 ms** | **10606 ms** |

## Cuello de botella

**El modelo, tanto en la mediana como en p95.** En la mediana es unos 1,4 s de 2,3 s (60 %). Su tiempo depende sobre todo de dos cosas:

- **Las iteraciones.** Un turno con tools (envío de correo, `get_mi_email`) hace dos llamadas y tarda 2 a 4 s. Un turno sin tools tarda 0,7 a 1,6 s.
- **Los tokens de salida.** La mediana son 56 tokens; un turno con 271 tardó 4 s.

La caché funciona: el 78 % de los tokens de entrada se leen de caché y el prefijo solo se reescribe al empezar sesión.

**Whisper causa los picos extremos.** Los dos turnos más lentos (9,3 s y 4,8 s de transcripción) son también los de confianza más baja (logprob medio de −0,99 y −0,78). El resto ronda −0,2. Es el patrón del *fallback* por temperatura de Whisper: cuando un segmento sale dudoso, lo decodifica otra vez con temperatura 0,2, 0,4… En `jarvis.log` hay al menos un turno con `temp=0.2`.

## Qué se cambió y qué no

**Cambiado: solo observabilidad.** Antes no se registraba la temperatura ni la duración del audio, así que el fallback no se podía demostrar turno a turno. Ahora cada turno guarda dos campos numéricos nuevos, sin texto:

- `audio_ms`: duración del audio capturado.
- `whisper_temp`: temperatura máxima de los segmentos. Si es mayor que 0, hubo fallback.

`replay.py --latencia` cuenta en cuántos turnos hubo fallback.

**No cambiado, por no ser una mejora clara y segura:**

- **Hablar la primera frase antes de `end_turn`.** Ahorraría cerca de `modelo − TTFT`, unos 0,7 s en la mediana. Rompe una regla de seguridad del streaming: no se dice nada hasta saber que la iteración no usa tools, porque el texto previo a un tool_use se descarta y lo que se dice pasa por el gate.
- **Limitar el fallback de Whisper.** Por ejemplo, `temperature=(0.0, 0.2)` en vez de las 6 temperaturas por defecto. Recortaría la cola, pero con 2 casos de 42 y sin saber todavía la temperatura de cada turno, no se puede afirmar que no empeore la transcripción. Revisar cuando `whisper_temp` tenga datos de unos días.
- **Cambiar de modelo** o reducir el prompt: cambia la calidad de las respuestas y del gate, y eso no es una optimización de latencia.
