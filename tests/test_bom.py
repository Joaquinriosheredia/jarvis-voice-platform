"""Archivos de configuración y secretos guardados con BOM UTF-8 (el Bloc de notas y PowerShell 5
lo ponen): todos los lectores los aceptan y lo que escribe JARVIS sale sin BOM.

El caso que lo motivó: un .env con BOM y ANTHROPIC_API_KEY en la primera línea. python-dotenv
leía la variable como «\\ufeffANTHROPIC_API_KEY» y JARVIS solo funcionaba gracias a la variable de
entorno de Windows. Claves y tokens falsos, generados en cada ejecución; sin red.
"""
import ast
import io
import json
import os
import secrets
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import configurar  # noqa: E402
import doctor  # noqa: E402
import limpiar_memoria  # noqa: E402
import memoria  # noqa: E402
import memoria_sesiones  # noqa: E402
import reautorizar  # noqa: E402
import rutas  # noqa: E402
import tts_elevenlabs  # noqa: E402
from skills import gmail, google_auth  # noqa: E402

BOM = b"\xef\xbb\xbf"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clave_falsa():
    return "sk-ant-api03-" + secrets.token_urlsafe(48)


def token_falso():
    return {"token": secrets.token_hex(8), "refresh_token": secrets.token_hex(8), "client_id": "x.apps",
            "client_secret": secrets.token_hex(8), "token_uri": "https://oauth2.googleapis.com/token"}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def con_bom(self, nombre, texto):
        ruta = os.path.join(self.dir.name, nombre)
        with open(ruta, "wb") as f:
            f.write(BOM + texto.encode("utf-8"))
        return ruta

    def bytes_de(self, ruta):
        with open(ruta, "rb") as f:
            return f.read()


class TestEnv(Base):
    def setUp(self):
        super().setUp()
        self.clave = clave_falsa()
        self.env = self.con_bom(".env", f"ANTHROPIC_API_KEY={self.clave}\r\nJARVIS_TTS=piper\r\n")

    def test_jarvis_py_la_carga_con_la_clave_en_la_primera_linea(self):
        """Ejecuta las sentencias de jarvis.py anteriores a sus imports pesados (las que cargan el
        .env), con la carpeta de secretos temporal y sin la variable en el proceso."""
        with open(os.path.join(RAIZ, "jarvis.py"), encoding="utf-8") as f:
            arbol = ast.parse(f.read())
        inicio = []
        for nodo in arbol.body:
            if isinstance(nodo, ast.Import) and nodo.names[0].name == "whisper":
                break
            inicio.append(nodo)
        self.assertTrue(any("load_dotenv" in ast.unparse(n) for n in inicio))
        with patch.dict(os.environ, {"JARVIS_SECRETOS": self.dir.name}):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("JARVIS_TTS", None)
            exec(compile(ast.Module(inicio, []), "jarvis.py", "exec"), {})
            self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), self.clave)
            self.assertEqual(os.environ.get("JARVIS_TTS"), "piper")
            self.assertFalse([k for k in os.environ if k.startswith("\ufeff")])

    def test_el_doctor_la_lee_y_avisa_del_bom(self):
        with patch.object(doctor, "ENV_FILE", self.env), patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertEqual(doctor.valores_env()["ANTHROPIC_API_KEY"], self.clave)
            self.assertTrue(doctor.env_con_bom())
            r = doctor.check_anthropic_key()
        self.assertEqual(r["estado"], doctor.AVISO)
        self.assertIn("origen: .env", r["detalle"])
        self.assertIn(".env con BOM", r["detalle"])
        self.assertIn("sin BOM", r["accion"])
        self.assertNotIn(self.clave, json.dumps(r))

    def test_configurar_la_conserva_y_reescribe_sin_bom(self):
        self.assertEqual(configurar.clave_actual(configurar.leer_lineas(self.env)), self.clave)
        nueva = clave_falsa()
        with patch.object(configurar, "restringir", return_value=True):
            configurar.escribir(self.env, configurar.con_clave(configurar.leer_lineas(self.env), nueva))
        contenido = self.bytes_de(self.env)
        self.assertFalse(contenido.startswith(BOM))
        self.assertTrue(contenido.startswith(f"ANTHROPIC_API_KEY={nueva}\n".encode()))
        self.assertIn(b"JARVIS_TTS=piper\n", contenido)


