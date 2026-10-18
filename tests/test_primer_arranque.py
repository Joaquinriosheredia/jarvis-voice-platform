"""Primer arranque con lo mínimo: solo la clave de Anthropic.

Sin Gmail, Calendar, Contactos ni GitHub configurados, cada función lo dice con una frase
clara y el comando que lo arregla, sin trazas, sin el texto de la excepción (lleva rutas de
la carpeta de secretos) y sin abrir el navegador de OAuth en mitad de una conversación.
El briefing lo omite en silencio y lo deja en el registro, una vez.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import requests  # noqa: E402

import brain  # noqa: E402
import briefing  # noqa: E402
from skills import calendario, contactos, github, gmail, google_auth as ga  # noqa: E402

# Nada de esto puede llegar a la voz: trazas, nombres de excepción o rutas de los secretos
CRUDO = ("Traceback", "FileNotFoundError", "Errno", "credentials.json", "token.json", "\\")


class Carpeta(unittest.TestCase):
    """Carpeta de secretos vacía (con o sin credentials.json y tokens)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cred = os.path.join(self.dir.name, "credentials.json")
        self.token = os.path.join(self.dir.name, "token.json")
        for p in (patch.object(gmail, "CREDENTIALS_FILE", self.cred),
                  patch.object(gmail, "TOKEN_FILE", self.token),
                  patch.object(calendario, "TOKEN_CAL", os.path.join(self.dir.name, "token_calendar.json")),
                  patch.object(contactos, "TOKEN_CONTACTOS", os.path.join(self.dir.name, "token_contacts.json")),
                  # el navegador de OAuth no puede abrirse nunca desde una tool
                  patch("google_auth_oauthlib.flow.InstalledAppFlow.run_local_server",
                        side_effect=AssertionError("navegador de OAuth abierto"))):
            p.start()
            self.addCleanup(p.stop)

    def con_credenciales(self):
        with open(self.cred, "w", encoding="utf-8") as f:
            json.dump({"installed": {"client_id": "x", "client_secret": "y"}}, f)

    def limpia(self, frase):
        for c in CRUDO:
            self.assertNotIn(c, frase)


class TestCredenciales(Carpeta):
    def test_sin_credentials_json(self):
        with self.assertRaises(ga.NoConfigurado) as e:
            gmail.credenciales(gmail.SCOPES, gmail.TOKEN_FILE)
        self.assertEqual((e.exception.servicio, e.exception.estado), ("gmail", ga.SIN_CREDENCIALES))

    def test_sin_token_no_abre_el_navegador(self):
        self.con_credenciales()
        with self.assertRaises(ga.NoConfigurado) as e:
            gmail.credenciales(calendario.SCOPES_CAL, calendario.TOKEN_CAL, "calendar")
        self.assertEqual((e.exception.servicio, e.exception.estado), ("calendar", ga.SIN_TOKEN))

    def test_token_sin_refresh_token_o_danado_no_abre_el_navegador(self):
        self.con_credenciales()
        for contenido in ({"token": "a", "client_id": "x", "client_secret": "y"}, ["no es un token"]):
            with open(self.token, "w", encoding="utf-8") as f:
                json.dump(contenido, f)
            with self.assertRaises(ga.NoConfigurado) as e:
                gmail.credenciales(gmail.SCOPES, gmail.TOKEN_FILE)
            self.assertEqual(e.exception.estado, ga.INVALIDO)
            self.assertEqual(e.exception.frase, "La autorización de Gmail ha caducado o no es válida; "
                                                "ejecuta reautorizar.py gmail.")


