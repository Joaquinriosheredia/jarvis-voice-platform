"""Las frases que dicen cómo configurar o reautorizar un servicio llegan al usuario tal cual.

Si una tool devuelve un aviso de «no configurado» (Gmail, Calendar, Contactos, GitHub, tiempo),
el sistema lo dice literalmente, como los anuncios de confirmación: si es lo único de la vuelta,
sin volver a llamar al modelo; si hay más (p. ej. agenda sin configurar y tareas), el modelo solo
responde a lo demás y lo que repita del aviso se quita. Claude mockeado: ninguna llamada real.
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import brain  # noqa: E402
from skills import github, gmail, google_auth as ga, sistema  # noqa: E402


def resp(stop, content):
    return NS(stop_reason=stop, content=content, usage=None)


def tool(name, id="t1", input=None):
    return NS(type="tool_use", name=name, id=id, input=input or {})


def texto(t):
    return NS(type="text", text=t)


AVISOS = {   # servicio: (tool, mensaje que devuelve la tool sin configurar, comando exacto)
    "gmail":     ("leer_emails", ga.frase_no_disponible("gmail", ga.SIN_CREDENCIALES), "reautorizar.py gmail"),
    "calendar":  ("ver_agenda", ga.frase_no_disponible("calendar", ga.SIN_TOKEN), "reautorizar.py calendar"),
    "contactos": ("buscar_contacto", ga.frase_no_disponible("contacts", ga.SIN_TOKEN), "reautorizar.py contacts"),
    "github":    ("github_estado", github.MENSAJES[github.SIN_TOKEN], "GITHUB_TOKEN"),
    "tiempo":    ("tiempo", sistema.TIEMPO_NO_CONFIGURADO, "configurar.py"),
}


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        for p in (patch.dict(os.environ, {"JARVIS_STREAMING": "0"}), patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def pensar(self, respuestas, resultados):
        """brain.pensar con Claude y las tools mockeados. resultados: {tool: dict de _ejecutar}."""
        with patch.object(brain.cliente.messages, "create", side_effect=list(respuestas)) as create, \
             patch("brain._ejecutar", side_effect=lambda nombre, *a, **k: resultados[nombre]):
            final = brain.pensar("petición de prueba")
        self.create = create
        return final


class TestSoloAviso(Base):
    def test_cada_servicio_literal_y_sin_otra_llamada(self):
        for servicio, (nombre, mensaje, comando) in AVISOS.items():
            with self.subTest(servicio):
                final = self.pensar([resp("tool_use", [tool(nombre)])],
                                    {nombre: brain._ok(mensaje)})
                self.assertEqual(final, mensaje[0].upper() + mensaje[1:])
                self.assertIn(comando, final)
                self.assertEqual(self.create.call_count, 1)   # el modelo no llega a parafrasear
                self.assertEqual(brain.ultima_traza()["stop_final"], "aviso_literal")

    def test_aviso_dentro_de_un_mensaje_mas_largo(self):
        mensaje = "Error leyendo emails: " + gmail.FALLOS["autenticacion"]
        final = self.pensar([resp("tool_use", [tool("leer_emails")])], {"leer_emails": brain._ok(mensaje)})
        self.assertTrue(final.startswith("La autorización de Gmail ha caducado"))
        self.assertIn("reautorizar.py gmail", final)

    def test_dos_avisos_iguales_se_dicen_una_vez(self):
        _, mensaje, _ = AVISOS["gmail"]
        final = self.pensar([resp("tool_use", [tool("leer_emails", "a"), tool("leer_emails", "b")])],
                            {"leer_emails": brain._ok(mensaje)})
        self.assertEqual(final.count("reautorizar.py gmail"), 1)


class TestTurnoMixto(Base):
    def test_el_modelo_responde_al_resto_y_no_repite_el_aviso(self):
        _, aviso, _ = AVISOS["calendar"]
        final = self.pensar(
            [resp("tool_use", [tool("ver_agenda", "a"), tool("listar_tareas", "b")]),
             resp("end_turn", [texto("No tengo acceso al calendario, falta configurarlo con reautorizar. "
                                     "Tienes 2 tareas pendientes.")])],
            {"ver_agenda": brain._err(aviso, None), "listar_tareas": brain._ok("2 tareas pendientes")})
        self.assertEqual(final, aviso + " Tienes 2 tareas pendientes.")
        # Al modelo le llega que el aviso ya está dicho, no el texto del aviso
        tool_results = self.create.call_args_list[-1].kwargs["messages"][-1]["content"]
        agenda = json.loads(tool_results[0]["content"])
        self.assertEqual(agenda["message"], brain.INSTRUCCION_AVISO)
        self.assertNotIn("reautorizar.py", tool_results[0]["content"])
        self.assertEqual(json.loads(tool_results[1]["content"])["message"], "2 tareas pendientes")

    def test_repeticiones_con_otras_palabras_se_quitan(self):
        _, aviso, _ = AVISOS["calendar"]
        final = self.pensar(
            [resp("tool_use", [tool("ver_agenda", "a"), tool("listar_tareas", "b")]),
             resp("end_turn", [texto("No tengo acceso a tu agenda en este momento. El sistema necesita configurar "
                                     "el acceso a Google Calendar. No tienes tareas pendientes.")])],
            {"ver_agenda": brain._err(aviso, None), "listar_tareas": brain._ok("No tienes tareas pendientes")})
        self.assertEqual(final, aviso + " No tienes tareas pendientes.")

    def test_lo_que_diga_del_servicio_sin_configurar_se_quita(self):
        _, aviso, _ = AVISOS["calendar"]
        final = self.pensar(
            [resp("tool_use", [tool("ver_agenda", "a"), tool("listar_tareas", "b")]),
             resp("end_turn", [texto("No tienes eventos en la agenda hoy. Tienes 1 tarea pendiente. OK")])],
            {"ver_agenda": brain._err(aviso, None), "listar_tareas": brain._ok("1 tarea pendiente")})
        self.assertEqual(final, aviso + " Tienes 1 tarea pendiente.")

    def test_ok_del_modelo_no_se_dice(self):
        _, aviso, _ = AVISOS["github"]
        final = self.pensar([resp("tool_use", [tool("github_estado", "a"), tool("hora", "b")]),
                             resp("end_turn", [texto("OK")])],
                            {"github_estado": brain._ok(aviso), "hora": brain._ok("Son las 10:00")})
        self.assertEqual(final, aviso)


class TestSinAvisos(Base):
    def test_respuesta_normal_intacta(self):
        final = self.pensar([resp("tool_use", [tool("hora")]), resp("end_turn", [texto("Son las 10.")])],
                            {"hora": brain._ok("Son las 10:00")})
        self.assertEqual(final, "Son las 10.")
        self.assertNotIn("avisos_literales", brain.ultima_traza())

    def test_un_error_cualquiera_no_es_un_aviso(self):
        self.assertIsNone(brain.aviso_literal("No se pudo leer el calendario: RuntimeError"))
        self.assertIsNone(brain.aviso_literal(None))

    def test_todas_las_frases_llevan_un_comando_o_variable(self):
        for frase in brain.frases_aviso():
            self.assertRegex(frase, r"reautorizar\.py \w+|configurar\.py|GITHUB_TOKEN|JARVIS_GITHUB_REPOS", frase)


if __name__ == "__main__":
    unittest.main()
