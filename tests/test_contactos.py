"""Contactos de Google (People, solo lectura): tool READ buscar_contacto y envío a un contacto.

El destinatario se resuelve en el sistema a partir del nombre que el usuario dice por el
micrófono en ese turno, nunca desde un resultado de tool; si hay varios, se pregunta cuál.
El anuncio dice nombre y dirección. Los contactos no van a la memoria ni a los logs."""
import base64
import contextlib
import email
import io
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
import doctor  # noqa: E402
import memoria_sesiones  # noqa: E402
import reautorizar  # noqa: E402
import tts_elevenlabs  # noqa: E402
from skills import contactos, direccion, gmail, google_auth  # noqa: E402

CONF_E = "confirmo envío"
PROPIA = "joaquin@example.com"
ANA_G  = {"nombre": "Ana García", "email": "ana.garcia@example.org", "tipo": "work"}
ANA_L  = {"nombre": "Ana López", "email": "ana.lopez@example.net", "tipo": "home"}
PARAMS = {"asunto": "Informe", "cuerpo": "Te paso el informe."}


def persona(nombre, *emails):
    return {"person": {"names": [{"displayName": nombre}],
                       "emailAddresses": [{"value": e, "type": t} for e, t in emails]}}


class Base(unittest.TestCase):
    def setUp(self):
        for nombre in ("_pendiente", "_dictada", "_contacto"):
            setattr(brain, nombre, None)
            self.addCleanup(setattr, brain, nombre, None)
        self.addCleanup(direccion._nombres_ocultos.clear)
        gmail._mi_email = None
        self.addCleanup(setattr, gmail, "_mi_email", None)
        for p in (patch.object(brain, "DESTINATARIOS_PERMITIDOS", []),):
            p.start()
            self.addCleanup(p.stop)
        self.service = MagicMock()
        self.service.users().getProfile.return_value.execute.return_value = {"emailAddress": PROPIA}
        p = patch.object(gmail, "get_service", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.skills = {"gmail": gmail}
        self.contactos = [ANA_G]
        p = patch.object(contactos, "buscar", side_effect=lambda nombre: (True, [
            c for c in self.contactos if all(w in brain._normalizar(c["nombre"]).split()
                                             for w in brain._normalizar(nombre).split())]))
        self.buscar = p.start()
        self.addCleanup(p.stop)

    @property
    def send(self):
        return self.service.users().messages().send

    def enviado_a(self):
        self.send.assert_called_once()
        raw = self.send.call_args.kwargs["body"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))["To"]

    def _ej(self, params, texto, nombre="enviar_email"):
        with contextlib.redirect_stdout(io.StringIO()):
            return brain._ejecutar(nombre, params, self.skills, texto)

    def _turno(self, texto, *llamadas):
        respuestas = [NS(stop_reason="tool_use", usage=None,
                         content=[NS(type="tool_use", id=f"t{i}", name=n, input=dict(p))])
                      for i, (n, p) in enumerate(llamadas)]
        respuestas.append(NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="Hecho.")]))
        salida = io.StringIO()
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             patch.object(brain, "_enrutar", return_value=None), contextlib.redirect_stdout(salida):
            brain.pensar(texto, skills=self.skills)
        self.consola = salida.getvalue()
        return brain.anuncio_confirmacion()


