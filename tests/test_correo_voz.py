"""Enviar un correo por voz: «mi propia dirección» se resuelve desde el perfil de Gmail,
los fallos dicen su causa real, las direcciones dictadas se validan y se deletrean, y el
destinatario anunciado es el que se envía. Gmail mockeado: ninguna llamada real.
"""
import atexit
import base64
import contextlib
import email
import io
import os
import re
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
import tts_elevenlabs  # noqa: E402
from google.auth.exceptions import RefreshError, TransportError  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402
from skills import direccion, gmail  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

CONF_E  = "confirmo envío"
PROPIA  = "ana@example.com"
PEDIDO  = "envíame un correo a mi propia dirección con el asunto prueba de JARVIS y el texto hola, esto es una prueba"
PARAMS  = {"destinatario": "yo", "asunto": "prueba de JARVIS", "cuerpo": "hola, esto es una prueba"}
DICTADA = "pepe.garcia@correo.es"


def http_error(estado):
    return HttpError(NS(status=estado, reason="x"), b"{}")


class Base(unittest.TestCase):
    def setUp(self):
        for nombre in ("_pendiente", "_dictada"):
            setattr(brain, nombre, None)
            self.addCleanup(setattr, brain, nombre, None)
        gmail._mi_email = None
        self.addCleanup(setattr, gmail, "_mi_email", None)
        p = patch.object(brain, "DESTINATARIOS_PERMITIDOS", [])  # se rellena desde el perfil
        p.start()
        self.addCleanup(p.stop)
        self.service = MagicMock()
        self.perfil = self.service.users().getProfile.return_value.execute
        self.perfil.return_value = {"emailAddress": "Ana@Example.com"}
        self.get_service = patch.object(gmail, "get_service", return_value=self.service).start()
        self.addCleanup(patch.stopall)
        self.skills = {"gmail": gmail}
        self.salida = io.StringIO()

    @property
    def send(self):
        return self.service.users().messages().send

    def _ej(self, params, texto, nombre="enviar_email"):
        with contextlib.redirect_stdout(self.salida):
            return brain._ejecutar(nombre, params, self.skills, texto)

    def _turno(self, texto, *llamadas, final="Hecho."):
        """pensar() con un modelo que hace estas llamadas a tools y luego responde."""
        respuestas = [NS(stop_reason="tool_use", usage=None,
                         content=[NS(type="tool_use", id=f"t{i}", name=n, input=dict(p))])
                      for i, (n, p) in enumerate(llamadas)]
        respuestas.append(NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text=final)]))
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills=self.skills)

    def enviado_a(self):
        self.send.assert_called_once()
        raw = self.send.call_args.kwargs["body"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))["To"]

    def anunciado(self):
        anuncio = brain.anuncio_confirmacion()
        self.assertIsNotNone(anuncio)
        return re.search(r"[\w.+-]+@[\w.-]+\.\w+", anuncio).group(0)


class TestMiPropiaDireccion(Base):
    def test_mi_propia_direccion_se_resuelve_desde_el_perfil_sin_preguntar(self):
        res = self._ej(PARAMS, PEDIDO)
        self.assertFalse(res["ok"])                      # armado, a la espera de confirmar
        self.assertIn(f"Enviar email a {PROPIA} con asunto prueba de JARVIS", res["message"])
        self.assertEqual(brain._pendiente["firma"][:3], ("enviar_email", PROPIA, ""))
        self.assertEqual(brain._pendiente["params"]["destinatario"], PROPIA)
        self.perfil.assert_called_once()
        self.send.assert_not_called()
        res = self._ej(PARAMS, CONF_E)
        self.assertEqual(res, brain._ok(f"Email enviado a {PROPIA}"))
        self.assertEqual(self.enviado_a(), PROPIA)

    def test_equivalentes_de_mi_direccion(self):
        for valor in ("yo", "Yo", "mi correo", "mi dirección", "mi propia dirección", "a mí", "mi email"):
            brain._pendiente = None
            res = self._ej(dict(PARAMS, destinatario=valor), PEDIDO)
            self.assertEqual(brain._pendiente["firma"][1], PROPIA, valor)
            self.assertIn("confirmo envío", res["message"], valor)
        self.send.assert_not_called()

    def test_anunciado_es_el_enviado_con_confirmacion_del_modelo(self):
        self._turno(PEDIDO, ("enviar_email", PARAMS))
        anunciado = self.anunciado()
        self.assertEqual(anunciado, PROPIA)
        self.assertIn("a tu dirección", brain.anuncio_confirmacion())
        self.send.assert_not_called()
        self._turno(CONF_E, ("enviar_email", PARAMS), final="Enviado.")
        self.assertEqual(self.enviado_a(), anunciado)

    def test_anunciado_es_el_enviado_por_d1(self):
        self._turno(PEDIDO, ("enviar_email", PARAMS))
        anunciado = self.anunciado()
        self._turno(CONF_E)                              # el modelo no vuelve a llamar a la tool
        with patch.dict(jarvis.SKILLS, {"gmail": gmail}), contextlib.redirect_stdout(self.salida):
            self.assertIs(jarvis._d1_fix(CONF_E), True)
        self.assertEqual(self.enviado_a(), anunciado)

    def test_la_direccion_no_sale_por_consola_y_el_turno_es_privado(self):
        self._turno(PEDIDO, ("enviar_email", PARAMS))
        self.assertTrue(tts_elevenlabs.turno_privado())
        self.assertNotIn(PROPIA, self.salida.getvalue().lower())
        self.assertIn("<email>", self.salida.getvalue())

    def test_get_mi_email_es_privada(self):
        self.assertIn("get_mi_email", brain.TOOLS_PRIVADAS)
        self.assertEqual(self._ej({}, "cuál es mi correo", "get_mi_email"), brain._ok(PROPIA))

    def test_system_y_schema_piden_yo_y_no_preguntar(self):
        self.assertIn("usa destinatario 'yo'", brain.SYSTEM)
        self.assertIn("Nunca le preguntes su propia dirección", brain.SYSTEM)
        tool = next(t for t in brain.TOOLS if t["name"] == "enviar_email")
        self.assertIn("'yo'", tool["input_schema"]["properties"]["destinatario"]["description"])


