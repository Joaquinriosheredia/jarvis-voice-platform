"""Aviso de autorización de Google al arrancar jarvis.py: una vez, por voz y en la
UI, con la causa real y el comando; sin tocar Gmail si el token no sirve. jarvis.py
se importa con whisper/audio/skills sustituidos por mocks (como test_jarvis_d1)."""
import atexit
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
from skills import google_auth as ga  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail", "skills.google_auth")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)


# La memoria entre sesiones de estos tests nunca es la real (jarvis.main purga al arrancar)
import tempfile  # noqa: E402
import memoria_sesiones  # noqa: E402
_DIR_MEMORIA = tempfile.TemporaryDirectory()
_RUTA_MEMORIA = patch.object(memoria_sesiones, "RUTA_DB", os.path.join(_DIR_MEMORIA.name, "memoria.db"))


def setUpModule():
    _RUTA_MEMORIA.start()


def tearDownModule():
    _RUTA_MEMORIA.stop()
    _DIR_MEMORIA.cleanup()


def todos(gmail, calendar=ga.VALIDO):
    return {"gmail": ga._resultado("gmail", gmail), "calendar": ga._resultado("calendar", calendar)}


class Base(unittest.TestCase):
    def setUp(self):
        p = patch.object(jarvis, "google_auth", ga)   # el módulo real, con comprobar_todos mockeado
        p.start()
        self.addCleanup(p.stop)
        jarvis.audio.reset_mock()
        jarvis.sistema.tiempo_sevilla.return_value = "Sevilla, 20 grados"
        jarvis.mem.tareas_vencidas.return_value = []
        jarvis.mem.tareas_pendientes.return_value = []
        # El briefing nunca toca Calendar ni GitHub de verdad en los tests
        for p in (patch.object(jarvis.briefing.calendario, "eventos_hoy", return_value=(True, [])),
                  patch.object(jarvis.briefing.github, "estado", return_value={"repos": [], "causa": None})):
            p.start()
            self.addCleanup(p.stop)

    def arrancar(self, resultados=None, error=None):
        """main() hasta el primer escuchar (que corta el test con Ctrl+C)."""
        audio = jarvis.audio
        audio.escuchar.side_effect = KeyboardInterrupt()
        audio.apagado_pedido.return_value = False
        with patch.object(ga, "comprobar_todos", return_value=resultados, side_effect=error) as comp, \
             patch.object(jarvis.brain, "al_texto_parcial", None), patch("ui_bridge.al_parar", None), \
             patch("ui_bridge.start"), patch("ui_bridge.avisar") as avisar, \
             patch.object(jarvis.brain, "cargar_destinatarios_permitidos") as cargar, \
             patch.object(jarvis, "_resumen_emails_arranque", return_value="Tienes un email.") as emails, \
             contextlib.redirect_stdout(io.StringIO()) as salida:
            jarvis.main()
        self.comp, self.avisar, self.cargar, self.emails = comp, avisar, cargar, emails
        self.consola = salida.getvalue()
        self.dichos = [c.args[0] for c in audio.hablar.call_args_list]
        return self.dichos[0]    # el resumen de arranque


class TestAvisoAlArrancar(Base):
    def test_revocado_avisa_por_voz_y_en_la_ui_una_vez(self):
        resumen = self.arrancar(todos(ga.REVOCADO))
        self.comp.assert_called_once()
        self.avisar.assert_called_once()
        pantalla = self.avisar.call_args.args[0]
        self.assertIn("invalid_grant", pantalla)
        self.assertIn(r"venv\Scripts\python.exe reautorizar.py gmail", pantalla)
        self.assertIn("Gmail ha caducado o se ha revocado", resumen)
        self.assertIn("reautorizar gmail", resumen)
        self.assertTrue(resumen.endswith("¿Qué puedo hacer por usted hoy?"))  # aviso antes de la pregunta
        self.assertEqual(sum("revocado" in d for d in self.dichos), 1)
        self.assertIn("[AUTH] Gmail: la autorización ha caducado o se ha revocado", self.consola)

    def test_con_el_token_de_gmail_roto_no_se_toca_gmail(self):
        for estado in (ga.REVOCADO, ga.CADUCADO, ga.SIN_TOKEN, ga.SIN_RED):
            jarvis.audio.reset_mock()
            resumen = self.arrancar(todos(estado))
            self.emails.assert_not_called()       # sin resumen de correo (ni navegador de OAuth)
            self.cargar.assert_not_called()       # sin getProfile
            self.assertNotIn("Tienes un email", resumen)

    def test_todo_valido_sin_aviso(self):
        resumen = self.arrancar(todos(ga.VALIDO, ga.RENOVADO))
        self.avisar.assert_not_called()
        self.cargar.assert_called_once()
        self.assertIn("Tienes un email.", resumen)
        self.assertNotIn("Aviso", resumen)

    def test_sin_red_no_avisa_pero_lo_registra(self):
        resumen = self.arrancar(todos(ga.SIN_RED, ga.SIN_RED))
        self.avisar.assert_not_called()
        self.assertNotIn("Aviso", resumen)
        self.assertIn("[AUTH] Gmail: no verificado", self.consola)

    def test_calendar_caducado_con_gmail_bien(self):
        resumen = self.arrancar(todos(ga.VALIDO, ga.REVOCADO))
        self.assertIn("Tienes un email.", resumen)
        self.assertIn("Calendar", resumen)
        self.assertIn("reautorizar.py calendar", self.avisar.call_args.args[0])

    def test_si_la_comprobacion_falla_arranca_igual(self):
        resumen = self.arrancar(error=RuntimeError("roto"))
        self.avisar.assert_not_called()
        self.assertIn("Tienes un email.", resumen)    # sin comprobación: como antes
        self.assertIn("[AUTH] no se pudo comprobar", self.consola)


class TestResumenDiario(Base):
    def test_sin_argumentos_como_antes(self):
        with patch.object(jarvis, "_resumen_emails_arranque", return_value="Tienes un email."):
            self.assertIn("Tienes un email.", jarvis.resumen_diario())

    def test_aviso_dentro_del_resumen(self):
        with patch.object(jarvis, "_resumen_emails_arranque") as emails:
            texto = jarvis.resumen_diario(todos(ga.REVOCADO), "Aviso. Algo.")
        emails.assert_not_called()
        self.assertTrue(texto.endswith("Aviso. Algo. ¿Qué puedo hacer por usted hoy?"))


if __name__ == "__main__":
    unittest.main()
