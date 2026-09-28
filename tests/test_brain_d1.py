"""Fase 2D: detección D1 (acción pendiente confirmada pero no ejecutada en el
turno) dentro de pensar(). Solo detección: no debe ejecutar nada
automáticamente ni cambiar el comportamiento del gate."""
import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

FILE_A = r"E:\descargas ryzen\informe.pdf"
CONF_B = "confirmo borrado"


def resp(stop, content, usage=None):
    return NS(stop_reason=stop, content=content, usage=usage)


def tool(name, id="t1", input=None):
    return NS(type="tool_use", name=name, id=id, input=input if input is not None else {})


def bloque_texto(t="ok"):
    return NS(type="text", text=t)


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        brain._ultima_traza = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_ultima_traza", None)

    def _armar_pendiente(self):
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")

    def correr(self, respuestas, txt):
        out = io.StringIO()
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             contextlib.redirect_stdout(out):
            brain.pensar(txt)
        return out.getvalue()


class TestD1Deteccion(Base):
    def test_d1_detectado_pendiente_confirmado_no_ejecutado(self):
        self._armar_pendiente()
        out = self.correr([resp("end_turn", [bloque_texto("Listo, borrado")])], CONF_B)
        t = brain.ultima_traza()
        self.assertTrue(t["d1_detectado"])
        self.assertIn("[D1] pendiente no ejecutado: tool=borrar_archivo", out)

    def test_no_detectado_sin_pendiente(self):
        out = self.correr([resp("end_turn", [bloque_texto("hola")])], "hola")
        t = brain.ultima_traza()
        self.assertFalse(t.get("d1_detectado"))
        self.assertNotIn("[D1]", out)

    def test_no_detectado_texto_no_es_confirmacion(self):
        self._armar_pendiente()
        out = self.correr([resp("end_turn", [bloque_texto("hola, que tal")])], "hola, que tal")
        t = brain.ultima_traza()
        self.assertFalse(t["d1_detectado"])
        self.assertNotIn("[D1]", out)

    def test_no_detectado_tool_si_ejecutada(self):
        self._armar_pendiente()
        with patch("brain._ejecutar", return_value=brain._ok("Eliminado: x")):
            out = self.correr([
                resp("tool_use", [tool("borrar_archivo", "t1", {"ruta": FILE_A})]),
                resp("end_turn", [bloque_texto("Hecho")]),
            ], CONF_B)
        t = brain.ultima_traza()
        self.assertFalse(t["d1_detectado"])
        self.assertNotIn("[D1]", out)


if __name__ == "__main__":
    unittest.main()
