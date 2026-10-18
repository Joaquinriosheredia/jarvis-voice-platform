"""doctor.py --reporte: informe para pegar en un issue sin claves, direcciones, rutas con el
nombre del usuario ni texto de turnos.

Los secretos, nombres y rutas son falsos y se generan en cada ejecución; se meten a la vez en
las comprobaciones del doctor y en un jarvis.log de prueba, y ninguno puede salir en el informe.
"""
import contextlib
import io
import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import doctor  # noqa: E402


def _palabra(n=8):
    return "".join(secrets.choice("bcdfghjklmnpqrstvwxz") for _ in range(n))


class Base(unittest.TestCase):
    def setUp(self):
        self.usuario = _palabra().capitalize()
        self.equipo = "PC-" + _palabra(6).upper()
        self.nombre = f"{_palabra().capitalize()} {_palabra().capitalize()}"
        self.marca_descargas = _palabra()
        self.secretos = {
            "anthropic": "sk-ant-api03-" + secrets.token_urlsafe(40),
            "github": "github_pat_" + secrets.token_hex(24),
            "google": "AIza" + secrets.token_urlsafe(26)[:35].ljust(35, "x"),
            "bearer": "Bearer " + secrets.token_hex(20),
            "email": f"{_palabra()}.{_palabra()}@{_palabra()}.es",
        }
        self.turno = f"texto privado del turno {_palabra()}"
        self.descargas = rf"E:\descargas {self.marca_descargas}"
        entorno = {"USERNAME": self.usuario, "COMPUTERNAME": self.equipo, "JARVIS_NOMBRE_COMPLETO": self.nombre,
                   "JARVIS_DESCARGAS": self.descargas}
        p = patch.dict(os.environ, entorno)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("JARVIS_HOME", None)   # el patrón de C:\Users\<usuario> también se prueba
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = os.path.join(self.dir.name, "jarvis.log")

    def escribir_log(self, lineas):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")

    def comprobaciones(self):
        s, u = self.secretos, self.usuario
        detalles = [
            f"ANTHROPIC_API_KEY={s['anthropic']}",
            f"token {s['github']} y cabecera Authorization: {s['bearer']}",
            f"clave de Google {s['google']} para {s['email']}",
            rf"Descargas {self.descargas} (rutas.json); Home C:\Users\{u} (rutas.json)",
            rf"proyecto en \\wsl.localhost\Ubuntu\home\{u.lower()}\repo y /home/{u.lower()}/repo",
            f"equipo {self.equipo}, perfil {self.nombre}",
        ]
        return [{"id": f"c{i}", "nombre": f"Comprobación {i}", "estado": doctor.AVISO, "detalle": d,
                 "accion": rf"Edita C:\Users\{u}\AppData\x.json"} for i, d in enumerate(detalles)]

    def informe(self):
        with patch.object(doctor, "ejecutar", return_value=self.comprobaciones()):
            return doctor.reporte(ruta_log=self.log)


class TestSinDatosPersonales(Base):
    def test_nada_de_lo_falso_se_cuela(self):
        s, u = self.secretos, self.usuario
        self.escribir_log([
            f"👂 Tú: {self.turno} con {s['email']}",
            f"🔊 Jarvis: {self.turno}",
            f"[TRAZA+] i=0 tool=leer_emails resultado=Error leyendo {self.turno}",
            f"[GMAIL] error leyendo emails: NoConfigurado (no_configurado) {s['email']}",
            rf"❌ Error: ConnectionError con {s['github']} en C:\Users\{u}\Desktop\{self.turno}.txt",
            f"[TTS] fallback a piper: HTTP 500 {self.turno}",
            "Traceback (most recent call last):",
            f'  File "{os.path.join(doctor.RAIZ, "brain.py")}", line 812, in pensar',
            f"    respuesta = cliente.messages.create({self.turno})",
            rf'  File "C:\Users\{u}\AppData\Local\Python\Lib\site-packages\anthropic\_base_client.py", '
            "line 99, in request",
            f"anthropic.APIConnectionError: Connection error. {s['anthropic']} {self.turno}",
        ])
        texto = self.informe()
        prohibidos = list(s.values()) + [s["bearer"].split()[1], u, u.lower(), self.equipo, self.nombre,
                                         self.marca_descargas, self.turno, "Tú:", "Jarvis:"]
        for valor in prohibidos:
            self.assertNotIn(valor, texto)
        for marca in ("[SECRETO-REDACTADO:", "<email>", r"C:\Users\<usuario>", r"\home\<usuario>",
                      "/home/<usuario>/", "<equipo>", "<descargas>"):
            self.assertIn(marca, texto)

    def test_firmas_de_los_errores(self):
        self.escribir_log([
            f"[GMAIL] error leyendo emails: NoConfigurado (no_configurado) {self.turno}",
            f"[TTS] fallback a piper: HTTP 503 {self.turno}",
            f"[GMAIL] error leyendo emails: NoConfigurado (no_configurado) {self.turno}",
            "Traceback (most recent call last):",
            f'  File "{os.path.join(doctor.RAIZ, "brain.py")}", line 812, in pensar',
            f'  File "{os.path.join(doctor.RAIZ, "skills", "gmail.py")}", line 40, in get_service',
            f"RuntimeError: {self.turno}",
            f"[ROUTER] claude {self.turno}",   # sin señal de error: no sale
        ])
        errores = doctor.errores_del_log(self.log)
        self.assertEqual(errores, [
            ("[TTS] HTTP 503", 1),
            ("[GMAIL] NoConfigurado", 2),
            ("Traza RuntimeError ← skills/gmail.py:40 en get_service ← brain.py:812 en pensar", 1),
        ])

    def test_maximo_y_solo_el_final(self):
        self.escribir_log([f"[X{i}] error {i}: ValueError" for i in range(40)])
        errores = doctor.errores_del_log(self.log, maximo=5)
        self.assertEqual([f for f, _ in errores], [f"[X{i}] ValueError" for i in range(35, 40)])

    def test_sin_log(self):
        self.assertEqual(doctor.errores_del_log(os.path.join(self.dir.name, "no-existe.log")), [])
        self.assertIn("ninguno", self.informe())


class TestContenido(Base):
    def test_versiones_y_cada_comprobacion(self):
        self.escribir_log([])
        texto = self.informe()
        for campo in ("**Commit:**", "**Python:**", "**Sistema:**", "**torch:**", "**CUDA:**"):
            self.assertIn(campo, texto)
        for i in range(6):
            self.assertIn(f"| AVISO | Comprobación {i} |", texto)
        self.assertIn("**Doctor: AVISO** (0 OK, 6 AVISO, 0 ERROR)", texto)

    def test_main_reporte(self):
        salida = io.StringIO()
        with patch.object(doctor, "reporte", return_value="informe") as rep, contextlib.redirect_stdout(salida):
            self.assertEqual(doctor.main(["--reporte"]), 0)
        rep.assert_called_once_with(live=False)
        self.assertEqual(salida.getvalue().strip(), "informe")

    def test_enlazado_desde_la_plantilla_y_el_readme(self):
        for ruta in (os.path.join(".github", "ISSUE_TEMPLATE", "error.yml"), "README.md"):
            with open(os.path.join(doctor.RAIZ, ruta), encoding="utf-8") as f:
                self.assertIn("doctor.py --reporte", f.read(), ruta)


if __name__ == "__main__":
    unittest.main()
