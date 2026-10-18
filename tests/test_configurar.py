"""configurar.py: la clave de Anthropic se pide sin mostrarla, se valida, se guarda en el .env
de la carpeta de secretos (solo esa línea, permisos restringidos) y nunca se imprime. Es
idempotente: una clave existente no se sustituye sin preguntar.

Las claves son falsas y se generan en cada ejecución; la validación está mockeada (salvo el
mapeo de errores, con excepciones reales de la SDK y sin red).
"""
import contextlib
import io
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (carpeta de secretos temporal)

import anthropic  # noqa: E402
import httpx  # noqa: E402

import configurar  # noqa: E402


def clave_falsa():
    return "sk-ant-api03-" + secrets.token_urlsafe(48)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = os.path.join(self.dir.name, ".env")
        for p in (patch.object(configurar, "ruta_env", return_value=self.env),
                  patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})):
            p.start()
            self.addCleanup(p.stop)
        self.restringir = patch.object(configurar, "restringir", return_value=True).start()
        self.addCleanup(patch.stopall)

    def ejecutar(self, claves=(), respuestas=(), validacion=configurar.OK):
        """main() con las claves que «teclea» el usuario y las respuestas a las preguntas s/n."""
        claves, respuestas = list(claves), list(respuestas)
        self.pedidas = 0

        def pedir(_):
            self.pedidas += 1
            return claves.pop(0) if claves else ""
        self.validar = MagicMock(side_effect=validacion if callable(validacion) else lambda v: validacion)
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
            codigo = configurar.main(pedir=pedir, preguntar=lambda _: respuestas.pop(0) if respuestas else "",
                                     validar_=self.validar)
        self.salida = salida.getvalue()
        return codigo

    def leer(self):
        with open(self.env, encoding="utf-8") as f:
            return f.read()

    def escribir(self, texto):
        with open(self.env, "w", encoding="utf-8") as f:
            f.write(texto)


class TestPrimeraVez(Base):
    def test_sin_env_parte_de_la_plantilla_y_guarda_la_clave(self):
        clave = clave_falsa()
        self.assertEqual(self.ejecutar([clave]), 0)
        texto = self.leer()
        self.assertIn(f"ANTHROPIC_API_KEY={clave}\n", texto)
        self.assertEqual(texto.count("ANTHROPIC_API_KEY="), 1)
        self.assertIn("GITHUB_TOKEN=\n", texto)          # el resto de la plantilla, sin valores
        self.validar.assert_called_once_with(clave)
        self.restringir.assert_called_with(self.env)
        self.assertNotIn(clave, self.salida)
        self.assertNotIn(clave[:12], self.salida.replace("sk-ant-", ""))
        self.assertIn("reautorizar.py gmail", self.salida)  # explica los servicios opcionales
        self.assertIn("GITHUB_TOKEN", self.salida)

    def test_env_existente_solo_cambia_esa_linea(self):
        otro = "ghp_" + secrets.token_hex(18)
        original = f"# mis claves\nGITHUB_TOKEN={otro}\nANTHROPIC_API_KEY=\nJARVIS_TTS=piper\n"
        self.escribir(original)
        clave = clave_falsa()
        self.ejecutar([clave])
        self.assertEqual(self.leer(), original.replace("ANTHROPIC_API_KEY=\n", f"ANTHROPIC_API_KEY={clave}\n"))
        self.assertNotIn(otro, self.salida)

    def test_clave_rechazada_no_se_guarda_y_se_puede_reintentar(self):
        mala, buena = clave_falsa(), clave_falsa()
        self.ejecutar([mala, buena], validacion=lambda v: configurar.RECHAZADA if v == mala else configurar.OK)
        self.assertIn("rechazado", self.salida)
        self.assertNotIn(mala, self.leer())
        self.assertIn(f"ANTHROPIC_API_KEY={buena}", self.leer())

    def test_intentos_limitados(self):
        codigo = self.ejecutar([clave_falsa() for _ in range(5)], validacion=configurar.RECHAZADA)
        self.assertEqual(self.pedidas, configurar.INTENTOS)
        self.assertEqual(codigo, 1)
        self.assertFalse(os.path.exists(self.env))

    def test_sin_red_pregunta_y_por_defecto_no_guarda(self):
        self.ejecutar([clave_falsa()], validacion=configurar.SIN_RED)
        self.assertFalse(os.path.exists(self.env))
        clave = clave_falsa()
        self.ejecutar([clave], respuestas=["s"], validacion=configurar.SIN_RED)
        self.assertIn(f"ANTHROPIC_API_KEY={clave}", self.leer())

    def test_formato_absurdo_no_llega_a_validarse(self):
        self.ejecutar(["hola que tal", "corta"])
        self.validar.assert_not_called()
        self.assertFalse(os.path.exists(self.env))

    def test_cancelar_no_escribe(self):
        self.assertEqual(self.ejecutar([""]), 1)
        self.assertFalse(os.path.exists(self.env))


