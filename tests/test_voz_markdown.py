"""Saneado de markdown solo en la voz, y tools destructivas/visión llamadas directamente
con el gate como única confirmación. Sin audio ni API: todo mockeado."""
import atexit
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frases  # noqa: E402
import tts_elevenlabs  # noqa: E402

_HW = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw",
                                "scipy", "scipy.io", "scipy.io.wavfile")}
with patch.dict(sys.modules, _HW), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402

import brain  # noqa: E402
from skills import pantalla  # noqa: E402

_JV = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _JV), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)


class TestLimpiarParaVoz(unittest.TestCase):
    def test_cada_simbolo_se_elimina(self):
        casos = {
            "Es **muy** importante": "Es muy importante",
            "Es __muy__ importante": "Es muy importante",
            "Usa `git log` ahora": "Usa git log ahora",
            "# Resumen\nTodo bien": "Resumen\nTodo bien",
            "## Hallazgos": "Hallazgos",
            "- primero\n- segundo": "primero\nsegundo",
            "* primero\n* segundo": "primero\nsegundo",
            "Lee [la guía](https://docs.ejemplo.com/guia) antes": "Lee la guía antes",
            "Mira https://ejemplo.com/una/ruta/bastante/larga/para/leer y sigue": "Mira y sigue",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(frases.limpiar_para_voz(entrada), esperado, entrada)

    def test_no_toca_texto_normal(self):
        for t in ("Son las 10:30. Hace sol.", "7 * 8 = 56", "La versión 3.5 - estable", "ver_pantalla",
                  "Di confirmo captura"):
            self.assertEqual(frases.limpiar_para_voz(t), t)


class TestSoloEnLaVoz(unittest.TestCase):
    def hablar(self, texto, **kw):
        audio.sd.get_stream.return_value.active = False
        audio.sd.play.side_effect = None
        with patch.object(tts_elevenlabs, "sintetizar", return_value=None), \
             patch.object(audio, "_sintetizar_piper", return_value=(np.zeros(4, dtype=np.int16), 22050)) as piper, \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit") as emit, contextlib.redirect_stdout(io.StringIO()):
            audio.hablar(texto, **kw)
        return piper.call_args.args[0], emit

    def test_la_voz_va_limpia_y_la_pantalla_conserva_el_original(self):
        original = "**Ojo**: usa `git diff` y revisa [esto](https://x.com/a)."
        dicho, emit = self.hablar(original)
        self.assertEqual(dicho, "Ojo: usa git diff y revisa esto.")
        emit.assert_any_call("assistant_message", text=original)

    def test_con_texto_pantalla_tambien(self):
        dicho, emit = self.hablar("- **uno**", texto_pantalla="lista completa en **markdown**")
        self.assertEqual(dicho, "uno")
        emit.assert_any_call("assistant_message", text="lista completa en **markdown**")

    def test_frases_tambien_se_limpian(self):
        audio.sd.get_stream.return_value.active = False
        with patch.object(tts_elevenlabs, "sintetizar", return_value=None), \
             patch.object(audio, "_sintetizar_piper", return_value=(np.zeros(4, dtype=np.int16), 22050)) as piper, \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit"), contextlib.redirect_stdout(io.StringIO()):
            audio.hablar_frases(["Primera **frase** bastante larga.", "Segunda con `codigo` dentro."])
        self.assertEqual([c.args[0] for c in piper.call_args_list],
                         ["Primera frase bastante larga.", "Segunda con codigo dentro."])

    def test_review_sigue_diciendo_resumen_y_aviso(self):
        review = ("Tus cambios tienen un bug. Hay 1 hallazgo.\n\n"
                  "- **app/x.py**: consulta con `f-string`. Usa parámetros.")
        brain._ultima_traza = {"router": "review"}
        self.addCleanup(setattr, brain, "_ultima_traza", None)
        voz = jarvis._texto_voz(review)
        self.assertEqual(voz, "Tus cambios tienen un bug. Hay 1 hallazgo. El detalle está en pantalla.")
        self.assertEqual(frases.limpiar_para_voz(voz), voz)


class TestLlamadaDirectaConGate(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        p = patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def test_system_sin_pedir_confirmacion_en_texto(self):
        self.assertIn("Para apagar el PC llama directamente a apagar_pc", brain.SYSTEM)
        self.assertIn("Llama directamente a ver_pantalla", brain.SYSTEM)
        self.assertIn("Llamar a ver_pantalla no captura nada todavía", brain.SYSTEM)
        self.assertIn("Llamar a apagar_pc no apaga nada todavía", brain.SYSTEM)
        self.assertIn("no pidas tú la confirmación", brain.SYSTEM)
        self.assertIn("no llames a las tools fecha ni hora", brain.SYSTEM)
        self.assertIn("no uses markdown", brain.SYSTEM)
        self.assertNotIn("llama primero a fecha", brain.SYSTEM)
        self.assertNotIn("consulta antes fecha", brain.SYSTEM)
        desc = {t["name"]: t["description"] for t in brain.TOOLS}
        self.assertNotIn("Pide confirmación por voz antes", desc["ver_pantalla"])
        self.assertNotIn("SOLO si el usuario dice", desc["apagar_pc"])
        self.assertIn("Llamar a ver_pantalla no captura nada todavía", desc["ver_pantalla"])
        self.assertIn("solo captura tras 'confirmo captura' leído del micrófono", desc["ver_pantalla"])
        self.assertIn("Llamar a apagar_pc no apaga nada todavía", desc["apagar_pc"])
        self.assertIn("no pidas tú la confirmación", desc["ver_pantalla"])
        self.assertIn("no pidas tú la confirmación", desc["apagar_pc"])

    def test_apagar_sin_la_frase_no_ejecuta_nada(self):
        with patch("brain.subprocess.run") as run:
            res = brain._ejecutar("apagar_pc", {"accion": "apagar"}, {}, "apaga el ordenador")
        run.assert_not_called()
        self.assertFalse(res["ok"])
        self.assertIn("confirmo apagado del pc", res["message"])
        self.assertTrue(brain.hay_pendiente())
        with patch("brain.subprocess.run") as run:  # una frase cualquiera tampoco
            brain._ejecutar("apagar_pc", {"accion": "apagar"}, {}, "sí, hazlo")
        run.assert_not_called()

    def test_captura_sin_la_frase_no_captura(self):
        with patch.object(pantalla, "capturar_pantalla") as captura:
            res = brain._ejecutar("ver_pantalla", {}, {}, "qué ves")
        captura.assert_not_called()
        self.assertFalse(res["ok"])
        self.assertTrue(brain.hay_pendiente())

    def test_el_gate_y_las_frases_no_cambian(self):
        self.assertEqual(brain.CONFIRM_PHRASES["apagar_pc"], "confirmo apagado del pc")
        self.assertEqual(brain.CONFIRM_PHRASES["ver_pantalla"], "confirmo captura")
        self.assertEqual(brain.REQUIERE_CONFIRMACION, {"ver_pantalla"})
        self.assertEqual(brain.TOOL_RISK["apagar_pc"], "DESTRUCTIVE")


if __name__ == "__main__":
    unittest.main()
