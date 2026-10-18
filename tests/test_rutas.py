"""Rutas configurables (rutas.py): entorno > rutas.json en la carpeta de secretos > respaldo
(los valores de la instalación original). Y ninguna ruta de usuario escrita en el código."""
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import doctor  # noqa: E402
import rutas  # noqa: E402

VARIABLES = [v for v, _ in rutas.CLAVES.values()] + list(rutas.PERFIL.values()) + ["JARVIS_SECRETOS"]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        for v in VARIABLES:
            os.environ.pop(v, None)
        os.environ["JARVIS_SECRETOS"] = self.dir   # sin rutas.json salvo que el test lo escriba
        # El respaldo sale del usuario de Windows: uno de prueba, igual aquí y en el CI
        os.environ.update({"USERNAME": "Prueba", "USERPROFILE": r"C:\Users\prueba", "HOME": r"C:\Users\prueba"})

    def archivo(self, datos, crudo=None):
        with open(os.path.join(self.dir, rutas.ARCHIVO), "w", encoding="utf-8") as f:
            f.write(crudo if crudo is not None else json.dumps(datos))


class TestOrden(Base):
    def test_respaldo_generico_del_usuario_de_windows(self):
        self.assertEqual(rutas.home(), os.path.expanduser("~"))
        self.assertEqual(rutas.descargas(), os.path.join(rutas.home(), "Downloads"))
        self.assertEqual(rutas.escritorio(), os.path.join(rutas.home(), "Desktop"))
        self.assertEqual(rutas.vlc(), r"C:\Program Files\VideoLAN\VLC\vlc.exe")
        self.assertEqual(rutas.wsl_ruta("AuthorityEngine/jarvis-runtime"),
                         "/home/prueba/AuthorityEngine/jarvis-runtime")
        self.assertEqual(rutas.wsl_unc("vibe-guard"), "\\\\wsl.localhost\\Ubuntu\\home\\prueba\\vibe-guard")
        self.assertEqual(rutas.origen("descargas"), "respaldo")
        os.environ.pop("USERNAME")
        os.environ.pop("USER", None)
        self.assertEqual(rutas.wsl_home(), "/home/usuario")

    def test_perfil_entorno_rutas_json_o_vacio(self):
        self.assertEqual(rutas.perfil("nombre"), "")
        self.archivo({"perfil": {"nombre": "Ana", "github_usuario": "ana-dev", "web_github": 3}})
        self.assertEqual((rutas.perfil("nombre"), rutas.perfil("github_usuario")), ("Ana", "ana-dev"))
        self.assertEqual(rutas.perfil("web_github"), "")    # tipos raros: vacío
        os.environ["JARVIS_NOMBRE"] = "Eva"
        self.assertEqual(rutas.perfil("nombre"), "Eva")
        self.archivo(None, "[1]")
        self.assertEqual(rutas.perfil("github_usuario"), "")

    def test_secretos_por_entorno_o_respaldo(self):
        os.environ.pop("JARVIS_SECRETOS")
        self.assertEqual(rutas.secretos(), r"C:\Jarvis-secrets")
        self.assertEqual(rutas.en_secretos(".env"), r"C:\Jarvis-secrets\.env")
        os.environ["JARVIS_SECRETOS"] = r"D:\secretos"
        self.assertEqual(rutas.en_secretos("token.json"), r"D:\secretos\token.json")

    def test_rutas_json_y_entorno_por_encima(self):
        self.archivo({"descargas": r"C:\Users\ana\Downloads", "home": r"C:\Users\ana"})
        self.assertEqual(rutas.descargas(), r"C:\Users\ana\Downloads")
        self.assertEqual(rutas.escritorio(), r"C:\Users\ana\Desktop")   # derivado de home
        self.assertEqual(rutas.origen("descargas"), "rutas.json")
        os.environ["JARVIS_DESCARGAS"] = r"F:\bajadas"
        self.assertEqual(rutas.descargas(), r"F:\bajadas")
        self.assertEqual(rutas.origen("descargas"), "entorno")

    def test_rutas_json_roto_o_con_tipos_raros_se_ignora(self):
        for crudo in ("{no es json", "[1, 2]", json.dumps({"descargas": 3, "home": "  "})):
            with self.subTest(crudo):
                self.archivo(None, crudo)
                self.assertEqual(rutas.descargas(), os.path.join(os.path.expanduser("~"), "Downloads"))
                self.assertEqual(rutas.home(), os.path.expanduser("~"))

    def test_rutas_json_con_bom_de_powershell(self):
        with open(os.path.join(self.dir, rutas.ARCHIVO), "w", encoding="utf-8-sig") as f:
            json.dump({"descargas": r"C:\x"}, f)
        self.assertEqual(rutas.descargas(), r"C:\x")

    def test_proyectos(self):
        self.assertEqual(rutas.proyectos(), {"jarvis": rutas.RAIZ})   # sin perfil: solo este repo
        self.archivo({"perfil": {"proyectos": {"dam": "wsl:DAM-Java-Mastery", "web": r"C:\code\web"}}})
        self.assertEqual(rutas.proyectos(), {"jarvis": rutas.RAIZ,
                                             "dam": "\\\\wsl.localhost\\Ubuntu\\home\\prueba\\DAM-Java-Mastery",
                                             "web": r"C:\code\web"})
        os.environ["JARVIS_WSL_HOME"] = "/home/ana/"
        os.environ["JARVIS_WSL_DISTRO"] = "Debian"
        self.assertEqual(rutas.proyectos()["dam"], "\\\\wsl.localhost\\Debian\\home\\ana\\DAM-Java-Mastery")
        self.archivo({"proyectos": {"web": r"C:\code\web"}})   # formato anterior: sustituye la lista
        self.assertEqual(rutas.proyectos(), {"web": r"C:\code\web"})