class TestOrigenDeLaClave(Base):
    """doctor: de dónde sale ANTHROPIC_API_KEY, sin imprimirla."""

    def comprobar(self, entorno, env, bom=False):
        ruta = os.path.join(self.dir.name, ".env")
        if env is not None:
            with open(ruta, "wb") as f:
                f.write((BOM if bom else b"") + f"ANTHROPIC_API_KEY={env}\n".encode())
        with patch.object(doctor, "ENV_FILE", ruta), patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if entorno is not None:
                os.environ["ANTHROPIC_API_KEY"] = entorno
            r = doctor.check_anthropic_key()
        for valor in (entorno, env):
            if valor:
                self.assertNotIn(valor, json.dumps(r))
        return r

    def test_solo_entorno(self):
        r = self.comprobar(clave_falsa(), None)
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("origen: entorno de Windows", r["detalle"])
        self.assertNotIn("iguales", r["detalle"])

    def test_solo_env(self):
        r = self.comprobar(None, clave_falsa())
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("origen: .env", r["detalle"])

    def test_ninguno(self):
        r = self.comprobar(None, None)
        self.assertEqual(r["estado"], doctor.ERROR)
        self.assertIn("origen: ninguno", r["detalle"])
        r = self.comprobar(None, "")   # la línea vacía de la plantilla
        self.assertIn("origen: ninguno", r["detalle"])

    def test_iguales(self):
        clave = clave_falsa()
        r = self.comprobar(clave, clave)
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("origen: entorno de Windows; entorno y .env: iguales", r["detalle"])

    def test_distintas(self):
        r = self.comprobar(clave_falsa(), clave_falsa())
        self.assertEqual(r["estado"], doctor.AVISO)
        self.assertIn("entorno y .env: distintas", r["detalle"])
        self.assertIn("quita la variable de entorno", r["accion"])

    def test_sin_clave_y_con_bom_avisa_de_las_dos_cosas(self):
        r = self.comprobar(None, "", bom=True)
        self.assertEqual(r["estado"], doctor.ERROR)
        self.assertIn(".env con BOM", r["detalle"])


class TestRutasJson(Base):
    def test_perfil_y_reescritura_sin_bom(self):
        self.con_bom("rutas.json", json.dumps({"descargas": r"D:\x", "perfil": {"saludo": "doña Prueba"}}))
        with patch.dict(os.environ, {"JARVIS_SECRETOS": self.dir.name}):
            os.environ.pop("JARVIS_SALUDO", None)
            self.assertEqual(rutas.perfil("saludo"), "doña Prueba")
            ruta = rutas.en_secretos(rutas.ARCHIVO)
            with redirect_stdout(io.StringIO()):
                configurar.configurar_perfil(lambda _: "", lambda _: None, ruta=ruta)   # nada que añadir
                configurar.escribir_rutas(ruta, configurar.leer_rutas(ruta))
        self.assertFalse(self.bytes_de(ruta).startswith(BOM))
        self.assertEqual(json.loads(self.bytes_de(ruta))["perfil"]["saludo"], "doña Prueba")


class TestVocabulario(Base):
    """El de Whisper (audio.vocabulario) se prueba en test_audio_alucinaciones, que importa audio
    con sus dependencias de sonido simuladas."""

    def test_memoria_de_sesiones(self):
        ruta = self.con_bom("vocabulario_whisper.txt", "# comentario en la primera línea\nAuthorityEngine\nOllama\n")
        with patch.dict(os.environ, {"JARVIS_VOCABULARIO": ruta}):
            self.assertEqual(memoria_sesiones._vocabulario(), ["AuthorityEngine", "Ollama"])


