"""Fase 6: gate de lectura (listar_directorio/buscar_archivo) y skill operador.

Los tests de leer_archivo usan un proyecto temporal (patch de PROYECTOS);
los de git_estado "reales" usan C:\\Jarvis, que es un repo git.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from skills import operador  # noqa: E402

DESC = r"E:\descargas ryzen"


def bloqueado(res):
    return isinstance(res, str) and res.startswith("BLOQUEADO")


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        p = patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)


class TestGateLectura(Base):
    def test_listar_directorio_jarvis_secrets_bloqueado(self):
        for ruta in (r"C:\Jarvis-secrets", r"C:\Jarvis-secrets\sub", r"c:\JARVIS-SECRETS", "C:/Jarvis-secrets"):
            res = brain._gate("listar_directorio", {"ruta": ruta}, "lista esa carpeta")
            self.assertTrue(bloqueado(res), ruta)

    def test_buscar_archivo_jarvis_secrets_bloqueado(self):
        res = brain._gate("buscar_archivo", {"nombre": "token", "directorio": r"C:\Jarvis-secrets"}, "x")
        self.assertTrue(bloqueado(res))

    def test_blocked_exact_y_trees_bloqueados(self):
        for ruta in (r"C:\Users\Usuario", r"C:\Windows", r"C:\Users\Usuario\AppData\Local",
                     r"C:\Program Files", r"C:\Jarvis", r"C:\Jarvis\skills"):
            for nombre, clave in (("listar_directorio", "ruta"), ("buscar_archivo", "directorio")):
                self.assertTrue(bloqueado(brain._gate(nombre, {clave: ruta, "nombre": "a"}, "x")), (nombre, ruta))

    def test_unc_traversal_y_vacia_bloqueados(self):
        for ruta in (r"\\servidor\share", "//servidor/share", DESC + r"\..\..\Jarvis-secrets", "", "a\x00b"):
            self.assertTrue(bloqueado(brain._gate("listar_directorio", {"ruta": ruta}, "x")), repr(ruta))

    def test_rutas_de_lectura_permitidas(self):
        self.assertIsNone(brain._gate("listar_directorio", {}, "x"))  # default DESCARGAS
        for ruta in (DESC, r"C:\Users\Usuario\Desktop", r"C:\Users\Usuario\Documents", "C:\\"):
            self.assertIsNone(brain._gate("listar_directorio", {"ruta": ruta}, "x"), ruta)
        self.assertIsNone(brain._gate("buscar_archivo", {"nombre": "a"}, "x"))

    def test_dispatcher_no_lista_si_esta_bloqueado(self):
        with patch("brain.os.listdir") as listdir:
            res = brain._ejecutar("listar_directorio", {"ruta": r"C:\Jarvis-secrets"}, {})
        self.assertFalse(res["ok"])
        self.assertIn("BLOQUEADO", res["message"])
        listdir.assert_not_called()

    def test_buscar_archivo_poda_arboles_bloqueados_durante_el_recorrido(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sub in ("libre", "secreto"):
                os.makedirs(os.path.join(tmp, sub))
                open(os.path.join(tmp, sub, "clave.txt"), "w").close()
            # Solo 'secreto' bloqueado (el temp de Windows cuelga de AppData, ya bloqueado)
            with patch.object(brain, "BLOCKED_TREES", [os.path.join(tmp, "secreto")]):
                self.assertIsNone(brain._gate("buscar_archivo", {"nombre": "clave", "directorio": tmp}, "x"))
                res = brain._ejecutar("buscar_archivo", {"nombre": "clave", "directorio": tmp}, {})
        self.assertTrue(res["ok"])
        self.assertIn(os.path.join("libre", "clave.txt"), res["message"])
        self.assertNotIn("secreto", res["message"])


class ProyectoTemporal(Base):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        p = patch.dict(operador.PROYECTOS, {"tmp": self.dir})
        p.start()
        self.addCleanup(p.stop)

    def escribir(self, ruta, contenido, modo="w"):
        completa = os.path.join(self.dir, ruta)
        os.makedirs(os.path.dirname(completa), exist_ok=True)
        with open(completa, modo, **({} if "b" in modo else {"encoding": "utf-8"})) as f:
            f.write(contenido)


class TestLeerArchivo(ProyectoTemporal):
    def test_archivo_real_devuelve_contenido(self):
        self.escribir(os.path.join("src", "app.py"), "print('hola')\nx = 1\n")
        ok, msg = operador.leer_archivo("tmp", "src/app.py")
        self.assertTrue(ok, msg)
        self.assertIn("print('hola')", msg)
        self.assertIn("líneas 1-2", msg)

    def test_archivo_real_del_repo_jarvis(self):
        ok, msg = operador.leer_archivo("jarvis", "skills/operador.py", 1, 5)
        self.assertTrue(ok, msg)
        self.assertIn("Fase 6", msg)

    def test_traversal_bloqueado(self):
        for ruta in ("../brain.py", "..\\x.txt", "src/../../x", "a/./../../b"):
            ok, msg = operador.leer_archivo("tmp", ruta)
            self.assertFalse(ok, ruta)
            self.assertIn("traversal", msg, ruta)

    def test_rutas_absolutas_y_raras_bloqueadas(self):
        for ruta in (r"C:\Windows\win.ini", "/etc/passwd", r"\x.txt", "C:x.txt", r"\\srv\share\a",
                     "a.py:stream", "", "   ", None, 7):
            ok, msg = operador.leer_archivo("tmp", ruta)
            self.assertFalse(ok, repr(ruta))
            self.assertTrue(msg.startswith("BLOQUEADO"), repr(ruta))

    def test_env_y_secretos_bloqueados(self):
        for ruta in (".env", ".env.local", "config/.env", ".ENV", "server.pem", "priv.KEY",
                     "id_rsa", "id_rsa.pub", "aws_credentials.json", "github_token.txt",
                     "tokens/a.txt", ".git/config"):
            self.escribir(ruta, "SECRETO=1\n")
            ok, msg = operador.leer_archivo("tmp", ruta)
            self.assertFalse(ok, ruta)
            self.assertIn("secretos", msg, ruta)
            self.assertNotIn("SECRETO=1", msg)

    def test_binario_rechazado(self):
        self.escribir("img.png", b"\x89PNG\x00\x00datos", "wb")
        ok, msg = operador.leer_archivo("tmp", "img.png")
        self.assertFalse(ok)
        self.assertIn("binario", msg)

    def test_maximo_400_lineas(self):
        self.escribir("largo.txt", "".join(f"{i}\n" for i in range(1, 501)))
        ok, msg = operador.leer_archivo("tmp", "largo.txt")
        self.assertTrue(ok)
        self.assertIn("líneas 1-400 (truncado: pide un rango con desde=401)", msg)
        self.assertNotIn("\n401\n", msg)

    def test_maximo_8_kb(self):
        self.escribir("ancho.txt", ("x" * 99 + "\n") * 200)  # 100 B/línea, 20 KB
        ok, msg = operador.leer_archivo("tmp", "ancho.txt")
        self.assertTrue(ok)
        self.assertIn("líneas 1-81 (truncado", msg)  # 81 * 100 = 8100 <= 8192
        contenido = msg.split(":\n", 1)[1]
        self.assertLessEqual(len(contenido.encode("utf-8")), operador.MAX_BYTES)

    def test_rango_desde_hasta(self):
        self.escribir("r.txt", "".join(f"L{i}\n" for i in range(1, 11)))
        ok, msg = operador.leer_archivo("tmp", "r.txt", 3, 5)
        self.assertTrue(ok)
        self.assertEqual(msg.split(":\n", 1)[1], "L3\nL4\nL5\n")
        self.assertFalse(operador.leer_archivo("tmp", "r.txt", 5, 3)[0])
        self.assertFalse(operador.leer_archivo("tmp", "r.txt", 0)[0])
        self.assertFalse(operador.leer_archivo("tmp", "r.txt", "3")[0])
        self.assertFalse(operador.leer_archivo("tmp", "r.txt", 50)[0])

    def test_archivo_inexistente_o_carpeta(self):
        os.makedirs(os.path.join(self.dir, "carpeta"))
        for ruta in ("no_existe.py", "carpeta"):
            ok, msg = operador.leer_archivo("tmp", ruta)
            self.assertFalse(ok, ruta)
            self.assertIn("No existe", msg)


class TestProyectoDesconocido(Base):
    def test_error_claro_en_las_dos_tools(self):
        for ok, msg in (operador.leer_archivo("windows", "a.txt"), operador.git_estado("C:\\Windows")):
            self.assertFalse(ok)
            self.assertIn("Proyecto desconocido", msg)
            self.assertIn("jarvis", msg)  # lista los disponibles

    def test_listar_proyectos(self):
        res = operador.listar_proyectos()
        for alias in ("jarvis", "authority", "java-labs", "dam", "vibe-guard"):
            self.assertIn(alias, res)


class TestGitEstado(ProyectoTemporal):
    def test_proyecto_valido_devuelve_estado(self):
        ok, msg = operador.git_estado("jarvis")
        self.assertTrue(ok, msg)
        self.assertTrue(msg.startswith("jarvis"), msg)

    def test_repo_temporal_muestra_cambios_y_limpio(self):
        subprocess.run(["git", "init", "-q", self.dir], check=True, capture_output=True)
        ok, msg = operador.git_estado("tmp")
        self.assertEqual((ok, msg), (True, "tmp: sin cambios pendientes"))
        self.escribir("nuevo.txt", "x\n")
        ok, msg = operador.git_estado("tmp")
        self.assertTrue(ok, msg)
        self.assertIn("?? nuevo.txt", msg)

    def test_no_es_repo(self):
        ok, msg = operador.git_estado("tmp")
        self.assertFalse(ok)
        self.assertIn("git status falló", msg)

    def test_flags_de_seguridad_y_timeout(self):
        with patch("skills.operador.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            operador.git_estado("vibe-guard")
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[:3], ["git", "--no-pager", "--no-optional-locks"])
        self.assertIn("safe.directory=//wsl.localhost/Ubuntu/home/usuariojoaquin/vibe-guard", cmd)
        self.assertIn("core.fsmonitor=", cmd)
        self.assertIn("core.hooksPath=/dev/null", cmd)
        self.assertEqual(cmd[-2:], ["status", "--short"])
        self.assertEqual(kw["timeout"], 10)
        self.assertNotIn("shell", kw)

    def test_timeout_devuelve_error(self):
        with patch("skills.operador.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            ok, msg = operador.git_estado("jarvis")
        self.assertFalse(ok)
        self.assertIn("superó 10 s", msg)

    @unittest.skipUnless(os.path.isdir(operador.PROYECTOS["vibe-guard"]), "WSL no accesible")
    def test_repo_en_wsl_no_falla_por_dubious_ownership(self):
        ok, msg = operador.git_estado("vibe-guard")
        self.assertTrue(ok, msg)


class TestIntegracionBrain(ProyectoTemporal):
    def test_riesgo_read_y_schema(self):
        for tool in ("listar_proyectos", "leer_archivo", "git_estado"):
            self.assertEqual(brain.TOOL_RISK[tool], "READ")
            self.assertIsNone(brain._gate(tool, {}, "x"))
        schemas = {t["name"]: t["input_schema"] for t in brain.TOOLS}
        aliases = ["jarvis", "authority", "java-labs", "dam", "vibe-guard"]
        self.assertEqual(schemas["leer_archivo"]["properties"]["proyecto"]["enum"], aliases)
        self.assertEqual(schemas["git_estado"]["properties"]["proyecto"]["enum"], aliases)
        self.assertIn("listar_proyectos", brain.SYSTEM)

    def test_dispatcher_contrato_ok_err(self):
        self.escribir("a.txt", "hola\n")
        res = brain._ejecutar("leer_archivo", {"proyecto": "tmp", "ruta_relativa": "a.txt"}, {})
        self.assertTrue(res["ok"])
        self.assertIn("hola", res["message"])
        res = brain._ejecutar("leer_archivo", {"proyecto": "tmp", "ruta_relativa": ".env"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "lectura_rechazada"))
        res = brain._ejecutar("git_estado", {"proyecto": "nada"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "git_fallo"))
        res = brain._ejecutar("listar_proyectos", {}, {})
        self.assertTrue(res["ok"])


if __name__ == "__main__":
    unittest.main()
