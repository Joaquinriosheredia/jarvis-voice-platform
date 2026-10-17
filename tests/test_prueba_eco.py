"""prueba_eco.py: el análisis (puro) y que nada de lo grabado o transcrito llegue al disco.
La medición con micrófono y altavoz se hace a mano (protocolo en el README)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import parada  # noqa: E402
import prueba_eco as pe  # noqa: E402

PARA = "Para configurar Kafka necesitas al menos tres brokers."
CALLE = "Si quieres que me calle, di silencio o basta."
SECRETO = "texto de la sala que no debe salir"


class TestAnalisis(unittest.TestCase):
    def test_similitud(self):
        self.assertEqual(pe.similitud("para configurar kafka necesitas al menos tres brokers", PARA), 1.0)
        self.assertLess(pe.similitud("qué hora es", PARA), pe.UMBRAL_ECO)
        self.assertEqual(pe.similitud("", PARA), 0.0)

    def test_eco_como_entrada_y_cola(self):
        r = pe.analizar(PARA, "Para configurar Kafka necesitas tres brokers", "", [])
        self.assertTrue(r["eco"])
        self.assertFalse(r["cola"])
        limpio = pe.analizar(PARA, "", "", [])
        self.assertEqual((limpio["eco"], limpio["entrada"]), (False, False))
        self.assertTrue(pe.analizar(PARA, "", "tres brokers al menos necesitas Kafka", [])["cola"] is False)

    def test_orden_dentro_de_la_frase_filtrada_por_eco_probable(self):
        r = pe.analizar(CALLE, "", "", ["silencio", "basta"])
        self.assertEqual((r["ordenes"], r["falsas_paradas"]), (2, 0))

    def test_falsa_parada_si_eco_probable_no_la_reconoce(self):
        # «Para» suelto es una orden, y la frase no tiene un trozo que solo sea «para»
        self.assertTrue(parada.clasificar("Para."))
        r = pe.analizar(PARA, "", "", ["Para."])
        self.assertEqual((r["ordenes"], r["falsas_paradas"]), (1, 1))

    def test_resumen_e_informe_sin_texto(self):
        resultados = [pe.analizar(PARA, SECRETO, "", ["Para."]), pe.analizar(CALLE, "", "", [])]
        s = pe.resumen(resultados)
        self.assertEqual((s["frases"], s["falsas_paradas"], s["aciertos"]), (2, 1, 1))
        for salida in (pe.informe(resultados, "altavoces"), pe.linea_json(resultados, "altavoces")):
            self.assertNotIn("sala", salida)
        datos = json.loads(pe.linea_json(resultados, "altavoces"))
        self.assertEqual(set(datos["frases"][0]), {"entrada", "similitud", "eco", "cola", "segmentos", "ordenes",
                                                    "falsas_paradas"})
        self.assertEqual(pe.resumen([]), {"frases": 0})

    def test_frases_con_ordenes_para_medir_falsas_paradas(self):
        self.assertTrue(any(parada.eco_probable("silencio", f) for f in pe.FRASES))
        self.assertTrue(any(f.lower().startswith("para ") for f in pe.FRASES))


class FalsoStream:
    def __init__(self, tramas):
        self.tramas = iter(tramas)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return next(self.tramas), False


class TestMedicionConMocks(unittest.TestCase):
    """medir() de punta a punta con audio y Whisper falsos: no escribe nada ni imprime lo oído."""

    def test_no_guarda_audio_ni_texto(self):
        trama = (np.ones(480, dtype=np.int16) * 3000).tobytes()
        audio = NS(sd=MagicMock(), SAMPLE_RATE=16000, FRAME_SIZE=480, MICROFONO=None, FRAMES_INICIO=3,
                   UMBRAL_RMS=300, modelo_whisper=None,
                   _sintetizar=lambda f: (np.ones(22050, dtype=np.int16) * 1000, 22050),
                   prompt_whisper=lambda: "", _filtrar_transcripcion=lambda r: r["text"],
                   _confianza=lambda r: (-0.2, 1.0), _baja_confianza=lambda lp, cr: False,
                   _rms=lambda b: 3000.0, _transcribir_orden=lambda s: SECRETO)
        audio.sd.RawInputStream.side_effect = lambda **k: FalsoStream([trama] * 1000)
        modelo = MagicMock()
        modelo.transcribe.return_value = {"text": SECRETO}
        whisper = MagicMock()
        whisper.load_model.return_value = modelo
        carpeta = tempfile.mkdtemp()
        destino = os.path.join(carpeta, "eco.jsonl")
        salida = io.StringIO()
        with patch.dict(sys.modules, {"audio": audio, "whisper": whisper}), \
             patch.object(pe, "FRASES", pe.FRASES[:2]), contextlib.redirect_stdout(salida), \
             patch.object(sys.stdout, "reconfigure", create=True):
            pe.main(["--etiqueta", "prueba", "--json", destino])
        self.assertNotIn(SECRETO, salida.getvalue())
        with open(destino, encoding="utf-8") as f:
            contenido = f.read()
        self.assertNotIn(SECRETO, contenido)
        self.assertEqual(json.loads(contenido)["resumen"]["frases"], 2)
        self.assertEqual(os.listdir(carpeta), ["eco.jsonl"])   # ni un .wav

    def test_importar_no_mide_nada(self):
        self.assertNotIn("whisper", pe.__dict__)
        self.assertNotIn("audio", pe.__dict__)


if __name__ == "__main__":
    unittest.main()