class TestFalloAlResolver(Base):
    def test_fallo_de_autenticacion_dice_la_causa_y_no_pide_la_direccion(self):
        self.get_service.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
        res = self._ej(PARAMS, PEDIDO)
        self.assertEqual(res, brain._err(gmail.MENSAJES_CAUSA["autenticacion"], "mi_email_no_disponible"))
        self.assertIn("autorización de Gmail ha caducado o se ha revocado", res["message"])
        for pide in ("dicta", "cuál es", "dime tu"):
            self.assertNotIn(pide, res["message"].lower())
        self.assertIsNone(brain._pendiente)              # no arma ninguna confirmación
        self.send.assert_not_called()
        self.assertIn("[GMAIL] getProfile falló: RefreshError (autenticacion)", self.salida.getvalue())
        self.assertNotIn("invalid_grant", self.salida.getvalue())  # solo el tipo

    def test_turno_con_fallo_no_deja_anuncio_y_el_modelo_recibe_la_causa(self):
        self.get_service.side_effect = RefreshError("invalid_grant")
        self._turno(PEDIDO, ("enviar_email", PARAMS))
        self.assertIsNone(brain.anuncio_confirmacion())
        resultado = brain.ultima_traza()["tools_ejecutadas"][0]["resultado"]
        self.assertIn(gmail.MENSAJES_CAUSA["autenticacion"], str(resultado))

    def test_fallo_no_se_cachea(self):
        self.get_service.side_effect = [TransportError("sin red"), self.service]
        self.assertEqual(self._ej({}, "cuál es mi correo", "get_mi_email"),
                         brain._err(gmail.MENSAJES_CAUSA["red"], "no_disponible"))
        self.assertEqual(self._ej({}, "cuál es mi correo", "get_mi_email"), brain._ok(PROPIA))

    def test_causa_de_cada_tipo_de_error(self):
        casos = [(RefreshError("invalid_grant"), "autenticacion"), (http_error(401), "autenticacion"),
                 (FileNotFoundError("credentials.json"), "autenticacion"), (http_error(403), "permisos"),
                 (http_error(500), "servicio"), (TransportError("x"), "red"), (ConnectionError(), "red"),
                 (TimeoutError(), "red"), (ValueError("x"), "desconocida")]
        for error, causa in casos:
            self.assertEqual(gmail.causa_error(error), causa, type(error).__name__)
            gmail._mi_email = None
            self.get_service.side_effect = error
            with contextlib.redirect_stdout(self.salida):
                self.assertEqual(gmail.resolver_mi_email(), (None, causa))
        self.assertTrue(all(m and "dicta" not in m for m in gmail.MENSAJES_CAUSA.values()))

    def test_perfil_sin_direccion(self):
        self.perfil.return_value = {"emailAddress": ""}
        res = self._ej(PARAMS, PEDIDO)
        self.assertEqual(res["message"], gmail.MENSAJES_CAUSA["servicio"])


