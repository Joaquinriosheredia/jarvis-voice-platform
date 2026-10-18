"""Autorización de Google: estado real de cada token (con mocks, sin red), aviso al
arrancar (voz y UI), reautorizar.py y ui_bridge.avisar. Ningún test llama a Google."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

from google.auth import exceptions as gexc  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402

import reautorizar  # noqa: E402
import ui_bridge  # noqa: E402
from skills import google_auth as ga  # noqa: E402

ACCESO   = "ACCESO-FALSO-1234567890"
REFRESCO = "REFRESCO-FALSO-0987654321"
SECRETO  = "FALSO-FALSO-FALSO-FALSO"
EMAIL    = "pepe.garcia@example.com"
SCOPES   = ["https://www.googleapis.com/auth/gmail.modify"]


def _fecha(horas):
    return (datetime.now(timezone.utc) + timedelta(hours=horas)).replace(tzinfo=None).isoformat() + "Z"


def token(refresh=True, horas=1, **extra):
    datos = {"token": ACCESO, "client_id": "cliente.apps.googleusercontent.com",
             "client_secret": SECRETO, "token_uri": "https://oauth2.googleapis.com/token",
             "scopes": SCOPES, "expiry": _fecha(horas), **extra}
    if refresh:
        datos["refresh_token"] = REFRESCO
    return datos


def refresh_error(codigo, descripcion="Token has been expired or revoked."):
    return gexc.RefreshError(f"{codigo}: {descripcion}", {"error": codigo, "error_description": descripcion})


class Carpeta(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.cred = os.path.join(self.dir, "credentials.json")
        self.tok = os.path.join(self.dir, "token.json")
        self.escribir(self.cred, {"installed": {"client_id": "x", "client_secret": SECRETO}})

    def escribir(self, ruta, datos):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(datos if isinstance(datos, str) else json.dumps(datos))

    def comprobar(self, refresh=None):
        """Estado con Credentials.refresh mockeado: nunca sale a la red."""
        with patch.object(Credentials, "refresh", side_effect=refresh) as r:
            res = ga.comprobar("gmail", self.tok, SCOPES, self.cred)
        self.refresh = r
        return res

    def sin_secretos(self, *textos):
        for t in textos:
            for prohibido in (ACCESO, REFRESCO, SECRETO, EMAIL):
                self.assertNotIn(prohibido, t)


# ── Cada estado del token ─────────────────────────────────────────
class TestEstados(Carpeta):
    def test_valido_renueva_de_verdad(self):
        self.escribir(self.tok, token(horas=1))
        r = self.comprobar()
        self.assertEqual(r["estado"], ga.VALIDO)
        self.assertTrue(r["ok"])
        self.refresh.assert_called_once()     # aunque el access token siga vigente

    def test_renovado_si_el_access_token_habia_caducado(self):
        self.escribir(self.tok, token(horas=-2))
        r = self.comprobar()
        self.assertEqual(r["estado"], ga.RENOVADO)
        self.assertTrue(r["ok"])
        self.assertFalse(r["reautorizar"])

    def test_revocado_invalid_grant(self):
        self.escribir(self.tok, token(horas=-2))
        r = self.comprobar(refresh_error("invalid_grant"))
        self.assertEqual((r["estado"], r["codigo"]), (ga.REVOCADO, "invalid_grant"))
        self.assertTrue(r["reautorizar"])
        self.assertIn("caducado o se ha revocado", ga.mensaje(r))
        self.assertIn("reautorizar.py gmail", ga.accion(r))

    def test_invalid_grant_sin_dict_tambien_es_revocado(self):
        self.escribir(self.tok, token())
        r = self.comprobar(gexc.RefreshError("invalid_grant: Bad Request"))
        self.assertEqual(r["estado"], ga.REVOCADO)

    def test_caducado_sin_refresh_token(self):
        self.escribir(self.tok, token(refresh=False, horas=-2))
        r = self.comprobar()
        self.assertEqual(r["estado"], ga.CADUCADO)
        self.refresh.assert_not_called()
        self.assertTrue(r["reautorizar"])

    def test_sin_red(self):
        self.escribir(self.tok, token())
        for error in (gexc.TransportError("sin conexión"), ConnectionError(), TimeoutError()):
            r = self.comprobar(error)
            self.assertEqual(r["estado"], ga.SIN_RED, error)
            self.assertFalse(r["ok"])
            self.assertFalse(r["reautorizar"])
            self.assertIn("no verificado", ga.mensaje(r))

    def test_sin_token(self):
        r = self.comprobar()
        self.assertEqual(r["estado"], ga.SIN_TOKEN)
        self.refresh.assert_not_called()
        self.assertIn("reautorizar.py gmail", ga.accion(r))

    def test_sin_credenciales(self):
        os.remove(self.cred)
        self.escribir(self.tok, token())
        r = self.comprobar()
        self.assertEqual(r["estado"], ga.SIN_CREDENCIALES)
        self.assertIn("credentials.json", ga.accion(r))
        self.assertFalse(r["reautorizar"])     # reautorizar.py tampoco puede sin el cliente OAuth

    def test_token_danado(self):
        for contenido in ("{no es json", "[1, 2]", json.dumps({"refresh_token": REFRESCO})):
            self.escribir(self.tok, contenido)
            self.assertEqual(self.comprobar()["estado"], ga.INVALIDO, contenido)

    def test_invalid_scope_es_invalido(self):
        self.escribir(self.tok, token())
        self.assertEqual(self.comprobar(refresh_error("invalid_scope", "Bad scope"))["estado"], ga.INVALIDO)

    def test_otro_rechazo_con_codigo_de_la_lista(self):
        self.escribir(self.tok, token())
        r = self.comprobar(refresh_error("invalid_client", "The OAuth client was not found."))
        self.assertEqual((r["estado"], r["codigo"]), (ga.ERROR, "invalid_client"))
        self.assertIn("(invalid_client)", ga.mensaje(r))

    def test_codigo_fuera_de_la_lista_no_se_muestra(self):
        self.escribir(self.tok, token())
        r = self.comprobar(gexc.RefreshError(f"raro: {REFRESCO}", {"error": REFRESCO}))
        self.assertEqual((r["estado"], r["codigo"]), (ga.ERROR, None))
        self.sin_secretos(ga.mensaje(r), json.dumps(r))

    def test_no_escribe_el_token(self):
        self.escribir(self.tok, token(horas=-2))
        antes = os.path.getmtime(self.tok), open(self.tok, encoding="utf-8").read()
        self.comprobar()
        self.assertEqual(antes, (os.path.getmtime(self.tok), open(self.tok, encoding="utf-8").read()))

    def test_nunca_lanza(self):
        self.escribir(self.tok, token())
        self.assertEqual(self.comprobar(RuntimeError(EMAIL))["estado"], ga.ERROR)

    def test_ningun_texto_lleva_secretos_ni_direcciones(self):
        self.escribir(self.tok, token())
        errores = [None, refresh_error("invalid_grant", f"{EMAIL} {REFRESCO}"),
                   gexc.TransportError(f"{EMAIL} {ACCESO}"), RuntimeError(f"{EMAIL} {REFRESCO}")]
        for error in errores:
            r = self.comprobar(error)
            aviso = ga.aviso_arranque({"gmail": r}) or ("", "")
            self.sin_secretos(json.dumps(r), ga.mensaje(r), ga.accion(r), *aviso)

    def test_limpio_redacta(self):
        texto = ga.limpio(f"token={REFRESCO} de {EMAIL}")
        self.sin_secretos(texto)
        self.assertIn("<email>", texto)

    def test_timeout_corto_en_el_transporte(self):
        llamadas = []
        with patch("google.auth.transport.requests.Request", return_value=lambda *a, **k: llamadas.append(k)):
            transporte = ga._transporte(timeout=7)
            transporte("https://oauth2.googleapis.com/token", method="POST")
            transporte("https://oauth2.googleapis.com/token", timeout=120)
        self.assertEqual([k["timeout"] for k in llamadas], [7, 7])


class TestComprobarTodos(unittest.TestCase):
    def test_paralelo_y_lo_que_no_responde_cuenta_como_sin_red(self):
        import threading
        bloqueo = threading.Event()
        self.addCleanup(bloqueo.set)

        def comprobar(servicio, *a, **k):
            if servicio == "calendar":
                bloqueo.wait(5)
            return ga._resultado(servicio, ga.VALIDO)
        config = {"gmail": ("Gmail", "t1", []), "calendar": ("Calendar", "t2", [])}
        with patch.object(ga, "servicios", return_value=config), patch.object(ga, "comprobar", side_effect=comprobar):
            r = ga.comprobar_todos(espera=0.2)
        self.assertEqual(r["gmail"]["estado"], ga.VALIDO)
        self.assertEqual(r["calendar"]["estado"], ga.SIN_RED)

    def test_servicios_usa_las_constantes_de_las_skills(self):
        from skills import calendario, gmail
        s = ga.servicios()
        self.assertEqual(s["gmail"][1:], (gmail.TOKEN_FILE, list(gmail.SCOPES)))
        self.assertEqual(s["calendar"][1:], (calendario.TOKEN_CAL, list(calendario.SCOPES_CAL)))


# ── Aviso al arrancar ─────────────────────────────────────────────
class TestAvisoArranque(unittest.TestCase):
    def todos(self, gmail, calendar=ga.VALIDO):
        return {"gmail": ga._resultado("gmail", gmail), "calendar": ga._resultado("calendar", calendar)}

    def test_sin_aviso_si_todo_va_bien_o_no_hay_red(self):
        self.assertIsNone(ga.aviso_arranque(self.todos(ga.VALIDO, ga.RENOVADO)))
        self.assertIsNone(ga.aviso_arranque(self.todos(ga.SIN_RED, ga.SIN_RED)))

    def test_revocado_con_causa_y_comando(self):
        voz, pantalla = ga.aviso_arranque(self.todos(ga.REVOCADO))
        self.assertIn("Gmail ha caducado o se ha revocado", voz)
        self.assertIn("reautorizar gmail", voz)
        self.assertIn("comando exacto en pantalla", voz)
        self.assertIn("invalid_grant", pantalla)
        self.assertIn(r"venv\Scripts\python.exe reautorizar.py gmail", pantalla)
        self.assertNotIn("Calendar", pantalla)

    def test_los_dos_servicios(self):
        voz, pantalla = ga.aviso_arranque(self.todos(ga.CADUCADO, ga.REVOCADO))
        self.assertIn("reautorizar.py gmail", pantalla)
        self.assertIn("reautorizar.py calendar", pantalla)
        self.assertIn("La autorización de Calendar ha caducado o se ha revocado", voz)

    def test_lo_no_configurado_no_avisa_al_arrancar(self):
        for estado in ga.NO_CONFIGURADO:
            self.assertIsNone(ga.aviso_arranque(self.todos(estado, estado)), estado)
        voz, pantalla = ga.aviso_arranque(self.todos(ga.REVOCADO, ga.SIN_TOKEN))
        self.assertNotIn("Calendar", voz + pantalla)

    def test_cada_estado_que_requiere_accion_avisa(self):
        for estado in ga.AVISAR:
            self.assertIsNotNone(ga.aviso_arranque(self.todos(estado)), estado)


class TestUiAvisar(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, ui_bridge, "_avisos", [])
        ui_bridge._avisos = []

    def test_guarda_una_vez_y_se_reenvia(self):
        ui_bridge.avisar("⚠ Gmail: caducado\n   → comando")
        ui_bridge.avisar("⚠ Gmail: caducado\n   → comando")
        ui_bridge.avisar(None)
        self.assertEqual(len(ui_bridge._avisos), 1)
        msg = json.loads(ui_bridge._avisos[0])
        self.assertEqual((msg["type"], msg["v"]), ("warning", 1))
        self.assertIn("→ comando", msg["message"])

    def test_cliente_que_conecta_despues_recibe_el_aviso(self):
        import asyncio

        class Cliente:  # websocket falso: recibe lo que envía el servidor y se desconecta
            def __init__(self):
                self.recibidos = []

            async def send(self, mensaje):
                self.recibidos.append(json.loads(mensaje))

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration
        with patch.object(ui_bridge, "_loop", None):
            ui_bridge.avisar("⚠ Calendar: no autorizado")   # antes de que haya UI
        cliente = Cliente()
        with patch.object(ui_bridge, "_clientes", set()):
            asyncio.run(ui_bridge._handler(cliente))
        self.assertEqual([m["type"] for m in cliente.recibidos], ["hello", "warning"])
        self.assertEqual(cliente.recibidos[1]["message"], "⚠ Calendar: no autorizado")


# ── reautorizar.py ────────────────────────────────────────────────
class TestReautorizar(Carpeta):
    AHORA = datetime(2026, 10, 16, 13, 5, 1)

    def correr(self, flujo, comprobar=None):
        comprobar = comprobar or (lambda s, t, sc, c: ga._resultado(s, ga.RENOVADO))
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            r = reautorizar.reautorizar("gmail", self.tok, SCOPES, self.cred, flujo=flujo,
                                        comprobar=comprobar, ahora=self.AHORA)
        self.salida = salida.getvalue()
        return r

    def creds(self, refresh=REFRESCO):
        return MagicMock(refresh_token=refresh, to_json=lambda: json.dumps(token(refresh=bool(refresh))))

    def test_proceso_completo(self):
        self.escribir(self.tok, token())
        flujo = MagicMock(return_value=self.creds())
        r = self.correr(flujo)
        self.assertTrue(r["ok"])
        flujo.assert_called_once_with(self.cred, SCOPES)
        renombrado = os.path.join(self.dir, "token-caducado-20261016-130501.json")
        self.assertTrue(os.path.isfile(renombrado))           # el anterior no se borra
        with open(self.tok, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["refresh_token"], REFRESCO)
        self.assertIn("token-caducado-20261016-130501.json", self.salida)
        self.assertIn("3/3  OK", self.salida)
        self.sin_secretos(self.salida)

    def test_confirma_con_la_comprobacion_real(self):
        self.escribir(self.tok, token())
        vistos = []

        def comprobar(servicio, token_file, scopes, cred):
            vistos.append(token_file)
            return ga._resultado(servicio, ga.REVOCADO, "invalid_grant")
        r = self.correr(MagicMock(return_value=self.creds()), comprobar)
        self.assertFalse(r["ok"])
        self.assertEqual(vistos, [self.tok])
        self.assertIn("3/3  ERROR", self.salida)

    def test_si_el_flujo_falla_restaura_el_token(self):
        self.escribir(self.tok, token())
        original = open(self.tok, encoding="utf-8").read()
        r = self.correr(MagicMock(side_effect=OSError("puerto ocupado")))
        self.assertFalse(r["ok"])
        self.assertEqual(open(self.tok, encoding="utf-8").read(), original)
        self.assertEqual(sorted(os.listdir(self.dir)), ["credentials.json", "token.json"])
        self.assertIn("Restaurado", self.salida)

    def test_sin_refresh_token_no_se_da_por_bueno(self):
        self.escribir(self.tok, token())
        r = self.correr(MagicMock(return_value=self.creds(refresh=None)))
        self.assertFalse(r["ok"])
        self.assertTrue(os.path.isfile(self.tok))               # el anterior, restaurado

    def test_sin_token_previo(self):
        r = self.correr(MagicMock(return_value=self.creds()))
        self.assertTrue(r["ok"])
        self.assertIn("No había token.json", self.salida)

    def test_sin_credentials_no_abre_el_navegador(self):
        os.remove(self.cred)
        flujo = MagicMock()
        r = self.correr(flujo)
        self.assertEqual(r["estado"], ga.SIN_CREDENCIALES)
        flujo.assert_not_called()

    def test_main_valida_el_servicio(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            reautorizar.main(["outlook"])
        with patch.object(reautorizar, "reautorizar", return_value={"ok": True}) as re_:
            self.assertEqual(reautorizar.main(["calendar"]), 0)
        self.assertEqual(re_.call_args.args[0], "calendar")
        self.assertTrue(re_.call_args.args[1].endswith("token_calendar.json"))


if __name__ == "__main__":
    unittest.main()
