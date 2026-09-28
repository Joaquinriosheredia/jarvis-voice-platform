"""Fase 2A: traza de ejecución de pensar(). Solo observabilidad: nada de estos
tests debe depender de que el modelo real responda (cliente mockeado)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import brain  # noqa: E402

FILE_A = r"E:\descargas ryzen\informe.pdf"
CONF_B = "confirmo borrado"


def resp(stop, content, usage=None):
    return NS(stop_reason=stop, content=content, usage=usage)


def tool(name="hora", id="t1", input=None):
    return NS(type="tool_use", name=name, id=id, input=input if input is not None else {})


def texto(t="ok"):
    return NS(type="text", text=t)


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        brain._ultima_traza = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_ultima_traza", None)

    def correr(self, respuestas, ejecutar="Son las 10:00", **kw):
        """Ejecuta pensar() con cliente y _ejecutar mockeados; devuelve (respuesta, stdout)."""
        out = io.StringIO()
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas) as create, \
             patch("brain._ejecutar", return_value=brain._ok(ejecutar)), \
             contextlib.redirect_stdout(out):
            r = brain.pensar(kw.pop("txt", "qué hora es"), **kw)
        self.create = create
        return r, out.getvalue()


class TestTrazaBasica(Base):
    def test_ultima_traza_es_none_antes_de_la_primera_llamada(self):
        # Import limpio en un proceso nuevo: el valor por defecto real, sin estado de otros tests
        env = dict(os.environ, ANTHROPIC_API_KEY="test-key-no-real")
        r = subprocess.run([sys.executable, "-c", "import brain; print(brain.ultima_traza())"],
                           cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "None", r.stderr)
        self.assertIsNone(brain.ultima_traza())

    def test_pensar_con_tool_registra_end_turn_y_la_tool(self):
        r, _ = self.correr([
            resp("tool_use", [tool("hora", "abc", {"x": 1})], NS(input_tokens=10, output_tokens=5)),
            resp("end_turn", [texto("Son las 10")]),
        ])
        self.assertEqual(r, "Son las 10")
        t = brain.ultima_traza()
        self.assertEqual(t["stop_final"], "end_turn")
        self.assertEqual(t["respuesta"], "Son las 10")
        self.assertEqual(t["texto"], "qué hora es")
        self.assertEqual(len(t["tools_ejecutadas"]), 1)
        reg = t["tools_ejecutadas"][0]
        self.assertEqual((reg["i"], reg["tool_use_id"], reg["stop_reason"], reg["nombre"], reg["input"], reg["resultado"]),
                         (0, "abc", "tool_use", "hora", {"x": 1}, "Son las 10:00"))
        self.assertIsInstance(reg["ms"], int)
        self.assertEqual([(i["i"], i["stop_reason"], i["n_tool_use"]) for i in t["iteraciones"]],
                         [(0, "tool_use", 1), (1, "end_turn", 0)])
        self.assertEqual((t["iteraciones"][0]["tokens_in"], t["iteraciones"][0]["tokens_out"]), (10, 5))
        self.assertIsNone(t["iteraciones"][1]["tokens_in"])  # sin usage: None, no rompe

    def test_pensar_sin_tool_deja_tools_ejecutadas_vacio(self):
        r, _ = self.correr([resp("end_turn", [texto("Hola")])])
        t = brain.ultima_traza()
        self.assertEqual(r, "Hola")
        self.assertEqual(t["tools_ejecutadas"], [])
        self.assertEqual(t["stop_final"], "end_turn")

    def test_stop_final_api_error_max_iter_y_otro(self):
        r, _ = self.correr([RuntimeError("caída")])
        self.assertEqual(r, "No puedo responder ahora mismo.")
        self.assertEqual(brain.ultima_traza()["stop_final"], "api_error")

        r, _ = self.correr([resp("tool_use", [tool()]) for _ in range(20)])
        t = brain.ultima_traza()
        self.assertEqual(r, "No he podido completar la tarea.")
        self.assertEqual((t["stop_final"], len(t["iteraciones"]), len(t["tools_ejecutadas"])), ("max_iter", 20, 20))

        r, _ = self.correr([resp("max_tokens", [texto("cortado")])])
        self.assertEqual(r, "No he podido completar la tarea.")
        self.assertEqual(brain.ultima_traza()["stop_final"], "max_tokens")


class TestPendienteAlInicio(Base):
    def test_refleja_hay_pendiente_al_empezar_el_turno(self):
        self.correr([resp("end_turn", [texto()])])
        self.assertIs(brain.ultima_traza()["pendiente_al_inicio"], False)

        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")   # arma el pendiente
        self.assertTrue(brain.hay_pendiente())
        self.correr([resp("end_turn", [texto()])])
        self.assertIs(brain.ultima_traza()["pendiente_al_inicio"], True)

    def test_es_el_valor_de_antes_de_ejecutar_aunque_la_tool_lo_consuma(self):
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        out = io.StringIO()
        with patch.object(brain.cliente.messages, "create", side_effect=[
                resp("tool_use", [tool("borrar_archivo", "d1", {"ruta": FILE_A})]),
                resp("end_turn", [texto("Hecho")])]), \
             patch("brain.os.path.exists", return_value=True), \
             patch("brain.os.path.isdir", return_value=False), \
             patch("brain.os.remove") as rm, contextlib.redirect_stdout(out):
            brain.pensar(CONF_B)
        rm.assert_called_once_with(FILE_A)
        t = brain.ultima_traza()
        self.assertIs(t["pendiente_al_inicio"], True)     # había pendiente al empezar…
        self.assertFalse(brain.hay_pendiente())            # …y la ejecución lo consumió
        self.assertEqual(t["tools_ejecutadas"][0]["nombre"], "borrar_archivo")


class TestLog(Base):
    def test_log_se_genera_sin_excepcion(self):
        largo = "x" * 500
        _, out = self.correr([
            resp("tool_use", [tool("hora", "t", {"q": largo})]),
            resp("end_turn", [texto("Son las 10")]),
        ], ejecutar=largo)
        lineas = [l for l in out.splitlines() if l.startswith("[TRAZA")]
        self.assertRegex(lineas[0], r"^\[TRAZA\] iters=2 stop=end_turn tools=\[hora\] resp_len=10 t=\d+\.\ds$")
        self.assertTrue(lineas[1].startswith("[TRAZA+] i=0 tool=hora ms="))
        self.assertNotIn("x" * 201, lineas[1])             # input y resultado truncados a 200
        self.assertIn("x" * 200 + "...", lineas[1])

    def test_log_traza_no_lanza_con_traza_corrupta(self):
        for corrupta in ({}, {"tools_ejecutadas": None}, None):
            brain._log_traza(corrupta)                     # no debe lanzar

    def test_log_tolera_consola_cp1252(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with patch.object(brain.cliente.messages, "create", side_effect=[
                resp("tool_use", [tool("nota", "n", {"texto": "日本語 ñ"})]), resp("end_turn", [texto("ok")])]), \
             patch("brain._ejecutar", return_value=brain._ok("Nota guardada: 日本語")), \
             contextlib.redirect_stdout(buf):
            brain.pensar("anota algo")                     # no debe lanzar UnicodeEncodeError
        self.assertEqual(brain.ultima_traza()["stop_final"], "end_turn")


class TestNoSeFiltra(Base):
    def test_la_traza_no_va_al_contexto_del_modelo(self):
        self.correr([resp("tool_use", [tool()]), resp("end_turn", [texto("ok")])])
        enviado = json.dumps(self.create.call_args_list[-1].kwargs["messages"], default=str)
        for marca in ("iteraciones", "tools_ejecutadas", "pendiente_al_inicio", "[TRAZA"):
            self.assertNotIn(marca, enviado)

    def test_pensar_no_toca_memoria_json(self):
        ruta = r"C:\Jarvis\memoria.json"
        antes = os.path.getmtime(ruta) if os.path.exists(ruta) else None
        self.correr([resp("end_turn", [texto("ok")])])
        despues = os.path.getmtime(ruta) if os.path.exists(ruta) else None
        self.assertEqual(antes, despues)


if __name__ == "__main__":
    unittest.main()
