"""Fase 5B: router en 3 capas (atajo / local / claude). Sin red ni modelo
real: cliente de Anthropic, Ollama y skills mockeados."""
import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import brain  # noqa: E402

FILE_A = r"E:\descargas ryzen\informe.pdf"


def resp(stop, content):
    return NS(stop_reason=stop, content=content, usage=None)


def skills():
    sistema = MagicMock()
    sistema.hora_actual.return_value = "Son las 10:00"
    return {"sistema": sistema}


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_cerrar", False)

    def _armar_pendiente(self):
        with contextlib.redirect_stdout(io.StringIO()):
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        self.assertTrue(brain.hay_pendiente())


class TestClasificar(Base):
    def test_hora_es_atajo(self):
        self.assertEqual(brain._clasificar_peticion("¿Qué hora es?"), "atajo")

    def test_borra_el_archivo_es_claude(self):
        self.assertEqual(brain._clasificar_peticion("borra el archivo"), "claude")

    def test_que_es_una_api_es_local(self):
        self.assertEqual(brain._clasificar_peticion("qué es una API"), "local")

    def test_confirmo_borrado_con_pendiente_es_claude(self):
        self._armar_pendiente()
        self.assertEqual(brain._clasificar_peticion("confirmo borrado"), "claude")

    def test_atajo_con_pendiente_es_claude(self):
        self._armar_pendiente()
        self.assertEqual(brain._clasificar_peticion("qué hora es"), "claude")

    def test_falsos_positivos_de_palabra_clave_van_a_claude(self):
        for txt in ("cierra el navegador", "ahora borra el informe", "a qué hora sale el tren",
                    "qué tengo en el correo",
                    "pon música y sube el volumen", "el segundo", "quién eres"):
            self.assertEqual(brain._clasificar_peticion(txt), "claude", txt)

    def test_tiempo_no_meteorologico_no_es_atajo(self):
        self.assertEqual(brain._clasificar_peticion("cuánto tiempo tarda un huevo"), "local")


