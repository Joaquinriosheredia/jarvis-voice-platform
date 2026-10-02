"""Fase 4: borrar_multiples_emails seguro — msg_ids concretos, firma por conjunto
de IDs, frase propia ("confirmo borrado de correos") y caché con caducidad.

Gmail va mockeado: get_service devuelve un MagicMock, ninguna llamada real.
"""
import contextlib
import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from skills import gmail  # noqa: E402

CONF_M = "confirmo borrado de correos"
CONF_C = "confirmo borrado de correo"
TOOL   = "borrar_multiples_emails"

DAVID = "David Pérez <david@x.com>"
EMAILS = (("m1", DAVID, "Oferta"), ("m2", "Ana <ana@x.com>", "Factura"),
          ("m3", DAVID, "Re: Oferta"), ("m4", DAVID, "Entrevista"))


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, gmail, "cache_emails", gmail.cache_emails)
        gmail.cache_emails = []
        self.service = MagicMock()
        p = patch.object(gmail, "get_service", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.skills = {"gmail": gmail}

    @property
    def trash(self):
        return self.service.users().messages().trash

    def _listar(self, *emails, edad=0):
        gmail.cache_emails = [{"id": i, "remitente": r, "asunto": a, "ts": time.time() - edad}
                              for i, r, a in (emails or EMAILS)]

    def _ej(self, ids, texto):
        with contextlib.redirect_stdout(io.StringIO()):
            return brain._ejecutar(TOOL, {"msg_ids": ids}, self.skills, texto)

    def _bloqueado_sin_armar(self, res):
        self.assertFalse(res["ok"])
        self.assertTrue(res["message"].startswith("BLOQUEADO"), res["message"])
        self.assertIsNone(brain._pendiente)
        self.trash.assert_not_called()


class TestGateBorrarMultiples(Base):
    def test_msg_ids_vacio_bloqueado(self):
        self._listar()
        self._bloqueado_sin_armar(self._ej([], "borra esos correos"))

    def test_algun_id_fuera_de_cache_bloqueado(self):
        self._listar()
        res = self._ej(["m1", "inventado", "m3"], "borra los de David")
        self._bloqueado_sin_armar(res)
        self.assertIn("Lee primero los emails", res["message"])

    def test_cache_caducada_bloqueado(self):
        self._listar(edad=gmail.CACHE_TTL + 1)
        res = self._ej(["m1", "m3"], "borra los de David")
        self._bloqueado_sin_armar(res)
        self.assertIn("desactualizada", res["message"])

    def test_mas_de_10_bloqueado(self):
        self._listar(*[(f"m{i}", DAVID, f"Asunto {i}") for i in range(11)])
        res = self._ej([f"m{i}" for i in range(11)], "borra todos")
        self._bloqueado_sin_armar(res)
        self.assertIn("máximo 10", res["message"])

    def test_ids_repetidos_bloqueado(self):
        self._listar()
        self._bloqueado_sin_armar(self._ej(["m1", "m1", "m3"], "borra los de David"))

    def test_confirmacion_valida_trash_con_cada_id(self):
        self._listar()
        res = self._ej(["m1", "m3", "m4"], "borra los 3 emails de David")
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.assertIn("3 emails de David Pérez", res["message"])
        self.assertNotIn("david@x.com", res["message"])
        self.assertIn(CONF_M, res["message"])
        self.trash.assert_not_called()

        res = self._ej(["m4", "m1", "m3"], CONF_M)           # mismo conjunto, otro orden
        self.assertEqual(res, brain._ok("3 emails eliminados"))
        self.assertEqual(sorted(self.trash.call_args_list, key=lambda c: c.kwargs["id"]),
                         [call(userId="me", id="m1"), call(userId="me", id="m3"), call(userId="me", id="m4")])
        self.assertEqual([e["id"] for e in gmail.cache_emails], ["m2"])
        self.assertIsNone(brain._pendiente)

    def test_confirmar_otro_conjunto_bloqueado(self):
        self._listar()
        self._ej(["m1", "m3"], "borra los de David")
        res = self._ej(["m1", "m3", "m4"], CONF_M)           # el modelo añade uno al confirmar
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.assertIsNone(brain._pendiente)                  # la confirmación se gasta
        self.trash.assert_not_called()

    def test_mensaje_agrupa_varios_remitentes(self):
        self._listar()
        res = self._ej(["m1", "m2", "m3"], "borra esos")
        self.assertIn("3 emails: 2 de David Pérez y 1 de Ana", res["message"])

    def test_frase_plural_activa_el_gate(self):
        self.assertEqual(brain._frase_confirmacion(CONF_M), TOOL)
        self.assertEqual(brain._frase_confirmacion("Jarvis, confirmo borrado de correos."), TOOL)

    def test_frase_singular_no_activa_esta_tool(self):
        self.assertEqual(brain._frase_confirmacion(CONF_C), "borrar_email")
        self._listar()
        self._ej(["m1", "m3"], "borra los de David")
        res = self._ej(["m1", "m3"], CONF_C)
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.assertIsNone(brain._pendiente)                  # la confirmación equivocada gasta el pendiente
        self.trash.assert_not_called()

    def test_firma_ordenada_e_invariante_al_orden(self):
        f = brain._firma(TOOL, {"msg_ids": ["m3", "m1", "m4"]})
        self.assertEqual(f, (TOOL, ("m1", "m3", "m4")))
        self.assertEqual(f, brain._firma(TOOL, {"msg_ids": ["m4", "m3", "m1"]}))
        self.assertNotEqual(f, brain._firma(TOOL, {"msg_ids": ["m1", "m3"]}))

    def test_params_desde_firma_reconstruye(self):
        self._listar()
        self._ej(["m3", "m1"], "borra los de David")
        info = brain.pendiente_info()
        self.assertEqual(info["tool"], TOOL)
        self.assertEqual(info["params"], {"msg_ids": ["m1", "m3"]})
        self.assertEqual(brain._firma(TOOL, info["params"]), info["firma"])


class TestGmailBorrarMultiples(Base):
    def test_valida_todos_antes_de_borrar_ninguno(self):
        self._listar()
        self.assertEqual(gmail.borrar_multiples(["m1", "inventado"]), (False, "Lee primero los emails"))
        self.assertEqual(gmail.borrar_multiples([]), (False, "No hay emails que borrar"))
        self.trash.assert_not_called()
        self.assertEqual(len(gmail.cache_emails), 4)

    def test_cache_caducada_no_borra(self):
        self._listar(edad=gmail.CACHE_TTL + 1)
        ok, msg = gmail.borrar_multiples(["m1", "m3"])
        self.assertFalse(ok)
        self.assertIn("desactualizada", msg)
        self.trash.assert_not_called()

    def test_fallo_parcial_informa_cuales_fallaron(self):
        self._listar()

        def trash(userId, id):
            peticion = MagicMock()
            if id == "m3":
                peticion.execute.side_effect = Exception("HttpError 500")
            return peticion
        self.service.users().messages().trash.side_effect = trash

        ok, msg = gmail.borrar_multiples(["m1", "m3", "m4"])
        self.assertFalse(ok)
        self.assertIn("Eliminados 2 de 3", msg)
        self.assertIn("Re: Oferta", msg)
        self.assertNotIn("Entrevista", msg)
        self.assertEqual([e["id"] for e in gmail.cache_emails], ["m2", "m3"])  # el fallido sigue listado

    def test_fallo_parcial_via_brain_es_err(self):
        self._listar()
        self._ej(["m1", "m3"], "borra los de David")
        self.service.users().messages().trash.side_effect = Exception("sin red")
        res = self._ej(["m1", "m3"], CONF_M)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "borrado_rechazado")
        self.assertIn("No se pudo borrar ningún email", res["message"])


if __name__ == "__main__":
    unittest.main()