class TestIdempotente(Base):
    def test_clave_existente_no_se_sustituye_sin_preguntar(self):
        clave = clave_falsa()
        original = f"ANTHROPIC_API_KEY={clave}\nTAVILY_API_KEY=\n"
        self.escribir(original)
        for _ in range(2):
            self.assertEqual(self.ejecutar(respuestas=["", ""]), 0)   # Intro: comprobar sí, sustituir no
            self.assertEqual(self.leer(), original)
            self.assertEqual(self.pedidas, 0)
            self.validar.assert_called_once_with(clave)
            self.assertIn("válida", self.salida)
            self.assertNotIn(clave, self.salida)
        self.restringir.assert_called_with(self.env)   # los permisos se reafirman igualmente

    def test_sustituir_si_se_pide(self):
        vieja, nueva = clave_falsa(), clave_falsa()
        self.escribir(f"ANTHROPIC_API_KEY={vieja}\n")
        self.ejecutar([nueva], respuestas=["n", "s"])
        self.assertEqual(self.leer(), f"ANTHROPIC_API_KEY={nueva}\n")
        self.assertNotIn(vieja, self.salida + "".join(c.args[0] for c in self.validar.call_args_list))

    def test_variable_de_entorno_distinta_avisa_sin_mostrarla(self):
        clave, del_entorno = clave_falsa(), clave_falsa()
        self.escribir(f"ANTHROPIC_API_KEY={clave}\n")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": del_entorno}):
            self.ejecutar(respuestas=["n", "n"])
        self.assertIn("variable de entorno ANTHROPIC_API_KEY", self.salida)
        self.assertNotIn(del_entorno, self.salida)


class TestValidar(unittest.TestCase):
    def cliente(self, error):
        c = MagicMock()
        c.models.list.side_effect = error
        return c

    def test_mapeo_de_errores(self):
        peticion = httpx.Request("GET", "https://api.anthropic.com/v1/models")
        casos = {
            configurar.RECHAZADA: anthropic.AuthenticationError(
                "x", response=httpx.Response(401, request=peticion), body=None),
            configurar.SIN_RED: anthropic.APIConnectionError(request=peticion),
            configurar.ERROR: anthropic.InternalServerError(
                "x", response=httpx.Response(500, request=peticion), body=None),
        }
        for esperado, error in casos.items():
            self.assertEqual(configurar.validar(clave_falsa(), cliente=self.cliente(error)), esperado)
        self.assertEqual(configurar.validar(clave_falsa(), cliente=MagicMock()), configurar.OK)

    def test_llamada_minima(self):
        c = MagicMock()
        configurar.validar(clave_falsa(), cliente=c)
        c.models.list.assert_called_once_with(limit=1)
        c.messages.create.assert_not_called()


class TestPermisos(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "icacls solo existe en Windows")
    def test_solo_el_usuario_actual(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, ".env")
            # Con entradas explícitas de otros (como las de SYSTEM y Administradores que trae un
            # archivo creado desde una consola elevada): también se quitan
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("x")
            subprocess.run(["icacls", ruta, "/grant", "*S-1-5-32-545:R", "*S-1-1-0:R"], capture_output=True)
            antes = subprocess.run(["icacls", ruta], capture_output=True, text=True).stdout
            self.assertGreater(len([l for l in antes.splitlines() if ":(" in l]), 2, antes)
            self.assertTrue(configurar.restringir(ruta))
            acl = subprocess.run(["icacls", ruta], capture_output=True, text=True).stdout
            self.assertEqual(len([l for l in acl.splitlines() if ":(" in l]), 1, acl)
            configurar.escribir(ruta, [f"ANTHROPIC_API_KEY={clave_falsa()}"])
            acl = subprocess.run(["icacls", ruta], capture_output=True, text=True).stdout
            entradas = [l for l in acl.splitlines() if ":(" in l]
            self.assertEqual(len(entradas), 1, acl)
            self.assertIn(os.environ["USERNAME"].lower(), entradas[0].lower())
            self.assertNotIn("(I)", acl)   # sin herencia
            self.assertIn("(F)", entradas[0])
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])   # sin restos

    def test_nunca_por_argumento(self):
        with open(configurar.__file__, encoding="utf-8") as f:
            codigo = f.read()
        self.assertNotIn("sys.argv", codigo)
        self.assertNotIn("argparse", codigo)


class TestInstalador(unittest.TestCase):
    def test_instalar_ofrece_el_asistente(self):
        with open(os.path.join(os.path.dirname(configurar.__file__), "instalar.ps1"), encoding="utf-8-sig") as f:
            script = f.read()
        self.assertIn('& $PythonVenv (Join-Path $Raiz "configurar.py")', script)
        self.assertIn("[switch]$SinConfigurar", script)


if __name__ == "__main__":
    unittest.main()