class TestDoctor(Base):
    def test_constante_evalua_llamadas_a_rutas(self):
        self.assertEqual(doctor.constante(os.path.join("skills", "gmail.py"), "TOKEN_FILE"),
                         os.path.join(self.dir, "token.json"))
        self.assertTrue(doctor.constante("audio.py", "VOICE_MODEL").startswith(rutas.RAIZ))

    def test_check_rutas(self):
        os.environ["JARVIS_HOME"] = self.dir
        os.environ["JARVIS_DESCARGAS"] = self.dir
        os.environ["JARVIS_ESCRITORIO"] = self.dir
        self.assertEqual(doctor.check_rutas()["estado"], doctor.OK)
        os.environ["JARVIS_DESCARGAS"] = os.path.join(self.dir, "no-existe")
        r = doctor.check_rutas()
        self.assertEqual(r["estado"], doctor.AVISO)
        self.assertIn("Descargas", r["detalle"])
        self.assertIn("(entorno)", r["detalle"])


# Patrones de rutas de esta máquina que no pueden volver a aparecer en el código
RUTA_DE_USUARIO = re.compile(r"(?i)[a-z]:\\users\\[^\\]+|e:\\descargas|[a-z]:\\jarvis(-secrets)?\b")
# Nombres de usuario de la instalación original: en ningún archivo del repo (van al perfil de
# rutas.json). Escritos con (?:) para que este archivo no se encuentre a sí mismo. El README,
# el CHANGELOG y SECURITY.md pueden enlazar el repo publicado, y LICENSE y el README nombrar al titular.
NOMBRE_FIJO = re.compile(r"(?i)usuariojoa(?:)quin|joaq(?:)u[ií]n|ry(?:)zen|users\\usuario\b|sinbarr(?:)eras")
URL_DEL_REPO = re.compile(r"(?i)github\.com/joaq(?:)uinriosheredia/jarvis-voice-platform")
TITULAR = re.compile(r"(?:Copyright|©) 2026 Joaq(?:)uín Ríos Heredia")


def _cadenas_de_codigo(ruta):
    """Literales de texto de un .py que no son docstrings ni comentarios."""
    with open(ruta, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    docstrings = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and nodo.body:
            primero = nodo.body[0]
            if isinstance(primero, ast.Expr) and isinstance(getattr(primero, "value", None), ast.Constant):
                docstrings.add(id(primero.value))
    return [n.value for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class TestSinRutasFijas(unittest.TestCase):
    def test_ningun_py_fuera_de_tests_tiene_rutas_de_usuario(self):
        encontradas = []
        for raiz, dirs, archivos in os.walk(rutas.RAIZ):
            dirs[:] = [d for d in dirs if d not in ("venv", "tests", "node_modules", ".git", "__pycache__", "logs")]
            for a in archivos:
                if a.endswith(".py") and a not in ("rutas.py",) and not a.startswith("test_"):
                    ruta = os.path.join(raiz, a)
                    for texto in _cadenas_de_codigo(ruta):
                        if RUTA_DE_USUARIO.search(texto):
                            encontradas.append(f"{os.path.relpath(ruta, rutas.RAIZ)}: {texto[:60]!r}")
        self.assertEqual(encontradas, [])

    def test_ningun_archivo_versionado_tiene_nombres_de_usuario(self):
        import subprocess
        try:
            archivos = subprocess.run(["git", "ls-files"], cwd=rutas.RAIZ, capture_output=True, text=True,
                                      timeout=30, check=True).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            self.skipTest("sin git")
        encontrados = []
        for a in archivos:
            if a.endswith((".gif", ".ico", ".png", ".onnx")) or a.startswith("app/package-lock"):
                continue
            try:
                with open(os.path.join(rutas.RAIZ, a), encoding="utf-8") as f:
                    texto = TITULAR.sub("", URL_DEL_REPO.sub("", f.read()))
            except (OSError, UnicodeDecodeError):
                continue
            if NOMBRE_FIJO.search(texto):
                encontrados.append(a)
        self.assertEqual(encontrados, [])

    def test_lanzador_sin_ruta_fija(self):
        with open(rutas.en_raiz("app", "start.vbs"), encoding="utf-8") as f:
            codigo = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("'"))
        self.assertNotRegex(codigo, RUTA_DE_USUARIO)
        self.assertIn("WScript.ScriptFullName", codigo)


if __name__ == "__main__":
    unittest.main()