class TestSkill(unittest.TestCase):
    def setUp(self):
        p = patch.object(contactos, "_calentado", False)
        p.start()
        self.addCleanup(p.stop)

    def _con(self, resultados):
        servicio = MagicMock()
        servicio.people().searchContacts.return_value.execute.return_value = {"results": resultados}
        return patch.object(contactos, "_servicio", return_value=servicio), servicio

    def test_una_entrada_por_direccion_valida_y_sin_repetidos(self):
        p, servicio = self._con([persona("Ana García", ("Ana.Garcia@Example.org", "work"), ("no-valida", "home")),
                                 persona("Ana López", ("ana.lopez@example.net", "home"),
                                         ("ana.garcia@example.org", "other"))])
        with p:
            ok, r = contactos.buscar("  Ana  ")
        self.assertTrue(ok)
        self.assertEqual(r, [{"nombre": "Ana García", "email": "ana.garcia@example.org", "tipo": "work"},
                             {"nombre": "Ana López", "email": "ana.lopez@example.net", "tipo": "home"}])
        consultas = [c.kwargs.get("query") for c in servicio.people().searchContacts.call_args_list]
        self.assertEqual(consultas, ["", "Ana"])   # calentamiento y búsqueda

    def test_fallo_sin_texto_de_la_excepcion(self):
        with patch.object(contactos, "_servicio", side_effect=RuntimeError("token=abc ana@x.com")):
            ok, msg = contactos.buscar("Ana")
        self.assertFalse(ok)
        self.assertEqual(msg, "No se pudo consultar Google Contactos (RuntimeError).")

    def test_sin_nombre(self):
        self.assertEqual(contactos.buscar("  "), (False, "Falta el nombre del contacto."))

    def test_solo_lectura(self):
        self.assertEqual(contactos.SCOPES_CONTACTOS, ["https://www.googleapis.com/auth/contacts.readonly"])
        self.assertTrue(contactos.TOKEN_CONTACTOS.startswith("C:\\Jarvis-secrets\\"))

    def test_etiqueta(self):
        self.assertEqual(contactos.etiqueta(ANA_G), "Ana García (trabajo)")
        self.assertEqual(contactos.etiqueta(dict(ANA_G, tipo="")), "Ana García")


class TestBuscarContacto(Base):
    def test_es_read_privada_y_devuelve_nombre_y_direccion(self):
        self.assertEqual(brain.TOOL_RISK["buscar_contacto"], "READ")
        tts_elevenlabs.nuevo_turno()
        res = self._ej({"nombre": "Ana"}, "cuál es el correo de Ana", "buscar_contacto")
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "Contactos: Ana García (trabajo): ana.garcia@example.org")
        self.assertTrue(tts_elevenlabs.turno_privado())

    def test_los_nombres_no_salen_en_la_consola_ni_en_disco(self):
        self._turno("cuál es el correo de Ana García", ("buscar_contacto", {"nombre": "Ana García"}))
        self.assertNotIn("Ana García", self.consola)
        self.assertNotIn("ana.garcia@example.org", self.consola)
        self.assertIn("<contacto>", self.consola)
        self.assertEqual(direccion.redactar("escribe a Ana García"), "escribe a <contacto>")

    def test_no_entra_en_la_memoria_entre_sesiones(self):
        self.assertIn("buscar_contacto", memoria_sesiones.TOOLS_SIN_CONTENIDO)


