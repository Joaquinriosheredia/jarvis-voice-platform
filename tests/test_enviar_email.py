"""Fase 5D: enviar_email con adjunto — EmailMessage, gate DESTRUCTIVE con firma
(destinatario, adjunto), frase propia y D1. Gmail mockeado: ninguna llamada real.
"""
import atexit
import base64
import contextlib
import email
import io
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from skills import gmail  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

CONF_E = "confirmo envío"
CONF_C = "confirmo borrado de correo"
DEST   = "joaquin@example.com"


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, gmail, "_mi_email", None)
        gmail._mi_email = None
        self.service = MagicMock()
        p = patch.object(gmail, "get_service", return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.skills = {"gmail": gmail}

        # Carpeta temporal como única raíz permitida (la real de Descargas no se toca)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for nombre, valor in (("ALLOWED_ROOTS", [self.dir]), ("BLOCKED_TREES", [r"C:\Windows"]),
                              ("DESTINATARIOS_PERMITIDOS", [DEST])):
            p = patch.object(brain, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        self.cv = os.path.join(self.dir, "CV_Joaquin.pdf")
        with open(self.cv, "wb") as f:
            f.write(b"%PDF-1.4 cv")

    @property
    def send(self):
        return self.service.users().messages().send

    def params(self, **kw):
        return {"destinatario": DEST, "asunto": "Mi CV", "cuerpo": "Adjunto mi CV.",
                "adjunto": self.cv, **kw}

    def _ej(self, params, texto):
        with contextlib.redirect_stdout(io.StringIO()):
            return brain._ejecutar("enviar_email", params, self.skills, texto)

    def enviado(self):
        """Mensaje MIME que se pasó a send()."""
        raw = self.send.call_args.kwargs["body"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))


class TestGmailEnviar(Base):
    def test_inyeccion_en_destinatario(self):
        ok, msg = gmail.enviar_email(f"{DEST}\nBcc: atacante@x.com", "Hola", "cuerpo")
        self.assertFalse(ok)
        self.assertIn("destinatario", msg)
        self.send.assert_not_called()

    def test_inyeccion_en_asunto(self):
        ok, msg = gmail.enviar_email(DEST, "Hola\r\nBcc: atacante@x.com", "cuerpo")
        self.assertFalse(ok)
        self.assertIn("asunto", msg)
        self.send.assert_not_called()

    def test_adjunto_mayor_del_limite(self):
        with patch.object(gmail, "MAX_ADJUNTO", 5):
            ok, msg = gmail.enviar_email(DEST, "Mi CV", "cuerpo", self.cv)
        self.assertEqual((ok, msg), (False, "Error: el adjunto supera 20 MB"))
        self.send.assert_not_called()

    def test_envio_con_adjunto_y_tildes(self):
        ok, msg = gmail.enviar_email(DEST, "Currículum de Joaquín", "Aquí está.", self.cv)
        self.assertEqual((ok, msg), (True, f"Email enviado a {DEST}"))
        m = self.enviado()
        self.assertEqual(str(email.header.make_header(email.header.decode_header(m["Subject"]))),
                         "Currículum de Joaquín")
        adj = [p for p in m.walk() if p.get_filename()]
        self.assertEqual(adj[0].get_filename(), "CV_Joaquin.pdf")
        self.assertEqual(adj[0].get_content_type(), "application/octet-stream")
        self.assertEqual(adj[0].get_payload(decode=True), b"%PDF-1.4 cv")


class TestGetMiEmail(Base):
    def test_devuelve_email_del_perfil_y_cachea(self):
        perfil = self.service.users().getProfile
        perfil.return_value.execute.return_value = {"emailAddress": DEST}
        self.assertEqual(gmail.get_mi_email(), DEST)
        self.assertEqual(gmail.get_mi_email(), DEST)
        self.assertEqual(perfil.return_value.execute.call_count, 1)

    def test_tool_get_mi_email_es_read_sin_confirmacion(self):
        self.service.users().getProfile.return_value.execute.return_value = {"emailAddress": DEST}
        with contextlib.redirect_stdout(io.StringIO()):
            res = brain._ejecutar("get_mi_email", {}, self.skills, "mándamelo a mí")
        self.assertEqual(res, brain._ok(DEST))
        self.assertIsNone(brain._pendiente)


