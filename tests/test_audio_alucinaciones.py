"""Filtro de alucinaciones de Whisper: frases típicas de subtítulos de YouTube
("¡Suscríbete!") y audio sin voz (no_speech_prob > 0.7 en todos los
segmentos) se tratan como "no entendido". Sin micro ni modelos: sounddevice,
piper y pycaw sustituidos por mocks."""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        self.assertEqual(kwargs["initial_prompt"], audio.WHISPER_PROMPT)


if __name__ == "__main__":
    unittest.main()
