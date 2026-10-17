"""Prueba manual del vocabulario de Whisper con audio real (no va en CI: carga Piper y Whisper).

Sintetiza frases con la voz de Piper de JARVIS y las transcribe con el mismo modelo y los
mismos parámetros que la escucha normal, una vez con el contexto de siempre (WHISPER_PROMPT)
y otra con el vocabulario propio delante. Compara la tasa de error por palabras (WER) de
frases normales (no debe empeorar) y de frases con términos técnicos (debe mejorar).

    venv\\Scripts\\python.exe probar_vocabulario.py

La voz es sintética, no la tuya: un resultado bueno aquí no garantiza el de tu micrófono.
"""
import re
import sys
import unicodedata
import wave
import tempfile
import os

import numpy as np
from scipy.signal import resample_poly

NORMALES = [
    "Buenos días, qué tal estás hoy",
    "Apunta que mañana tengo que comprar pan",
    "Pon música tranquila para trabajar",
    "Recuérdame llamar a mi madre a las siete",
    "Qué tiempo va a hacer el fin de semana en Sevilla",
    "Lee mi último correo",
    "Confirmo envío",
    "Busca restaurantes cerca de casa",
    "Cuánto queda para la reunión de las cinco",
    "Baja el volumen un poco",
]
TECNICAS = [
    "Te acuerdas de lo que hablábamos de Kafka",
    "Despliega el servicio en Kubernetes",
    "Crea una API con FastAPI",
    "Revisa el proyecto de Spring Boot",
    "Arranca Ollama con el modelo pequeño",
    "Whisper y Piper funcionan en local",
    "Configura el consumidor de Kafka con Docker",
    "Pasa los tests de JARVIS con pytest",
]


def normalizar(texto):
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", t).split()


def wer(referencia, hipotesis):
    r, h = normalizar(referencia), normalizar(hipotesis)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        previo, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            previo, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, previo + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def main():
    import whisper
    from piper.voice import PiperVoice
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import audio  # noqa: E402  (solo para WHISPER_PROMPT, vocabulario y la ruta de la voz)

    voz = PiperVoice.load(audio.VOICE_MODEL)
    modelo = whisper.load_model("small")
    base = audio.WHISPER_PROMPT
    con_vocabulario = audio.prompt_whisper()
    print(f"Vocabulario: {len(audio.vocabulario())} términos")

    def transcribir(frase, prompt):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            ruta = f.name
        try:
            with wave.open(ruta, "wb") as wf:
                voz.synthesize_wav(frase, wf)
            with wave.open(ruta, "rb") as wf:
                pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                tasa = wf.getframerate()
        finally:
            os.unlink(ruta)
        muestras = resample_poly(pcm.astype(np.float32) / 32768.0, 16000, tasa).astype(np.float32)
        return modelo.transcribe(muestras, language="es", initial_prompt=prompt)["text"].strip()

    for nombre, frases in (("NORMALES", NORMALES), ("TÉCNICAS", TECNICAS)):
        total = {"base": 0.0, "vocab": 0.0}
        print(f"\n{nombre}")
        for frase in frases:
            sin, con = transcribir(frase, base), transcribir(frase, con_vocabulario)
            total["base"] += wer(frase, sin)
            total["vocab"] += wer(frase, con)
            print(f"  «{frase}»\n     sin: {sin}\n     con: {con}")
        n = len(frases)
        print(f"  WER medio sin vocabulario: {total['base'] / n:.3f} | con vocabulario: {total['vocab'] / n:.3f}")


if __name__ == "__main__":
    main()