class TestTokensDeGoogle(Base):
    def setUp(self):
        super().setUp()
        self.credentials = self.con_bom("credentials.json", json.dumps({"installed": {
            "client_id": "x.apps", "client_secret": secrets.token_hex(8), "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))
        self.token = self.con_bom("token.json", json.dumps(token_falso()))

    def test_comprobar_no_lo_da_por_invalido(self):
        with patch("google.oauth2.credentials.Credentials.refresh"):
            r = google_auth.comprobar("gmail", self.token, gmail.SCOPES, credentials_file=self.credentials,
                                      transporte=object())
        self.assertTrue(r["ok"], r)

    def test_gmail_credenciales(self):
        with open(self.token, "wb") as f:   # vigente: no hace falta renovarlo
            f.write(BOM + json.dumps(dict(token_falso(), expiry="2099-01-01T00:00:00Z")).encode())
        with patch.object(gmail, "CREDENTIALS_FILE", self.credentials), \
             patch("google.oauth2.credentials.Credentials.refresh", side_effect=AssertionError("sin red")):
            creds = gmail.credenciales(gmail.SCOPES, self.token)
        self.assertTrue(creds.valid)
        self.assertTrue(creds.refresh_token)

    def test_doctor_token(self):
        self.assertEqual(doctor._check_token(self.token, "Gmail", "x")["estado"], doctor.OK)

    def test_reautorizar_lee_credentials_json(self):
        with patch("google_auth_oauthlib.flow.InstalledAppFlow.run_local_server", return_value="creds") as run:
            self.assertEqual(reautorizar.flujo_oauth(self.credentials, gmail.SCOPES), "creds")
        run.assert_called_once()


class TestOtrosJson(Base):
    def test_elevenlabs(self):
        clave = secrets.token_hex(16)
        ruta = self.con_bom("elevenlabs.json", json.dumps({"api_key": clave, "voice_id": "voz"}))
        with patch.dict(os.environ), patch.object(tts_elevenlabs, "ELEVENLABS_JSON", ruta):
            for v in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
                os.environ.pop(v, None)
            self.assertEqual(tts_elevenlabs._config(), (clave, "voz"))
            with patch.object(doctor, "constante", return_value=ruta):
                self.assertEqual(doctor._elevenlabs(), (clave, "voz"))

    def test_memoria_json(self):
        """Con BOM, cargar() devolvía la memoria vacía y el siguiente guardado la habría perdido."""
        ruta = self.con_bom("memoria.json", json.dumps({"notas": ["nota de prueba"], "tareas": []}))
        with patch.object(memoria, "MEMORIA_FILE", ruta):
            self.assertEqual(memoria.cargar()["notas"], ["nota de prueba"])
        plan = limpiar_memoria.analizar(self.dir.name)
        self.assertEqual(len(plan), 1)
        self.assertIsNone(plan[0][4])   # sin error de lectura


class TestLoQueEscribeJarvisNoLlevaBom(unittest.TestCase):
    def test_plantilla_env_example(self):
        with open(os.path.join(RAIZ, "env.example"), "rb") as f:
            self.assertFalse(f.read(3) == BOM)

    def test_instalador(self):
        """instalar.ps1 copia la plantilla tal cual y escribe rutas.json con UTF8Encoding sin BOM;
        nunca con Set-Content ni Out-File (en PowerShell 5, -Encoding UTF8 pone BOM)."""
        with open(os.path.join(RAIZ, "instalar.ps1"), encoding="utf-8-sig") as f:
            script = f.read()
        self.assertIn("(New-Object System.Text.UTF8Encoding $false)", script)
        for cmdlet in ("Set-Content", "Out-File", "Add-Content"):
            self.assertNotIn(cmdlet, script)

    def test_escrituras_de_python_en_utf8_sin_bom(self):
        for nombre in ("configurar.py", "reautorizar.py", os.path.join("skills", "gmail.py"), "memoria.py",
                       "limpiar_memoria.py"):
            with open(os.path.join(RAIZ, nombre), encoding="utf-8") as f:
                codigo = f.read()
            self.assertNotRegex(codigo, r"""open\([^)]*['"]w['"]\)""", nombre)   # sin la codificación del sistema


if __name__ == "__main__":
    unittest.main()
