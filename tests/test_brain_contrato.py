"""Fase 2C: contrato estructurado de resultado ({ok, message, error}) y su
propagación a pensar(). Nada de operaciones reales: subprocess/skills mockeados.
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

CONF_P = "confirmo apagado del pc"


def resp(stop, content, usage=None):
    return NS(stop_reason=stop, content=content, usage=usage)


def tool(name, id="t1", input=None):
    return NS(type="tool_use", name=name, id=id, input=input if input is not None else {})


def texto(t="ok"):
    return NS(type="text", text=t)


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        brain._ultima_traza = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_ultima_traza", None)


class TestEnvolver(unittest.TestCase):
    def test_bloqueado_es_error(self):
        res = brain._envolver("BLOQUEADO: motivo cualquiera")
        self.assertEqual(res, {"ok": False, "message": "BLOQUEADO: motivo cualquiera", "error": None})

    def test_eliminado_es_ok(self):
        res = brain._envolver("Eliminado: X")
        self.assertEqual(res, {"ok": True, "message": "Eliminado: X", "error": None})


class TestApagarPc(Base):
    def test_ok_devuelve_contrato_estructurado(self):
        with patch("brain.subprocess.run", return_value=NS(returncode=0)) as run:
            brain._gate("apagar_pc", {"accion": "apagar"}, "apágate")  # arma el pendiente
            res = brain._ejecutar("apagar_pc", {"accion": "apagar"}, {}, CONF_P)
        self.assertEqual(res, {"ok": True, "message": "Comando de apagado aceptado", "error": None})
        run.assert_called_once_with(['shutdown', '/s', '/t', '5'], capture_output=True, timeout=10)

    def test_fallo_devuelve_error_estructurado(self):
        with patch("brain.subprocess.run", return_value=NS(returncode=1)):
            brain._gate("apagar_pc", {"accion": "apagar"}, "apágate")
            res = brain._ejecutar("apagar_pc", {"accion": "apagar"}, {}, CONF_P)
        self.assertEqual(res["ok"], False)
        self.assertEqual(res["error"], "fallo_subproceso")


class TestReproducirMusica(Base):
    def test_ok_devuelve_busqueda_iniciada(self):
        skills = {"musica": MagicMock()}
        res = brain._ejecutar("reproducir_musica", {"query": "lofi"}, skills, "pon musica de lofi")
        self.assertTrue(res["ok"])
        self.assertIn("Búsqueda", res["message"])
        skills["musica"].reproducir.assert_called_once_with("lofi")


class TestVolumen(Base):
    def test_sin_control_devuelve_error(self):
        audio = MagicMock()
        audio.get_volumen.return_value = 50  # sentinel de audio.py cuando no hay control real
        skills = {"audio": audio}
        res = brain._ejecutar("volumen_subir", {}, skills, "sube el volumen")
        self.assertEqual(res, {"ok": False, "message": "Control de volumen no disponible", "error": "no_disponible"})


class TestPensarContrato(Base):
    def test_tool_result_contiene_json_valido_con_ok(self):
        with patch.object(brain.cliente.messages, "create", side_effect=[
                resp("tool_use", [tool("hora", "t1")]),
                resp("end_turn", [texto("Son las 10")])]) as create, \
             patch("brain._ejecutar", return_value=brain._ok("Son las 10:00")), \
             patch("builtins.print"):
            brain.pensar("qué hora es")
            tool_result = create.call_args_list[-1].kwargs["messages"][-1]["content"][0]

        cuerpo = json.loads(tool_result["content"])
        self.assertEqual(cuerpo, {"ok": True, "message": "Son las 10:00", "error": None})
        self.assertNotIn("is_error", tool_result)

    def test_tool_result_con_ok_false_tiene_is_error(self):
        with patch.object(brain.cliente.messages, "create", side_effect=[
                resp("tool_use", [tool("borrar_archivo", "t1", {"ruta": "x"})]),
                resp("end_turn", [texto("no se pudo")])]) as create, \
             patch("brain._ejecutar", return_value=brain._err("BLOQUEADO: x", None)), \
             patch("builtins.print"):
            brain.pensar("borra x")
            tool_result = create.call_args_list[-1].kwargs["messages"][-1]["content"][0]

        cuerpo = json.loads(tool_result["content"])
        self.assertEqual(cuerpo["ok"], False)
        self.assertTrue(tool_result["is_error"])


if __name__ == "__main__":
    unittest.main()