class TestPensarRouter(Base):
    def test_atajo_ejecuta_tool_sin_llm(self):
        with patch.object(brain.cliente.messages, "create") as create, \
             patch("brain.requests.post") as post, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            r = brain.pensar("qué hora es", skills=skills())
        self.assertEqual(r, "Son las 10:00")
        create.assert_not_called()
        post.assert_not_called()
        self.assertIn("[ROUTER] atajo=hora", out.getvalue())
        self.assertEqual(brain.ultima_traza()["tools_ejecutadas"][0]["nombre"], "hora")

    def test_local_responde_con_ollama(self):
        ok = MagicMock()
        ok.json.return_value = {"response": "Una interfaz entre programas."}
        with patch.object(brain.cliente.messages, "create") as create, \
             patch("brain.requests.post", return_value=ok) as post, \
             contextlib.redirect_stdout(io.StringIO()):
            r = brain.pensar("qué es una API", skills=skills())
        self.assertEqual(r, "Una interfaz entre programas.")
        create.assert_not_called()
        self.assertEqual(post.call_args.kwargs["timeout"], brain.OLLAMA_TIMEOUT)

    def test_ollama_caido_pasa_a_claude(self):
        with patch.object(brain.cliente.messages, "create",
                          return_value=resp("end_turn", [NS(type="text", text="Respuesta de Claude")])) as create, \
             patch("brain.requests.post", side_effect=brain.requests.ConnectionError("down")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            r = brain.pensar("qué es una API", skills=skills())
        self.assertEqual(r, "Respuesta de Claude")
        create.assert_called_once()
        self.assertIn("[ROUTER] claude", out.getvalue())

    def test_atajo_con_pendiente_pasa_a_claude(self):
        self._armar_pendiente()
        sk = skills()
        with patch.object(brain.cliente.messages, "create",
                          return_value=resp("end_turn", [NS(type="text", text="ok")])) as create, \
             patch("brain.requests.post") as post, \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("qué hora es", skills=sk)
        create.assert_called_once()
        post.assert_not_called()
        sk["sistema"].hora_actual.assert_not_called()

    def test_sin_skills_no_enruta(self):
        with patch.object(brain.cliente.messages, "create",
                          return_value=resp("end_turn", [NS(type="text", text="ok")])) as create, \
             patch("brain.requests.post") as post, \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("qué hora es")
        create.assert_called_once()
        post.assert_not_called()


class TestDev(Base):
    def test_nullpointer_es_dev(self):
        self.assertEqual(brain._clasificar_peticion("tengo un NullPointerException"), "dev")

    def test_que_hace_esta_funcion_es_dev(self):
        self.assertEqual(brain._clasificar_peticion("qué hace esta función"), "dev")

    def test_error_404_es_dev(self):
        self.assertEqual(brain._clasificar_peticion("error 404 en mi API"), "dev")

    def test_que_hora_es_no_es_dev(self):
        self.assertNotEqual(brain._clasificar_peticion("qué hora es"), "dev")

    def test_borra_el_archivo_no_es_dev(self):
        self.assertNotEqual(brain._clasificar_peticion("borra el archivo"), "dev")

    def test_keyword_debil_sola_no_es_dev(self):
        for txt in ("qué clase de vino va con pescado", "cuánto son 500 euros en dólares",
                    "la línea 3 del metro"):
            self.assertNotEqual(brain._clasificar_peticion(txt), "dev", txt)

    def test_dos_debiles_o_debil_con_inicio_tecnico_es_dev(self):
        self.assertEqual(brain._clasificar_peticion("en qué línea está el error"), "dev")
        self.assertEqual(brain._clasificar_peticion("por qué este método devuelve vacío"), "dev")

    def test_keyword_fuerte_sola_es_dev(self):
        for txt in ("me sale un traceback", "por qué falla mi consumer de Kafka", "tengo un bug raro"):
            self.assertEqual(brain._clasificar_peticion(txt), "dev", txt)

    def test_accion_con_palabra_dev_no_es_dev(self):
        self.assertEqual(brain._clasificar_peticion("borra el log de errores"), "claude")

    def test_explicame_algo_tecnico_es_dev_y_que_es_una_api_sigue_local(self):
        self.assertEqual(brain._clasificar_peticion("explícame cómo funciona Kafka"), "dev")
        self.assertEqual(brain._clasificar_peticion("qué es una API"), "local")

    def test_dev_va_a_claude_con_system_dev_y_no_a_ollama(self):
        with patch.object(brain.cliente.messages, "create",
                          return_value=resp("end_turn", [NS(type="text", text="ok")])) as create, \
             patch("brain.requests.post") as post, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            brain.pensar("tengo un NullPointerException", skills=skills())
        post.assert_not_called()
        self.assertIn(brain.SYSTEM_DEV, create.call_args.kwargs["system"])
        self.assertIn("[ROUTER] dev", out.getvalue())

    def test_no_dev_usa_system_normal(self):
        with patch.object(brain.cliente.messages, "create",
                          return_value=resp("end_turn", [NS(type="text", text="ok")])) as create, \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("borra el archivo", skills=skills())
        self.assertEqual(create.call_args.kwargs["system"], brain.SYSTEM)


class TestLimpiarLocal(unittest.TestCase):
    def test_corta_en_la_ultima_frase_completa(self):
        self.assertEqual(brain._limpiar_local("Una API conecta programas. Se usa mucho en"),
                         "Una API conecta programas.")

    def test_descarta_caracteres_no_latinos(self):
        self.assertIsNone(brain._limpiar_local("Una API es un conjunto 交互模式 de reglas."))

    def test_conserva_espanol_y_descarta_sin_frase_completa(self):
        self.assertEqual(brain._limpiar_local("¿Sabías que el ñu corre?"), "¿Sabías que el ñu corre?")
        self.assertIsNone(brain._limpiar_local("Una frase sin terminar"))


if __name__ == "__main__":
    unittest.main()
