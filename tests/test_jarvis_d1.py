"""Fase 2E: ejecución directa de la acción pendiente cuando pensar() detecta
D1. jarvis.py se importa con whisper/audio/skills/memoria sustituidos por
mocks (no se carga ningún modelo ni se toca el micro)."""
import atexit
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)  # no ejecutar el cleanup real al salir de los tests

FILE_A = r"E:\descargas ryzen\informe.pdf"
CONF_B = "confirmo borrado"


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        brain._ultima_traza = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_ultima_traza", None)
        jarvis.audio.reset_mock()

    def _armar_pendiente(self):
        with contextlib.redirect_stdout(io.StringIO()):
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")

    def _d1(self, detectado=True):
        brain._ultima_traza = {"d1_detectado": detectado, "tools_ejecutadas": []}

    def _fix(self, res):
        out = io.StringIO()
        with patch("brain._ejecutar", return_value=res) as ej, contextlib.redirect_stdout(out):
            self.devuelto = jarvis._d1_fix(CONF_B)
        return ej, out.getvalue()


class TestD1Fix(Base):
    def test_borrar_archivo_ejecutar_con_params_correctos(self):
        self._armar_pendiente()
        self._d1()
        ej, out = self._fix(brain._ok("Eliminado: informe.pdf"))
        ej.assert_called_once_with("borrar_archivo", {"ruta": brain._real(FILE_A)},
                                   jarvis.SKILLS, CONF_B)
        self.assertIn("[D1-FIX] ejecutado tool=borrar_archivo ok=True", out)
        self.assertIs(self.devuelto, True)

    def test_ok_true_habla_message(self):
        self._armar_pendiente()
        self._d1()
        self._fix(brain._ok("Eliminado: informe.pdf"))
        jarvis.audio.hablar.assert_called_once_with("Eliminado: informe.pdf")

    def test_ok_false_habla_error(self):
        self._armar_pendiente()
        self._d1()
        _, out = self._fix(brain._err("Error en borrar_archivo: no existe", "not_found"))
        jarvis.audio.hablar.assert_called_once_with("Error en borrar_archivo: no existe")
        self.assertIn("ok=False", out)
        self.assertIs(self.devuelto, True)  # ejecutó (aunque fallase): se suprime la respuesta

    def test_consume_pendiente_tras_ejecutar(self):
        self._armar_pendiente()
        self._d1()
        self._fix(brain._ok("Eliminado"))
        self.assertIsNone(brain._pendiente)

    def test_sin_d1_no_ejecuta(self):
        self._armar_pendiente()
        self._d1(detectado=False)
        ej, out = self._fix(brain._ok("x"))
        ej.assert_not_called()
        jarvis.audio.hablar.assert_not_called()
        self.assertIsNotNone(brain._pendiente)
        self.assertIs(self.devuelto, False)

    def test_sin_traza_no_ejecuta(self):
        ej, _ = self._fix(brain._ok("x"))
        ej.assert_not_called()
        self.assertIs(self.devuelto, False)

    def test_tool_fuera_de_lista_no_ejecuta(self):
        brain._pendiente = {"firma": ("borrar_multiples_emails", "[]"), "ts": brain.time.monotonic()}
        self._d1()
        ej, _ = self._fix(brain._ok("x"))
        ej.assert_not_called()
        self.assertIs(self.devuelto, False)


class TestPendienteInfo(Base):
    def test_none_sin_pendiente(self):
        self.assertIsNone(brain.pendiente_info())

    def test_consumir_pendiente_limpia(self):
        self._armar_pendiente()
        brain.consumir_pendiente()
        self.assertIsNone(brain._pendiente)
        self.assertIsNone(brain.pendiente_info())

    def test_apagar_pc_params_desde_firma(self):
        with contextlib.redirect_stdout(io.StringIO()):
            brain._gate("apagar_pc", {"accion": "reiniciar"}, "reinicia")
        info = brain.pendiente_info()
        self.assertEqual(info["params"], {"accion": "reiniciar"})
        self.assertEqual(brain._firma("apagar_pc", info["params"]), info["firma"])

    def test_params_reconstruidos_pasan_el_gate_real(self):
        # Sin esto, params={} haría que _gate_rutas bloquease siempre.
        self._armar_pendiente()
        info = brain.pendiente_info()
        self.assertEqual(brain._firma(info["tool"], info["params"]), info["firma"])
        self.assertIsNone(brain._gate_core(info["tool"], info["params"], CONF_B))


if __name__ == "__main__":
    unittest.main()