class TestGateEnviar(Base):
    def test_adjunto_fuera_de_allowlist_bloqueado(self):
        res = self._ej(self.params(adjunto=r"C:\Windows\win.ini"), "mándame ese archivo")
        self.assertFalse(res["ok"])
        self.assertIn("BLOQUEADO por seguridad (enviar_email)", res["message"])
        self.assertIsNone(brain._pendiente)          # no arma confirmación
        self.send.assert_not_called()

    def test_adjunto_mayor_del_limite_bloqueado_en_gate(self):
        with patch.object(brain, "MAX_ADJUNTO", 5):
            res = self._ej(self.params(), "mándame el cv")
        self.assertIn("supera 20 MB", res["message"])
        self.assertIsNone(brain._pendiente)
        self.send.assert_not_called()

    def test_primera_llamada_pide_confirmacion_con_destinatario_y_adjunto(self):
        res = self._ej(self.params(), "mándame el cv")
        self.assertFalse(res["ok"])
        self.assertIn(f"Enviar email a {DEST} con asunto Mi CV y adjunto CV_Joaquin.pdf", res["message"])
        self.assertIn(f'"{CONF_E}"', res["message"])
        self.assertEqual(brain._pendiente["firma"], ("enviar_email", DEST, brain._real(self.cv)))
        self.send.assert_not_called()

    def test_confirmacion_valida_envia_con_datos_correctos(self):
        self._ej(self.params(), "mándame el cv")
        res = self._ej(self.params(), CONF_E)
        self.assertEqual(res, brain._ok(f"Email enviado a {DEST}"))
        self.send.assert_called_once()
        m = self.enviado()
        self.assertEqual((m["To"], m["Subject"]), (DEST, "Mi CV"))
        self.assertEqual([p.get_filename() for p in m.walk() if p.get_filename()], ["CV_Joaquin.pdf"])
        self.assertFalse(brain.hay_pendiente())

    def test_confirmo_borrado_de_correo_no_activa_enviar_email(self):
        self._ej(self.params(), "mándame el cv")
        res = self._ej(self.params(), CONF_C)
        self.assertIn("la confirmación no corresponde", res["message"])
        self.send.assert_not_called()

    def test_otro_destinatario_no_reutiliza_la_confirmacion(self):
        # Ambos permitidos: lo que se prueba es que la firma liga la confirmación al destinatario
        brain.DESTINATARIOS_PERMITIDOS.append("otro@x.com")
        self._ej(self.params(), "mándame el cv")
        res = self._ej(self.params(destinatario="otro@x.com"), CONF_E)
        self.assertIn("la confirmación no corresponde", res["message"])
        self.send.assert_not_called()


