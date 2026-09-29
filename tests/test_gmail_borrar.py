"""Fase 3: borrar_email seguro — firma por msg_id, frase propia y caché con caducidad.

Gmail va mockeado: get_service devuelve un MagicMock, ninguna llamada real.
"""
import contextlib
import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from skills import gmail  # noqa: E402

CONF_C = "confirmo borrado de correo"
CONF_B = "confirmo borrado"


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
                              for i, r, a in emails]

    def _ej(self, params, texto):
        with contextlib.redirect_stdout(io.StringIO()):
            return brain._ejecutar("borrar_email", params, self.skills, texto)


class TestGmailBorrarEmail(Base):
    def test_cache_vacia_lee_primero(self):
        self.assertEqual(gmail.borrar_email("m1"), (False, "Lee primero los emails"))
        self.trash.assert_not_called()

    def test_cache_caducada_mensaje_desactualizada(self):
        self._listar(("m1", "Ana", "Factura"), edad=301)
        ok, msg = gmail.borrar_email("m1")
        self.assertFalse(ok)
        self.assertIn("desactualizada", msg)
        self.trash.assert_not_called()

    def test_msg_id_fuera_de_cache_lee_primero(self):
        self._listar(("m1", "Ana", "Factura"))
        self.assertEqual(gmail.borrar_email("inventado"), (False, "Lee primero los emails"))
        self.assertFalse(gmail.esta_en_cache("inventado"))
        self.trash.assert_not_called()

    def test_listado_incluye_ids_y_rellena_cache(self):
        msgs = self.service.users().messages()
        msgs.list().execute.return_value = {"messages": [{"id": "m1", "threadId": "t1"}]}
        msgs.get().execute.return_value = {"payload": {"headers": [
            {"name": "From", "value": "Ana <ana@x.com>"}, {"name": "Subject", "value": "Factura"}]}}
        res = gmail.leer_no_leidos()
        self.assertIn("1. Ana: Factura [id=m1]", res)
        e = gmail.info_email("m1")
        self.assertEqual((e["remitente"], e["asunto"]), ("Ana", "Factura"))
        self.assertLess(time.time() - e["ts"], 5)


class TestGateBorrarEmail(Base):
    def test_sin_msg_id_falla_cerrado(self):
        self._listar(("m1", "Ana", "Factura"))
        for params in ({}, {"indice": 0}, {"msg_id": ""}):
            res = self._ej(params, "borra el primer correo")
            self.assertFalse(res["ok"], params)
            self.assertTrue(res["message"].startswith("BLOQUEADO"), params)
        self.assertIsNone(brain._pendiente)
        self.trash.assert_not_called()

    def test_msg_id_fuera_de_cache_no_arma(self):
        self._listar(("m1", "Ana", "Factura"))
        res = self._ej({"msg_id": "inventado"}, "borra ese correo")
        self.assertIn("Lee primero los emails", res["message"])
        self.assertIsNone(brain._pendiente)
        self.trash.assert_not_called()

    def test_confirmacion_valida_trash_con_msg_id(self):
        self._listar(("m1", "Ana", "Factura"), ("m2", "Luis", "Reunión"))
        res = self._ej({"msg_id": "m2"}, "borra el de Luis")
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.assertIn("Luis", res["message"])
        self.assertIn("Reunión", res["message"])
        self.assertIn(CONF_C, res["message"])
        self.trash.assert_not_called()

        res = self._ej({"msg_id": "m2"}, CONF_C)
        self.assertEqual(res, brain._ok("Email de Luis eliminado"))
        self.trash.assert_called_once_with(userId="me", id="m2")
        self.assertFalse(gmail.esta_en_cache("m2"))
        self.assertTrue(gmail.esta_en_cache("m1"))
        self.assertIsNone(brain._pendiente)

    def test_cache_caduca_entre_armado_y_confirmacion(self):
        self._listar(("m1", "Ana", "Factura"))
        self._ej({"msg_id": "m1"}, "borra el de Ana")
        gmail.cache_emails[0]["ts"] -= 301
        res = self._ej({"msg_id": "m1"}, CONF_C)
        self.assertFalse(res["ok"])
        self.assertIn("desactualizada", res["message"])
        self.trash.assert_not_called()

    def test_firma_usa_msg_id_no_indice(self):
        self.assertEqual(brain._firma("borrar_email", {"msg_id": "m2"}), ("borrar_email", "m2"))
        self._listar(("m1", "Ana", "Factura"), ("m2", "Luis", "Reunión"))
        self._ej({"msg_id": "m2"}, "borra el segundo")
        self.assertEqual(brain._pendiente["firma"], ("borrar_email", "m2"))
        self.assertEqual(brain.pendiente_info()["params"], {"msg_id": "m2"})
        # la lista cambia de orden: confirmar otro email en la misma posición no vale
        self._listar(("m2", "Luis", "Reunión"), ("m1", "Ana", "Factura"))
        res = self._ej({"msg_id": "m1"}, CONF_C)
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.trash.assert_not_called()

    def test_frase_de_correo_activa_el_gate(self):
        self.assertEqual(brain._frase_confirmacion(CONF_C), "borrar_email")
        self.assertEqual(brain._frase_confirmacion("Jarvis, confirmo borrado de correo."), "borrar_email")

    def test_confirmo_borrado_no_activa_borrar_email(self):
        self.assertEqual(brain._frase_confirmacion(CONF_B), "borrar_archivo")
        self._listar(("m1", "Ana", "Factura"))
        self._ej({"msg_id": "m1"}, "borra el de Ana")
        res = self._ej({"msg_id": "m1"}, CONF_B)
        self.assertTrue(res["message"].startswith("BLOQUEADO"))
        self.assertIsNone(brain._pendiente)  # la confirmación equivocada gasta el pendiente
        self.trash.assert_not_called()


if __name__ == "__main__":
    unittest.main()
