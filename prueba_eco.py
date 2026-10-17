"""Prueba de eco y altavoz: ¿el micrófono oye a JARVIS como si fuera el usuario?

Diagnóstico manual: no se ejecuta solo, ni en los tests ni al arrancar JARVIS. Cierra JARVIS
antes (usan el mismo micrófono). Ver el protocolo en el README («Prueba de eco y altavoz»).

    venv\\Scripts\\python.exe prueba_eco.py
    venv\\Scripts\\python.exe prueba_eco.py --etiqueta "altavoces 75% 1 m"
    venv\\Scripts\\python.exe prueba_eco.py --etiqueta "auriculares" --json logs\\eco.jsonl

Para cada frase fija, la dice con el TTS de JARVIS (Piper o ElevenLabs, como en un turno) y
graba el micrófono mientras suena y COLA_S segundos después. Luego mide, con el mismo Whisper
y los mismos filtros que JARVIS:
  - eco como entrada: lo grabado, transcrito como en escuchar(), pasa los filtros de confianza
    y se parece a la frase (sería un turno del usuario con las palabras de JARVIS);
  - cola: lo mismo solo con lo grabado tras terminar la frase (lo que oiría el siguiente
    escuchar() si el eco de la sala dura);
  - falsas paradas: la escucha de parada (DetectorVoz, como mientras JARVIS habla) corta un
    segmento, Whisper lo entiende como orden de silencio o apagado y eco_probable no lo filtra:
    JARVIS se cortaría a sí mismo.

Privacidad: ni el audio ni el texto transcrito se guardan en disco ni se imprimen (el micro
también oye la sala). Solo métricas: aciertos, similitud y recuentos. --json añade una línea
con esas métricas.
"""
import datetime
import difflib
import json
import os
import sys

import parada

FRASES = [
    "Son las diez y cuarto de la mañana.",
    "Para configurar Kafka necesitas al menos tres brokers.",          # empieza por «para»
    "Tienes dos correos sin leer y una reunión a las cinco.",
    "Si quieres que me calle, di silencio o basta.",                   # lee órdenes de parada
    "Espera un momento, estoy buscando el archivo.",                    # «espera» es una orden
    "El despliegue terminó bien y el CI está en verde.",
    "Hasta luego, que tengas un buen día.",                             # frase de apagado
]
COLA_S     = 1.0
UMBRAL_ECO = 0.5    # similitud a partir de la cual lo transcrito es la frase de JARVIS
FRAME_MS   = 30


# ── ANÁLISIS (puro: sin audio ni Whisper) ─────────────────────────
def similitud(transcrito, frase):
    """0..1: parecido por palabras (sin tildes, mayúsculas ni puntuación)."""
    a, b = parada.normalizar(transcrito).split(), parada.normalizar(frase).split()
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 2)


def analizar(frase, entrada, cola, ordenes):
    """Métricas de una frase. entrada / cola: texto que escuchar() habría devuelto (""
    si los filtros lo descartan). ordenes: textos de los segmentos de la escucha de parada.
    Devuelve solo números y booleanos."""
    sim, sim_cola = similitud(entrada, frase), similitud(cola, frase)
    paradas = [t for t in ordenes if parada.clasificar(t)]
    falsas = [t for t in paradas if not parada.eco_probable(t, frase)]
    return {
        "entrada": bool(entrada),
        "similitud": sim,
        "eco": bool(entrada) and sim >= UMBRAL_ECO,
        "cola": bool(cola) and sim_cola >= UMBRAL_ECO,
        "segmentos": len(ordenes),
        "ordenes": len(paradas),
        "falsas_paradas": len(falsas),
    }


def resumen(resultados):
    n = len(resultados)
    if not n:
        return {"frases": 0}
    return {
        "frases": n,
        "eco": sum(r["eco"] for r in resultados),
        "cola": sum(r["cola"] for r in resultados),
        "entradas": sum(r["entrada"] for r in resultados),
        "similitud_media": round(sum(r["similitud"] for r in resultados) / n, 2),
        "ordenes_filtradas": sum(r["ordenes"] - r["falsas_paradas"] for r in resultados),
        "falsas_paradas": sum(r["falsas_paradas"] for r in resultados),
        "aciertos": sum(not r["eco"] and not r["cola"] and not r["falsas_paradas"] for r in resultados),
    }