class TestDestinatariosPermitidos(Base):
    def test_destinatario_externo_bloqueado_por_gate(self):
        res = self._ej(self.params(destinatario="atacante@evil.com"), "mándame el cv")
        self.assertEqual(res["message"], "BLOQUEADO: solo puedes enviar emails a tu propia dirección por ahora.")
        self.assertIsNone(brain._pendiente)          # no arma confirmación
        res = self._ej(self.params(destinatario="atacante@evil.com"), CONF_E)
        self.assertFalse(res["ok"])
        self.send.assert_not_called()

    def test_destinatario_propio_permitido_sin_distinguir_mayusculas(self):
        self._ej(self.params(destinatario=DEST.upper()), "mándame el cv")
        res = self._ej(self.params(destinatario=DEST.upper()), CONF_E)
        self.assertTrue(res["ok"])
        self.send.assert_called_once()

    def test_mensaje_del_gate_incluye_la_direccion_real(self):
        res = self._ej(self.params(), "mándame el cv")
        self.assertIn(f"Enviar email a {DEST}", res["message"])

    def test_lista_vacia_bloquea_todo_envio(self):
        self.service.users().getProfile.return_value.execute.side_effect = Exception("sin red")
        with patch.object(brain, "DESTINATARIOS_PERMITIDOS", []):
            res = self._ej(self.params(), "mándame el cv")
        self.assertIn("solo puedes enviar emails a tu propia dirección", res["message"])
        self.assertIsNone(brain._pendiente)

    def test_carga_perezosa_desde_el_perfil(self):
        self.service.users().getProfile.return_value.execute.return_value = {"emailAddress": "Yo@Gmail.com"}
        with patch.object(brain, "DESTINATARIOS_PERMITIDOS", []):
            res = self._ej(self.params(destinatario="yo@gmail.com"), "mándame el cv")
            self.assertEqual(brain.DESTINATARIOS_PERMITIDOS, ["yo@gmail.com"])
        self.assertIn("confirmo envío", res["message"])   # pasó la allowlist y armó confirmación


class TestAnuncioConfirmacion(Base):
    def _turno(self, params, texto="mándame el cv"):
        """pensar() con un modelo que llama a enviar_email y luego resume a su manera."""
        respuestas = [
            NS(stop_reason="tool_use", usage=None,
               content=[NS(type="tool_use", id="e1", name="enviar_email", input=params)]),
            NS(stop_reason="end_turn", usage=None,
               content=[NS(type="text", text="Te lo envío a tu dirección, ¿confirmas?")]),
        ]
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar(texto, skills=self.skills)

    def test_anuncio_usa_la_direccion_real_no_la_del_modelo(self):
        self._turno(self.params())
        self.assertEqual(brain.anuncio_confirmacion(),
                         f"Vas a enviar un email a {DEST} con el archivo CV_Joaquin.pdf. "
                         f"¿Confirmas con 'confirmo envío'?")

    def test_sin_envio_pendiente_no_hay_anuncio(self):
        self._turno(self.params(destinatario="atacante@evil.com"))  # bloqueado, no arma
        self.assertIsNone(brain.anuncio_confirmacion())

    def test_turno_sin_enviar_email_no_repite_anuncio(self):
        self._turno(self.params())
        with patch.object(brain.cliente.messages, "create",
                          return_value=NS(stop_reason="end_turn", usage=None,
                                          content=[NS(type="text", text="ok")])), \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("espera un momento", skills=self.skills)
        self.assertIsNone(brain.anuncio_confirmacion())


class TestD1Enviar(Base):
    def test_d1_envia_con_asunto_y_cuerpo_originales(self):
        self._ej(self.params(), "mándame el cv")
        out = io.StringIO()
        with patch.object(brain.cliente.messages, "create",
                          return_value=NS(stop_reason="end_turn", usage=None,
                                          content=[NS(type="text", text="Enviado")])), \
             patch.dict(jarvis.SKILLS, {"gmail": gmail}), contextlib.redirect_stdout(out):
            brain.pensar(CONF_E)                     # el modelo no llama a la tool
            self.assertTrue(brain.ultima_traza()["d1_detectado"])
            self.assertIs(jarvis._d1_fix(CONF_E), True)
        self.send.assert_called_once()
        m = self.enviado()
        self.assertEqual((m["To"], m["Subject"]), (DEST, "Mi CV"))
        cuerpo = next(p for p in m.walk() if p.get_content_type() == "text/plain")
        self.assertIn(b"Adjunto mi CV.", cuerpo.get_payload(decode=True))
        self.assertIn("[D1-FIX] ejecutado tool=enviar_email ok=True", out.getvalue())


if __name__ == "__main__":
    unittest.main()