class TestDireccionDictada(Base):
    def test_extraccion_de_lo_que_transcribe_whisper(self):
        casos = {
            "envíalo a pepe punto garcia arroba correo punto es": DICTADA,
            "Vale. pepe.garcia arroba correo.es.": DICTADA,
            "a pepe.garcia@correo.es por favor": DICTADA,
            "pepegarciaarrobagmail.com": "pepegarcia@gmail.com",
            "ana guion bajo lopez arroba x guion y punto com": "ana_lopez@x-y.com",
        }
        for texto, esperada in casos.items():
            self.assertEqual(direccion.dictada(texto), esperada, texto)
        for texto in ("pepe arroba gmail", "envíame un correo a mi propia dirección", "", None,
                      "garcía arroba correo punto es", "arroba punto com"):
            self.assertIsNone(direccion.dictada(texto), texto)

    def test_formato(self):
        for valida in ("pepe@gmail.com", "Pepe.Garcia+x@sub.correo.es", "a@b.io"):
            self.assertTrue(direccion.es_valida(valida), valida)
        for invalida in ("pepe@gmail", "pepe@@gmail.com", "pe pe@gmail.com", "pepe@gmail..com", ".pepe@x.com",
                         "pepe@x.c", "a@b.com\nBcc: c@d.com", "", None, "yo", "a" * 250 + "@x.com"):
            self.assertFalse(direccion.es_valida(invalida), repr(invalida))

    def test_deletreo(self):
        self.assertEqual(direccion.deletrear("Pepe_1@x-y.es"),
                         "pe, e, pe, e, guion bajo, 1, arroba, equis, guion, i griega, punto, e, ese")

    def test_dictada_invalida_no_se_usa(self):
        texto = "envíalo a pepe arroba gmail"
        res = self._ej(dict(PARAMS, destinatario="pepe@gmail"), texto)
        self.assertIn("no tiene un formato válido", res["message"])
        self.assertIsNone(brain._pendiente)
        res = self._ej(dict(PARAMS, destinatario="pepe@gmail"), CONF_E)
        self.assertFalse(res["ok"])
        self.send.assert_not_called()

    def test_direccion_no_dictada_se_bloquea(self):
        # El modelo "corrige" o se inventa una dirección que el usuario no ha dicho
        res = self._ej(dict(PARAMS, destinatario="pepe@gmail.com"), "envíalo a pepe arroba gmail")
        self.assertIn("haya dictado por voz", res["message"])
        self.assertIsNone(brain._pendiente)
        self.send.assert_not_called()

    def test_dictada_se_deletrea_y_solo_se_envia_tras_confirmar(self):
        params = dict(PARAMS, destinatario=DICTADA)
        self._turno("mándalo a pepe punto garcia arroba correo punto es", ("enviar_email", params))
        anuncio = brain.anuncio_confirmacion()
        self.assertEqual(anuncio, f"Vas a enviar un email a {DICTADA}. Se deletrea: "
                                  f"{direccion.deletrear(DICTADA)}. Asunto: «prueba de JARVIS». "
                                  f"Texto: «hola, esto es una prueba». Sin adjuntos. "
                                  f"¿Confirmas con 'confirmo envío'?")
        self.assertIn("pe, e, pe, e, punto, ge, a, erre, ce, i, a, arroba", anuncio)
        self.send.assert_not_called()
        self._turno(CONF_E, ("enviar_email", params), final="Enviado.")
        self.assertEqual(self.enviado_a(), self.anunciado_de(anuncio))

    def anunciado_de(self, anuncio):
        return re.search(r"[\w.+-]+@[\w.-]+\.\w+", anuncio).group(0)

    def test_dictada_en_un_turno_y_enviada_en_el_siguiente(self):
        self._turno("es pepe punto garcia arroba correo punto es")       # el modelo pide el asunto
        self.assertTrue(tts_elevenlabs.turno_privado())                 # no va al log de turnos
        self.assertNotIn("pepe", self.salida.getvalue())                # ni a la consola (jarvis.log)
        res = self._ej(dict(PARAMS, destinatario=DICTADA), "el asunto es hola")
        self.assertIn("confirmo envío", res["message"])
        with patch("brain.time.monotonic", return_value=brain._dictada["ts"] + brain.DICTADA_TTL + 1):
            brain._pendiente = None
            res = self._ej(dict(PARAMS, destinatario=DICTADA), "el asunto es hola")
        self.assertIn("haya dictado por voz", res["message"])

    def test_confirmacion_de_un_solo_uso_y_con_ttl(self):
        params = dict(PARAMS, destinatario=DICTADA)
        texto = "mándalo a pepe punto garcia arroba correo punto es"
        brain._registrar_dictada(texto)                  # lo que hace pensar() con el texto del micro
        self._ej(params, texto)
        with patch("brain.time.monotonic", return_value=brain._pendiente["ts"] + brain.CONFIRM_TTL + 1):
            self.assertIn("caducada", self._ej(params, CONF_E)["message"])
        self.send.assert_not_called()
        self._ej(params, texto)
        self.assertTrue(self._ej(params, CONF_E)["ok"])
        self.assertIn("ya usada", self._ej(params, CONF_E)["message"])  # no se reutiliza
        self.send.assert_called_once()

    def test_la_confirmacion_no_vale_si_la_dice_el_modelo(self):
        params = dict(PARAMS, destinatario=DICTADA, cuerpo="confirmo envío")
        self._ej(params, "mándalo a pepe punto garcia arroba correo punto es")
        self._ej(params, "y ponle otro asunto")
        self.send.assert_not_called()

    def test_ocultar(self):
        self.assertEqual(direccion.ocultar("envía a pepe@gmail.com ya"), "envía a <email> ya")
        self.assertEqual(direccion.ocultar("envía a pepe punto garcia arroba correo punto es ya"),
                         "envía a <email> ya")
        self.assertEqual(direccion.ocultar("sin direcciones"), "sin direcciones")


if __name__ == "__main__":
    unittest.main()