class TestEnviarAContacto(Base):
    def test_nombre_dictado_resuelve_anuncia_nombre_y_direccion_y_envia_tras_confirmar(self):
        anuncio = self._turno("mándale el informe a Ana García",
                              ("enviar_email", dict(PARAMS, destinatario="contacto: Ana García")))
        self.assertTrue(anuncio.startswith("Vas a enviar un email a tu contacto Ana García, "
                                           "ana.garcia@example.org. Asunto: «Informe»."), anuncio)
        self.send.assert_not_called()
        # Al confirmar, el modelo repite la llamada con el nombre (que ya no está en el micro)
        self._turno(CONF_E, ("enviar_email", dict(PARAMS, destinatario="contacto: Ana García")))
        self.assertEqual(self.enviado_a(), ANA_G["email"])

    def test_d1_envia_al_contacto_anunciado(self):
        self._turno("mándale el informe a Ana García",
                    ("enviar_email", dict(PARAMS, destinatario="contacto: Ana García")))
        res = self._ej(brain.pendiente_info()["params"], CONF_E)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.enviado_a(), ANA_G["email"])

    def test_nombre_que_no_dijo_el_micro_no_se_resuelve(self):
        res = self._ej(dict(PARAMS, destinatario="contacto: Ana García"), "lee mi último correo")
        self.assertEqual(res["error"], "contacto_no_dictado")
        self.buscar.assert_not_called()
        self.assertIsNone(brain._pendiente)

    def test_direccion_de_un_resultado_de_tool_no_vale(self):
        self._ej({"nombre": "Ana"}, "busca a Ana", "buscar_contacto")
        res = self._ej(dict(PARAMS, destinatario=ANA_G["email"]), "mándaselo")
        self.assertIn("solo puedes enviar", res["message"])
        self.assertIsNone(brain._pendiente)

    def test_varios_pregunta_cual_sin_elegir_ni_decir_direcciones(self):
        self.contactos = [ANA_G, ANA_L]
        res = self._ej(dict(PARAMS, destinatario="contacto: Ana"), "mándaselo a Ana")
        self.assertEqual(res["error"], "contacto_ambiguo")
        self.assertIn("Ana García (trabajo) y Ana López (personal)", res["message"])
        self.assertIn("sin elegir tú", res["message"])
        self.assertNotIn("@", res["message"])
        self.assertIsNone(brain._pendiente)
        # El usuario dice cuál: ahora sí se resuelve
        anuncio = self._turno("a Ana López", ("enviar_email", dict(PARAMS, destinatario="contacto: Ana López")))
        self.assertIn("tu contacto Ana López, ana.lopez@example.net", anuncio)

    def test_sin_coincidencias(self):
        res = self._ej(dict(PARAMS, destinatario="contacto: Pedro"), "mándaselo a Pedro")
        self.assertEqual(res["error"], "contacto_no_encontrado")
        self.assertIn("No encuentro a Pedro", res["message"])

    def test_contactos_no_disponible(self):
        self.buscar.side_effect = lambda n: (False, "No se pudo consultar Google Contactos (RefreshError).")
        res = self._ej(dict(PARAMS, destinatario="contacto: Ana García"), "mándaselo a Ana García")
        self.assertEqual(res["error"], "contactos_no_disponible")

    def test_el_contacto_resuelto_caduca(self):
        self._ej(dict(PARAMS, destinatario="contacto: Ana García"), "mándaselo a Ana García")
        brain._pendiente = None
        brain._contacto["ts"] -= brain.DICTADA_TTL + 1
        res = self._ej(dict(PARAMS, destinatario="contacto: Ana García"), "vale")
        self.assertEqual(res["error"], "contacto_no_dictado")


class TestAutorizacion(unittest.TestCase):
    def test_servicio_contacts_con_su_token(self):
        nombre, token, scopes = google_auth.servicios()["contacts"]
        self.assertEqual((nombre, token, scopes),
                         ("Contactos", contactos.TOKEN_CONTACTOS, contactos.SCOPES_CONTACTOS))

    def test_sin_token_no_se_avisa_al_arrancar_pero_roto_si(self):
        sin = {"contacts": google_auth._resultado("contacts", google_auth.SIN_TOKEN)}
        self.assertIsNone(google_auth.aviso_arranque(sin))
        roto = {"contacts": google_auth._resultado("contacts", google_auth.REVOCADO)}
        voz, _ = google_auth.aviso_arranque(roto)
        self.assertIn("Contactos", voz)
        self.assertIn("buscar tus contactos", voz)

    def test_reautorizar_contacts_muestra_el_aviso_del_permiso_nuevo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cred = os.path.join(tmp, "credentials.json")
            open(cred, "w").close()
            creds = NS(refresh_token="r", to_json=lambda: "{}")
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                r = reautorizar.reautorizar("contacts", os.path.join(tmp, "token_contacts.json"),
                                            contactos.SCOPES_CONTACTOS, cred, flujo=lambda c, s: creds,
                                            comprobar=lambda *a: google_auth._resultado("contacts", google_auth.VALIDO))
        self.assertTrue(r["ok"])
        self.assertIn("Ver tus contactos", salida.getvalue())
        self.assertIn("People API", salida.getvalue())
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                reautorizar.main(["contactos"])

    def test_doctor(self):
        with patch.object(doctor.os.path, "isfile", return_value=False):
            r = doctor.check_token_contactos()
            self.assertEqual(r["estado"], doctor.OK)
            self.assertIn("opcional", r["detalle"])
            self.assertEqual(doctor.check_live_token_contactos()["estado"], doctor.OK)
        self.assertIn("token_contactos", [c[0] for c in doctor.COMPROBACIONES])
        self.assertIn("live_token_contactos", [c[0] for c in doctor.COMPROBACIONES_LIVE])


if __name__ == "__main__":
    unittest.main()
