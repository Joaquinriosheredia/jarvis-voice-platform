"""Filtro de alucinaciones de Whisper: frases típicas de subtítulos de YouTube
("¡Suscríbete!") y audio sin voz (no_speech_prob > 0.7 en todos los
segmentos) se tratan como "no entendido". Sin micro ni modelos: sounddevice,
piper y pycaw sustituidos por mocks."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

_STUBS = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402


def _resultado(texto, no_speech=(0.1,)):
    return {"text": texto, "segments": [{"no_speech_prob": p} for p in no_speech]}


def _filtrar(resultado):
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        texto = audio._filtrar_transcripcion(resultado)
    return texto, salida.getvalue()


class TestFiltro(unittest.TestCase):
    def test_suscribete_es_alucinacion(self):
        texto, log = _filtrar(_resultado(" ¡Suscríbete!"))
        self.assertEqual(texto, "")
        self.assertIn("[AUDIO] alucinación detectada: ¡Suscríbete!", log)

    def test_todas_las_frases(self):
        for frase in ("¡Suscríbete!", "suscribete al canal", "Gracias por ver el vídeo",
                      "No olvides darle a la campanita", "Dale like", "Nos vemos en el próximo vídeo",
                      "en el próximo video", "¡Hasta la próxima!"):
            with self.subTest(frase=frase):
                self.assertEqual(_filtrar(_resultado(frase))[0], "")

    def test_mayusculas(self):
        self.assertEqual(_filtrar(_resultado("SUSCRÍBETE"))[0], "")

    def test_orden_normal_pasa(self):
        texto, log = _filtrar(_resultado(" Jarvis, ¿qué tiempo hace en Sevilla? "))
        self.assertEqual(texto, "Jarvis, ¿qué tiempo hace en Sevilla?")
        self.assertEqual(log, "")

    def test_no_speech_alto_en_todos_vacio(self):
        texto, log = _filtrar(_resultado("Jarvis, abre YouTube", no_speech=(0.8, 0.95)))
        self.assertEqual(texto, "")
        self.assertIn("sin voz", log)
        self.assertNotIn("alucinación", log)  # no llega al filtro de frases

    def test_no_speech_alto_solo_en_algunos_pasa(self):
        texto, _ = _filtrar(_resultado("Jarvis, abre YouTube", no_speech=(0.9, 0.2)))
        self.assertEqual(texto, "Jarvis, abre YouTube")

    def test_no_speech_exactamente_umbral_pasa(self):
        self.assertEqual(_filtrar(_resultado("hola", no_speech=(0.7,)))[0], "hola")

    def test_sin_segmentos_no_rompe(self):
        self.assertEqual(_filtrar({"text": "hola"})[0], "hola")
        self.assertEqual(_filtrar({"text": "hola", "segments": [{}]})[0], "hola")

    def test_prompt_sin_crear(self):
        self.assertNotIn("crear", audio.WHISPER_PROMPT)
        self.assertNotIn("crear", audio.prompt_whisper())


class TestEscuchar(unittest.TestCase):
    """escuchar() completo con micro simulado: voz, luego silencio."""
    def _escuchar(self, resultado_whisper):
        voz      = np.full(audio.FRAME_SIZE, 5000, dtype=np.int16)
        silencio = np.zeros(audio.FRAME_SIZE, dtype=np.int16)
        tramas   = [voz] * 10 + [silencio] * audio.SILENCIO_FRAMES
        stream   = MagicMock()
        stream.read.side_effect = [(t, False) for t in tramas]
        whisper  = MagicMock()
        whisper.transcribe.return_value = resultado_whisper
        bridge   = MagicMock()
        with patch.object(audio, "sd") as sd, patch.object(audio, "ui_bridge", bridge), \
             patch.object(audio, "_vlc_activo", return_value=False), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio, "JARVIS_HABLANDO", False), \
             contextlib.redirect_stdout(io.StringIO()):
            sd.RawInputStream.return_value.__enter__.return_value = stream
            texto = audio.escuchar(whisper)
        return texto, bridge, whisper

    def test_alucinacion_devuelve_vacio_y_no_entendido(self):
        texto, bridge, _ = self._escuchar(_resultado("¡Suscríbete!"))
        self.assertEqual(texto, "")
        bridge.emit.assert_any_call("state", state="not_understood")
        self.assertNotIn("user_message", [c.args[0] for c in bridge.emit.call_args_list])

    def test_sin_voz_devuelve_vacio(self):
        texto, bridge, _ = self._escuchar(_resultado("Gracias.", no_speech=(0.9,)))
        self.assertEqual(texto, "")
        bridge.emit.assert_any_call("state", state="not_understood")

    def test_orden_real_se_devuelve(self):
        texto, bridge, whisper = self._escuchar(_resultado(" Jarvis, pon música "))
        self.assertEqual(texto, "Jarvis, pon música")
        bridge.emit.assert_any_call("user_message", text="Jarvis, pon música")
        _, kwargs = whisper.transcribe.call_args
        self.assertEqual(kwargs["initial_prompt"], audio.prompt_whisper())
        self.assertTrue(kwargs["initial_prompt"].endswith(audio.WHISPER_PROMPT))


# ── Vocabulario propio (vocabulario_whisper.txt): contexto inicial de la escucha normal ──
def _archivo(texto):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write(texto)
    f.close()
    return f.name


class TestVocabulario(unittest.TestCase):
    def setUp(self):
        p = patch.object(audio, "_vocabulario", None)
        p.start()
        self.addCleanup(p.stop)

    def test_archivo_por_defecto_trae_los_terminos_iniciales(self):
        terminos = audio.vocabulario(audio.RUTA_VOCABULARIO)
        for t in ("Kafka", "Kubernetes", "FastAPI", "Spring Boot", "Ollama", "Whisper", "Piper", "JARVIS"):
            self.assertIn(t, terminos)
        self.assertLessEqual(len(terminos), audio.MAX_TERMINOS)

    def test_comentarios_vacias_repetidos_y_limites(self):
        ruta = _archivo("# comentario\n\nKafka\nkafka\n  Spring   Boot ,\n" + "x" * 50 + "\nconfirmo envío\n"
                        + "\n".join(f"t{i}" for i in range(60)))
        self.addCleanup(os.remove, ruta)
        terminos = audio.vocabulario(ruta)
        self.assertEqual(terminos[:3], ["Kafka", "Spring Boot", "t0"])
        self.assertEqual(len(terminos), audio.MAX_TERMINOS)
        self.assertFalse(any("confirm" in t.lower() for t in terminos))

    def test_sin_archivo_queda_el_prompt_de_siempre(self):
        with patch.dict(os.environ, {"JARVIS_VOCABULARIO": r"C:\no\existe.txt"}):
            self.assertEqual(audio.prompt_whisper(), audio.WHISPER_PROMPT)

    def test_vocabulario_delante_y_ordenes_al_final(self):
        ruta = _archivo("Kafka\nOllama\n")
        self.addCleanup(os.remove, ruta)
        with patch.dict(os.environ, {"JARVIS_VOCABULARIO": ruta}):
            prompt = audio.prompt_whisper()
        self.assertEqual(prompt, "Kafka, Ollama. " + audio.WHISPER_PROMPT)
        self.assertTrue(prompt.endswith(audio.WHISPER_PROMPT))

    def test_el_prompt_completo_no_lleva_crear(self):
        self.assertNotIn("crear", audio.prompt_whisper())

    def test_los_umbrales_no_cambian(self):
        self.assertEqual((audio.WHISPER_MIN_LOGPROB, audio.WHISPER_MAX_COMPRESSION,
                          audio.WHISPER_MIN_LOGPROB_CONFIRMACION), (-1.0, 2.4, -0.7))


if __name__ == "__main__":
    unittest.main()