class TestFrasesPorServicio(Carpeta):
    def test_gmail_sin_configurar(self):
        for llamada in (gmail.leer_no_leidos, lambda: gmail.enviar_email("a@b.es", "x", "y")[1],
                        lambda: gmail.buscar_emails("factura")):
            with patch("builtins.print"):
                frase = llamada()
            self.assertEqual(frase, "Gmail no está configurado: falta el archivo credentials de Google. "
                                    "Sigue el apartado Autorización de Google del README y después ejecuta "
                                    "reautorizar.py gmail.")
            self.limpia(frase)

    def test_gmail_con_credenciales_y_sin_token(self):
        self.con_credenciales()
        with patch("builtins.print"):
            self.assertEqual(gmail.leer_no_leidos(), "Gmail no está configurado; ejecuta reautorizar.py gmail.")

    def test_calendar_sin_configurar(self):
        self.con_credenciales()
        with patch("builtins.print"):
            resultados = [calendario.ver_agenda(1), calendario.eventos_hoy(),
                          calendario.crear_evento("Daily", "2026-10-20", "09:30")]
        for ok, frase in resultados:
            self.assertFalse(ok)
            self.assertEqual(frase, "Calendar no está configurado; ejecuta reautorizar.py calendar.")

    def test_calendar_otro_fallo_solo_el_tipo(self):
        ruta = os.path.join(self.dir.name, "credentials.json")
        with patch.object(calendario, "_servicio", side_effect=RuntimeError(f"fallo leyendo {ruta}")):
            ok, frase = calendario.ver_agenda(1)
        self.assertEqual(frase, "No se pudo leer el calendario: RuntimeError")
        self.assertNotIn(self.dir.name, frase)

    def test_contactos_sin_configurar(self):
        self.con_credenciales()
        ok, frase = contactos.buscar("Ana")
        self.assertFalse(ok)
        self.assertEqual(frase, "Contactos no está configurado; ejecuta reautorizar.py contacts.")

    def test_mi_direccion_sin_gmail(self):
        with patch("builtins.print"):
            mi_email, error = brain._resolver_mi_email({"gmail": gmail})
        self.assertIsNone(mi_email)
        self.assertIn("Gmail no está configurado", error)
        self.assertIn("reautorizar.py gmail", error)

    def test_la_tool_dice_la_frase(self):
        with patch("builtins.print"):
            res = brain._ejecutar("leer_emails", {}, {"gmail": gmail})
        self.assertIn("Gmail no está configurado", res["message"])
        self.limpia(res["message"])


class TestGitHub(unittest.TestCase):
    def test_sin_token_no_consulta_y_no_dice_nada_pendiente(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}), patch.object(requests, "get") as get:
            datos = github.estado()
        get.assert_not_called()
        self.assertEqual(datos, {"repos": [], "causa": github.SIN_TOKEN})
        self.assertIn("GitHub no está configurado", github.detalle(datos))
        self.assertIn("GITHUB_TOKEN", github.frase(datos))

    def test_sin_repos_validos_no_dice_nada_pendiente(self):
        # "repo" sin dueño configurado: antes salía "En GitHub no hay nada pendiente"
        with patch.dict(os.environ, {"GITHUB_TOKEN": "falso", "JARVIS_GITHUB_REPOS": ""}), \
             patch.object(github, "DUENIO", ""), patch.object(requests, "get") as get:
            datos = github.estado()
        get.assert_not_called()
        self.assertEqual(datos["causa"], github.SIN_REPOS)
        self.assertNotIn("nada pendiente", github.frase(datos))
        self.assertIn("JARVIS_GITHUB_REPOS", github.frase(datos))

    def test_briefing_sin_github_en_silencio(self):
        self.addCleanup(setattr, briefing, "_github_avisado", False)
        briefing._github_avisado = False
        with patch.object(github, "estado", return_value={"repos": [], "causa": github.SIN_REPOS}), \
             patch("builtins.print") as imprimir:
            self.assertEqual(briefing.frase_github(), "")
            self.assertEqual(briefing.frase_github(), "")
        self.assertEqual(len(imprimir.call_args_list), 1)
        self.assertIn("GitHub no configurado (sin_repos)", imprimir.call_args.args[0])


class TestArranque(unittest.TestCase):
    def todos(self, estado):
        return {s: ga._resultado(s, estado) for s in ("gmail", "calendar", "contacts")}

    def test_nada_configurado_sin_aviso_y_en_el_registro(self):
        for estado in ga.NO_CONFIGURADO:
            self.assertIsNone(ga.aviso_arranque(self.todos(estado)))
            self.assertEqual(ga.no_configurados(self.todos(estado)), ["gmail", "calendar", "contacts"])

    def test_el_briefing_no_toca_lo_no_configurado(self):
        f = briefing.fuentes(MagicMock(), MagicMock(), list, list, resultados=self.todos(ga.SIN_CREDENCIALES))
        self.assertNotIn("agenda", f)
        self.assertNotIn("emails", f)


if __name__ == "__main__":
    unittest.main()