def informe(resultados, etiqueta=""):
    """Tabla de métricas. Nunca incluye lo transcrito (no lo recibe)."""
    filas = [f"Prueba de eco{f' ({etiqueta})' if etiqueta else ''}",
             f"{'#':>2} {'eco':>4} {'cola':>5} {'simil.':>7} {'segm.':>6} {'órdenes':>8} {'falsas':>7}"]
    for i, r in enumerate(resultados, 1):
        filas.append(f"{i:>2} {'SÍ' if r['eco'] else 'no':>4} {'SÍ' if r['cola'] else 'no':>5} "
                     f"{r['similitud']:>7.2f} {r['segmentos']:>6} {r['ordenes']:>8} {r['falsas_paradas']:>7}")
    s = resumen(resultados)
    if s["frases"]:
        filas.append(f"Aciertos (ni eco, ni cola, ni falsa parada): {s['aciertos']}/{s['frases']} · "
                     f"eco como entrada {s['eco']} · cola {s['cola']} · falsas paradas {s['falsas_paradas']} · "
                     f"órdenes filtradas por eco_probable {s['ordenes_filtradas']}")
    return "\n".join(filas)


def linea_json(resultados, etiqueta=""):
    """Una línea JSON con las métricas (sin texto transcrito ni audio)."""
    return json.dumps({"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "etiqueta": etiqueta, "resumen": resumen(resultados), "frases": resultados},
                      ensure_ascii=False)


# ── MEDICIÓN (hardware: solo al ejecutar el script) ──────────────
def _frames_ref(data, rate, n_frames):
    """RMS de lo que suena, por tramas de 30 ms alineadas con las del micro."""
    import numpy as np
    paso = max(1, int(rate * FRAME_MS / 1000))
    refs = [float(np.sqrt(np.mean(data[i:i + paso].astype(np.float32) ** 2)))
            for i in range(0, len(data), paso)]
    return (refs + [0.0] * n_frames)[:n_frames]


def _grabar_mientras_suena(audio, data, rate):
    """(tramas del micro durante la frase, tramas de la cola). Todo en memoria."""
    import numpy as np
    sd = audio.sd
    tramas = []
    duracion = len(data) / rate
    total = int((duracion + COLA_S) * 1000 / FRAME_MS)
    with sd.RawInputStream(samplerate=audio.SAMPLE_RATE, channels=1, dtype="int16",
                           blocksize=audio.FRAME_SIZE, device=audio.MICROFONO) as stream:
        sd.play(data, rate)
        while len(tramas) < total:
            frame, _ = stream.read(audio.FRAME_SIZE)
            tramas.append(np.frombuffer(bytes(frame), dtype=np.int16).copy())
    sd.stop()
    corte = int(duracion * 1000 / FRAME_MS)
    return tramas[:corte], tramas[corte:]


def _como_escuchar(audio, modelo, tramas):
    """Texto que escuchar() devolvería con esas tramas ("" si los filtros lo descartan)."""
    import numpy as np
    if len(tramas) < audio.FRAMES_INICIO:
        return ""
    datos = np.concatenate(tramas).astype(np.float32)
    pico = float(np.max(np.abs(datos)))
    if pico == 0:
        return ""
    resultado = modelo.transcribe(datos / pico * 0.9, language="es", initial_prompt=audio.prompt_whisper())
    texto = audio._filtrar_transcripcion(resultado)
    if len(texto) >= 2 and audio._baja_confianza(*audio._confianza(resultado)):
        return ""
    return texto if len(texto) >= 2 else ""


def _escucha_de_parada(audio, tramas, refs):
    """Textos de los segmentos que la escucha de parada transcribiría (como mientras habla)."""
    detector = parada.DetectorVoz(audio.UMBRAL_RMS * 2)
    textos, ultimos = [], []
    for frame, ref in zip(tramas, refs + [0.0] * len(tramas)):
        ultimos = (ultimos + [ref])[-5:]
        segmento = detector.trama(audio._rms(frame.tobytes()), max(ultimos), frame)
        if segmento is not None:
            textos.append(audio._transcribir_orden(segmento))
    return [t for t in textos if t]


def medir(frases=None):
    frases = frases or FRASES
    import whisper
    import audio
    print("Cargando Whisper y la voz…")
    modelo = whisper.load_model("small")
    audio.modelo_whisper = modelo
    resultados = []
    for i, frase in enumerate(frases, 1):
        data, rate = audio._sintetizar(frase)
        durante, cola = _grabar_mientras_suena(audio, data, rate)
        refs = _frames_ref(data, rate, len(durante))
        r = analizar(frase, _como_escuchar(audio, modelo, durante + cola), _como_escuchar(audio, modelo, cola),
                     _escucha_de_parada(audio, durante + cola, refs))
        resultados.append(r)
        print(f"  frase {i}/{len(frases)}: eco={'sí' if r['eco'] else 'no'} falsas paradas={r['falsas_paradas']}")
    return resultados


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    etiqueta = argv[argv.index("--etiqueta") + 1] if "--etiqueta" in argv[:-1] else ""
    destino = argv[argv.index("--json") + 1] if "--json" in argv[:-1] else None
    resultados = medir()
    print(informe(resultados, etiqueta))
    if destino:
        os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
        with open(destino, "a", encoding="utf-8") as f:
            f.write(linea_json(resultados, etiqueta) + "\n")
        print(f"Métricas añadidas a {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
