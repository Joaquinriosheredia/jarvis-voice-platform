"""Umbral de confianza de Whisper en audio.escuchar: avg_logprob y compression_ratio.
Sin micro ni modelos: sounddevice, piper, pycaw y scipy sustituidos por mocks."""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics  # noqa: E402  (antes de audio: así audio usa este mismo módulo)

# scipy también: otro test ya importó audio dentro de patch.dict y scipy no se puede reimportar
_STUBS = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw",
                                   "scipy", "scipy.io", "scipy.io.wavfile")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402

TEXTO = "Jarvis borra el informe"


def resultado(texto=TEXTO, logprobs=(-0.3,), compresiones=(1.2,), no_speech=0.1):
    segs = [{"avg_logprob": lp, "compression_ratio": cr, "no_speech_prob": no_speech}
            for lp, cr in zip(logprobs, compresiones)]
    return {"text": texto, "segments": segs}


class TestConfianza(unittest.TestCase):
    def setUp(self):
        self.addCleanup(metrics.descartar_turno)

    def escuchar(self, res, pendiente=False):
        voz      = np.full(audio.FRAME_SIZE, 5000, dtype=np.int16)
        silencio = np.zeros(audio.FRAME_SIZE, dtype=np.int16)
        stream   = MagicMock()
        stream.read.side_effect = [(t, False) for t in [voz] * 10 + [silencio] * audio.SILENCIO_FRAMES]
        whisper  = MagicMock()
        whisper.transcribe.return_value = res
        bridge   = MagicMock()
        salida   = io.StringIO()
        with patch.object(audio, "sd") as sd, patch.object(audio, "ui_bridge", bridge), \
             patch.object(audio, "_vlc_activo", return_value=False), \
             patch.object(audio, "JARVIS_HABLANDO", False), \
             patch.object(audio, "hay_confirmacion_pendiente", lambda: pendiente), \
             contextlib.redirect_stdout(salida):
            sd.RawInputStream.return_value.__enter__.return_value = stream
            texto = audio.escuchar(whisper)
        self.bridge, self.log = bridge, salida.getvalue()
        return texto

    def assert_no_entendido(self):
        self.bridge.emit.assert_any_call("state", state="not_understood")
        self.assertNotIn("user_message", [c.args[0] for c in self.bridge.emit.call_args_list])

    def test_logprob_alto_pasa(self):
        self.assertEqual(self.escuchar(resultado(logprobs=(-0.3, -0.5), compresiones=(1.1, 1.4))), TEXTO)
        self.bridge.emit.assert_any_call("user_message", text=TEXTO)

    def test_logprob_bajo_descartado(self):
        self.assertEqual(self.escuchar(resultado(logprobs=(-1.2, -1.0), compresiones=(1.0, 1.0))), "")
        self.assert_no_entendido()
        self.assertIn("[WHISPER] descartado por baja confianza logprob=-1.10 cr=1.00", self.log)

    def test_compression_ratio_alto_descartado(self):
        self.assertEqual(self.escuchar(resultado(logprobs=(-0.2, -0.2), compresiones=(1.0, 2.8))), "")
        self.assert_no_entendido()
        self.assertIn("cr=2.80", self.log)

    def test_con_pendiente_umbral_estricto(self):
        self.assertEqual(self.escuchar(resultado(logprobs=(-0.9,)), pendiente=True), "")
        self.assert_no_entendido()
        self.assertIn("(confirmación pendiente)", self.log)
        self.assertEqual(self.escuchar(resultado(logprobs=(-0.9,)), pendiente=False), TEXTO)
        self.assertEqual(self.escuchar(resultado(logprobs=(-0.6,)), pendiente=True), TEXTO)

    def test_con_pendiente_y_sin_metrica_se_descarta(self):
        sin_metrica = {"text": "confirmo borrado", "segments": [{"no_speech_prob": 0.1}]}
        self.assertEqual(self.escuchar(sin_metrica, pendiente=True), "")
        self.assertEqual(self.escuchar(sin_metrica, pendiente=False), "confirmo borrado")

    def test_si_falla_la_consulta_del_pendiente_se_aplica_el_estricto(self):
        def roto():
            raise RuntimeError("brain no disponible")
        with patch.object(audio, "hay_confirmacion_pendiente", roto):
            self.assertTrue(audio._baja_confianza(-0.9, 1.0))

    def test_filtro_de_alucinaciones_sigue_funcionando(self):
        self.assertEqual(self.escuchar(resultado("¡Suscríbete!", logprobs=(-0.1,))), "")
        self.assertIn("alucinación detectada", self.log)
        self.assertEqual(self.escuchar(resultado("Gracias.", no_speech=0.9)), "")
        self.assertIn("sin voz", self.log)

    def test_el_log_no_contiene_el_texto(self):
        for res, pendiente in ((resultado(logprobs=(-1.5,)), False),
                               (resultado(compresiones=(3.0,)), False),
                               (resultado(logprobs=(-0.9,)), True)):
            self.escuchar(res, pendiente)
            self.assertIn("[WHISPER] descartado", self.log)
            self.assertNotIn(TEXTO, self.log)
            self.assertNotIn("borra el informe", self.log)

    def test_umbrales_por_entorno(self):
        with patch.dict(os.environ, {"WHISPER_MIN_LOGPROB": "-0.5", "WHISPER_MAX_COMPRESSION": "abc"}):
            self.assertEqual(audio._umbral("WHISPER_MIN_LOGPROB", -1.0), -0.5)
            self.assertEqual(audio._umbral("WHISPER_MAX_COMPRESSION", 2.4), 2.4)  # inválido → defecto
        self.assertEqual((audio.WHISPER_MIN_LOGPROB, audio.WHISPER_MAX_COMPRESSION), (-1.0, 2.4))

    def test_metrics_registra_la_confianza_media(self):
        with patch.object(metrics.ui_bridge, "emit"), patch.object(metrics, "_escribir"):
            self.escuchar(resultado(logprobs=(-0.2, -0.4), compresiones=(1.0, 1.0)))
            reg = metrics.cerrar_turno("x", "y")
        self.assertAlmostEqual(reg["whisper_logprob"], -0.3)
        self.assertIn("whisper_logprob", metrics.evento_ui(reg))


if __name__ == "__main__":
    unittest.main()
