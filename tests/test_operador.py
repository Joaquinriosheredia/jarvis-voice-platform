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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

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


class RepoTemporal(ProyectoTemporal):
    """Repo git real en el proyecto temporal 'tmp'."""
    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        *args], cwd=self.dir, check=True, capture_output=True)

    def setUp(self):
        super().setUp()
        self.git("init", "-q")

    def commit(self, ruta, contenido, mensaje):
        self.escribir(ruta, contenido)
        self.git("add", "-f", ruta)
        self.git("commit", "-q", "-m", mensaje)


class TestGitDiff(RepoTemporal):
    def test_repo_limpio_diff_vacio_ok(self):
        self.commit("a.py", "x = 1\n", "inicial")
        self.assertEqual(operador.git_diff("tmp"), (True, "tmp: sin diferencias"))

    def test_muestra_cambios_staged_y_ref(self):
        self.commit("a.py", "x = 1\n", "inicial")
        self.escribir("a.py", "x = 2\n")
        ok, msg = operador.git_diff("tmp")
        self.assertTrue(ok, msg)
        self.assertIn("+x = 2", msg)
        self.assertEqual(operador.git_diff("tmp", staged=True), (True, "tmp: sin diferencias"))
        self.git("branch", "base")
        self.git("add", "a.py")
        self.assertIn("+x = 2", operador.git_diff("tmp", staged=True)[1])
        self.git("commit", "-q", "-m", "dos")
        ok, msg = operador.git_diff("tmp", ref="base", ruta="a.py")
        self.assertTrue(ok, msg)
        self.assertIn("-x = 1", msg)

    def test_ref_head_tilde_y_caret_validas(self):
        self.commit("a.py", "x = 1\n", "uno")
        self.commit("a.py", "x = 2\n", "dos")
        for ref in ("HEAD~1", "HEAD^"):
            ok, msg = operador.git_diff("tmp", ref=ref)
            self.assertTrue(ok, (ref, msg))
            self.assertIn("-x = 1", msg, ref)
            self.assertIn("+x = 2", msg, ref)

    def test_ref_invalida_rechazada_sin_ejecutar_git(self):
        with patch("skills.operador.subprocess.run") as run:
            for ref in ("-main", "--output=/tmp/x", "--ext-diff", "a b", "main;rm", "x" * 65, "", 7):
                ok, msg = operador.git_diff("tmp", ref=ref)
                self.assertFalse(ok, ref)
                self.assertIn("ref no válida", msg, ref)
        run.assert_not_called()

    def test_ruta_con_traversal_o_secretos_rechazada(self):
        for ruta in ("../x", "a/../../b", ".env", "config/.env.local", "id_rsa", "/etc/passwd"):
            for ok, msg in (operador.git_diff("tmp", ruta=ruta), operador.git_log("tmp", ruta=ruta)):
                self.assertFalse(ok, ruta)
                self.assertTrue(msg.startswith("BLOQUEADO"), ruta)

    def test_secretos_versionados_no_aparecen_en_el_diff(self):
        self.commit(".env", "CLAVE=vieja\n", "inicial")
        self.commit("conf/aws_credentials.json", "{}\n", "cred")
        self.escribir(".env", "CLAVE=SECRETO_NUEVO\n")
        self.escribir("conf/aws_credentials.json", '{"k": "SECRETO_NUEVO"}\n')
        self.assertEqual(operador.git_diff("tmp"), (True, "tmp: sin diferencias"))

    def test_flags_de_seguridad_y_timeout(self):
        with patch("skills.operador.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            operador.git_diff("jarvis", staged=True, ref="main")
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        for flag in ("core.fsmonitor=", "core.hooksPath=/dev/null", "diff.external=",
                     "--no-ext-diff", "--no-textconv", "--staged"):
            self.assertIn(flag, cmd)
        self.assertLess(cmd.index("main"), cmd.index("--"))
        self.assertEqual(kw["timeout"], 15)
        self.assertNotIn("shell", kw)

    def test_salida_maxima_8_kb(self):
        self.commit("g.txt", "", "vacío")
        self.escribir("g.txt", ("y" * 99 + "\n") * 300)
        ok, msg = operador.git_diff("tmp")
        self.assertTrue(ok)
        self.assertTrue(msg.endswith("(truncado)"))
        self.assertLessEqual(len(msg.encode("utf-8")), operador.MAX_BYTES + 100)


class TestGitLog(RepoTemporal):
    def test_devuelve_n_commits(self):
        for i in range(5):
            self.commit("a.txt", f"{i}\n", f"commit {i}")
        ok, msg = operador.git_log("tmp", n=3)
        self.assertTrue(ok, msg)
        lineas = msg.split(":\n", 1)[1].splitlines()
        self.assertEqual(len(lineas), 3)
        self.assertIn("commit 4", lineas[0])

    def test_n_mayor_de_30_limitado_a_30(self):
        with patch("skills.operador.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "abc uno\n", "")) as run:
            operador.git_log("jarvis", n=500)
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[cmd.index("-n") + 1], "30")
        self.assertEqual(kw["timeout"], 10)

    def test_n_invalido(self):
        for n in (0, -1, "5", True, 2.5):
            self.assertFalse(operador.git_log("tmp", n=n)[0], repr(n))

    def test_filtra_por_ruta(self):
        self.commit("a.txt", "1\n", "toca a")
        self.commit("b.txt", "1\n", "toca b")
        ok, msg = operador.git_log("tmp", ruta="a.txt")
        self.assertTrue(ok, msg)
        self.assertIn("toca a", msg)
        self.assertNotIn("toca b", msg)

    def test_repo_jarvis_real(self):
        ok, msg = operador.git_log("jarvis", n=50)
        self.assertTrue(ok, msg)
        self.assertLessEqual(len(msg.split(":\n", 1)[1].splitlines()), 30)


class TestBuscarEnCodigo(RepoTemporal):
    def setUp(self):
        super().setUp()
        self.commit("src/app.py", "def pensar(texto):\n    return texto\n", "app")
        self.commit("src/App.java", "class App { void pensar() {} }\n", "java")

    def test_encuentra_patron(self):
        ok, msg = operador.buscar_en_codigo("tmp", "pensar")
        self.assertTrue(ok, msg)
        self.assertIn("src/app.py:1:def pensar(texto):", msg)
        self.assertIn("src/App.java:1:", msg)

    def test_glob_filtra_por_extension(self):
        ok, msg = operador.buscar_en_codigo("tmp", "pensar", glob="*.py")
        self.assertTrue(ok, msg)
        self.assertIn("src/app.py", msg)
        self.assertNotIn("App.java", msg)
        for glob in ("../*.py", "*", "*.py;rm", "--no-index", "**/*.py"):
            self.assertFalse(operador.buscar_en_codigo("tmp", "x", glob=glob)[0], glob)

    def test_patron_no_encontrado_ok_vacio(self):
        self.assertEqual(operador.buscar_en_codigo("tmp", "no_existe_xyz"),
                         (True, "tmp: sin coincidencias para 'no_existe_xyz'"))

    def test_patron_con_guiones_se_busca_literal_no_como_opcion(self):
        # -O / --open-files-in-pager harían que git grep ejecutase un programa
        self.commit("raro.txt", "flag --open-files-in-pager=calc y -O y -e y --no-index\n", "raro")
        for patron in ("--open-files-in-pager=calc", "-O", "--", "-e", "--no-index"):
            ok, msg = operador.buscar_en_codigo("tmp", patron)
            self.assertTrue(ok, (patron, msg))
            self.assertIn("raro.txt:1:", msg, patron)
        with patch("skills.operador.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "true\n", "")) as run:
            operador.buscar_en_codigo("tmp", "-O")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-e") + 1], "-O")
        self.assertLess(cmd.index("-e"), cmd.index("--"))
        self.assertIn("-F", cmd)

    def test_patron_invalido_rechazado(self):
        for patron in ("", "   ", None, 5, "a\nb", "a\x00b", "x" * 201):
            ok, msg = operador.buscar_en_codigo("tmp", patron)
            self.assertFalse(ok, repr(patron))
            self.assertTrue(msg.startswith("BLOQUEADO"), repr(patron))

    def test_secretos_versionados_no_se_buscan(self):
        self.commit(".env", "PATRON_SECRETO=1\n", "env")
        self.commit("tokens/a.txt", "PATRON_SECRETO=2\n", "tok")
        self.assertEqual(operador.buscar_en_codigo("tmp", "PATRON_SECRETO"),
                         (True, "tmp: sin coincidencias para 'PATRON_SECRETO'"))

    def test_maximo_50_coincidencias_y_200_caracteres(self):
        self.commit("muchas.txt", "".join(f"aguja {'z' * 300}\n" for _ in range(80)), "muchas")
        ok, msg = operador.buscar_en_codigo("tmp", "aguja")
        self.assertTrue(ok)
        self.assertIn("mostrando las primeras 50", msg)
        lineas = msg.split(":\n", 1)[1].splitlines()
        self.assertEqual(len(lineas), 50)
        self.assertTrue(all(len(l) <= 201 for l in lineas))


class TestBuscarSinGit(ProyectoTemporal):
    def test_fallback_python_sin_repo(self):
        self.escribir("a.py", "import os\n")
        self.escribir(".env", "import os\n")
        self.escribir(os.path.join("node_modules", "x.js"), "import os\n")
        self.escribir("img.bin", b"import os\x00", "wb")
        ok, msg = operador.buscar_en_codigo("tmp", "import os")
        self.assertTrue(ok, msg)
        self.assertIn("a.py:1:import os", msg)
        for fuera in (".env", "node_modules", "img.bin"):
            self.assertNotIn(fuera, msg)
        self.assertEqual(operador.buscar_en_codigo("tmp", "nada_de_nada"),
                         (True, "tmp: sin coincidencias para 'nada_de_nada'"))


class TestIntegracionBrainFase6b(ProyectoTemporal):
    def test_riesgo_read_schema_y_dispatcher(self):
        schemas = {t["name"]: t["input_schema"] for t in brain.TOOLS}
        for tool in ("git_diff", "git_log", "buscar_en_codigo"):
            self.assertEqual(brain.TOOL_RISK[tool], "READ")
            self.assertIsNone(brain._gate(tool, {}, "x"))
            self.assertEqual(schemas[tool]["properties"]["proyecto"]["enum"],
                             ["jarvis", "authority", "java-labs", "dam", "vibe-guard"])
            self.assertIn(tool, brain.SYSTEM)
        self.escribir("a.py", "hola\n")
        res = brain._ejecutar("buscar_en_codigo", {"proyecto": "tmp", "patron": "hola"}, {})
        self.assertTrue(res["ok"], res)
        res = brain._ejecutar("git_diff", {"proyecto": "tmp", "ref": "-main"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "git_fallo"))
        res = brain._ejecutar("git_log", {"proyecto": "nada"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "git_fallo"))
        res = brain._ejecutar("buscar_en_codigo", {"proyecto": "tmp", "patron": ""}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "busqueda_rechazada"))


def completado(salida, codigo=0):
    return subprocess.CompletedProcess([], codigo, salida, None)


class TestEjecutarTests(Base):
    def test_proyecto_invalido_error_claro(self):
        with patch("skills.operador.subprocess.run") as run:
            ok, msg = operador.ejecutar_tests("windows")
        self.assertFalse(ok)
        self.assertIn("Proyecto desconocido", msg)
        run.assert_not_called()

    def test_proyecto_sin_suite_configurada(self):
        with patch("skills.operador.subprocess.run") as run:
            ok, msg = operador.ejecutar_tests("dam")
        self.assertFalse(ok)
        self.assertIn("no tiene suite de tests configurada", msg)
        run.assert_not_called()

    def test_filtro_con_inyeccion_rechazado(self):
        with patch("skills.operador.subprocess.run") as run:
            for filtro in ("a;rm -rf /", "a && b", "a|b", "$(x)", "-x", "--co", "a b", "x" * 81, "a\n", 5):
                ok, msg = operador.ejecutar_tests("jarvis", filtro)
                self.assertFalse(ok, repr(filtro))
                self.assertIn("filtro no válido", msg, repr(filtro))
        run.assert_not_called()

    def test_filtro_vacio_equivale_a_sin_filtro(self):
        with patch("skills.operador.subprocess.run", return_value=completado("1 passed\n")) as run:
            ok, msg = operador.ejecutar_tests("authority", "")
        self.assertTrue(ok, msg)
        self.assertNotIn("-k", run.call_args.args[0])

    def test_timeout_error_claro(self):
        with patch("skills.operador.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 300)):
            ok, msg = operador.ejecutar_tests("authority")
        self.assertFalse(ok)
        self.assertIn("superaron 300 s", msg)

    def test_comando_jarvis_sin_shell_y_con_filtro(self):
        with patch("skills.operador.subprocess.run",
                   return_value=completado("....\nRan 4 tests in 0.1s\n\nOK\n")) as run:
            ok, msg = operador.ejecutar_tests("jarvis", "test_gate")
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[0], os.path.join(operador.PROYECTOS["jarvis"], "venv\\Scripts\\python.exe"))
        self.assertEqual(cmd[-2:], ["-k", "test_gate"])
        self.assertEqual(kw["cwd"], operador.PROYECTOS["jarvis"])
        self.assertEqual(kw["timeout"], 300)
        self.assertEqual(kw["stderr"], subprocess.STDOUT)
        self.assertNotIn("shell", kw)
        self.assertTrue(ok)
        self.assertTrue(msg.startswith("Tests de jarvis [filtro test_gate]: PASAN. Ran 4 tests in 0.1s | OK"), msg)

    def test_comando_wsl_y_fallo_con_resumen(self):
        salida = "F.\nFAILED tests/test_x.py::test_a - assert 1 == 2\n1 failed, 1 passed in 0.2s\n"
        with patch("skills.operador.subprocess.run", return_value=completado(salida, 1)) as run:
            ok, msg = operador.ejecutar_tests("authority")
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[:5], ["wsl.exe", "-d", "Ubuntu", "--cd",
                                   "/home/usuariojoaquin/AuthorityEngine/jarvis-runtime"])
        self.assertIsNone(kw["cwd"])
        self.assertFalse(ok)
        self.assertIn("FALLAN (código 1)", msg)
        self.assertIn("1 failed, 1 passed", msg)

    def test_filtro_maven_usa_dtest(self):
        with patch.dict(operador.COMANDOS_TEST, {"java-labs": ["wsl.exe", "--", "mvn", "-q", "test"]}), \
             patch("skills.operador.subprocess.run", return_value=completado("BUILD SUCCESS\n")) as run:
            operador.ejecutar_tests("java-labs", "RateLimiterTest")
        self.assertEqual(run.call_args.args[0][-1], "-Dtest=RateLimiterTest")

    def test_salida_limitada_a_ultimos_4_kb(self):
        salida = "INICIO\n" + "x" * 20000 + "\nRan 1 test in 0.1s\nOK\n"
        with patch("skills.operador.subprocess.run", return_value=completado(salida)):
            ok, msg = operador.ejecutar_tests("jarvis")
        self.assertNotIn("INICIO", msg)
        self.assertIn("(…)", msg)
        self.assertLessEqual(len(msg.encode("utf-8")), operador.MAX_COLA_TEST + 300)
        self.assertTrue(msg.rstrip().endswith("OK"))

    def test_riesgo_external_schema_y_dispatcher(self):
        self.assertEqual(brain.TOOL_RISK["ejecutar_tests"], "EXTERNAL")
        schema = {t["name"]: t["input_schema"] for t in brain.TOOLS}["ejecutar_tests"]
        self.assertEqual(schema["properties"]["proyecto"]["enum"],
                         ["jarvis", "authority", "java-labs", "dam", "vibe-guard"])
        self.assertEqual(schema["required"], ["proyecto"])
        self.assertIn("ejecutar_tests", brain.SYSTEM)
        with patch("skills.operador.subprocess.run", return_value=completado("1 failed\n", 1)), \
             patch.dict(os.environ, {"JARVIS_GATE_ESTRICTO": "0"}):   # aquí se prueba la tool, no la confirmación del gate estricto
            res = brain._ejecutar("ejecutar_tests", {"proyecto": "authority"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "tests_fallo"))
        with patch("skills.operador.subprocess.run", return_value=completado("1 passed\n")), \
             patch.dict(os.environ, {"JARVIS_GATE_ESTRICTO": "0"}):
            res = brain._ejecutar("ejecutar_tests", {"proyecto": "authority", "filtro": "router"}, {})
        self.assertTrue(res["ok"], res)


class TestPromptCaching(Base):
    def llamar(self, usage=None, texto="explícame el patrón outbox"):
        from types import SimpleNamespace as NS
        fin = NS(stop_reason="end_turn", content=[NS(type="text", text="hola")], usage=usage)
        with patch.object(brain.cliente.messages, "create", return_value=fin) as create, \
             patch("brain._enrutar", return_value=None):
            brain.pensar(texto)
        return create.call_args.kwargs

    def test_system_lleva_cache_control(self):
        kw = self.llamar()
        self.assertEqual(kw["system"][0], {"type": "text", "text": brain.SYSTEM,
                                           "cache_control": {"type": "ephemeral"}})
        self.assertEqual(len(kw["system"]), 2)

    def test_ultima_tool_lleva_cache_control_sin_tocar_tools(self):
        kw = self.llamar()
        self.assertEqual(kw["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertTrue(all("cache_control" not in t for t in kw["tools"][:-1]))
        self.assertTrue(all("cache_control" not in t for t in brain.TOOLS))
        self.assertEqual([t["name"] for t in kw["tools"]], [t["name"] for t in brain.TOOLS])

    def test_log_cache_solo_si_hay_lectura(self):
        from types import SimpleNamespace as NS
        with patch("brain._print_seguro") as log:
            self.llamar(NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=4200))
        self.assertIn("[CACHE] hit=4200 tok guardados", [c.args[0] for c in log.call_args_list])
        with patch("brain._print_seguro") as log:
            self.llamar(NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))
        self.assertFalse(any("[CACHE]" in c.args[0] for c in log.call_args_list))


class TestFechaEnSystem(Base):
    """El 2.º bloque de system lleva la fecha real y va sin caché; el 1.º no cambia."""
    llamar = TestPromptCaching.llamar

    def test_segundo_bloque_con_el_ano_actual_y_sin_cache(self):
        import datetime
        bloque = self.llamar()["system"][1]
        self.assertNotIn("cache_control", bloque)
        self.assertIn(str(datetime.date.today().year), bloque["text"])
        self.assertIn("Usa siempre esta fecha como hoy; no uses tu fecha de entrenamiento.", bloque["text"])

    def test_bloque_1_identico_y_bloque_2_recalculado(self):
        import datetime
        momentos = [(datetime.datetime(2026, 10, 15, 9, 5), "Europe/Madrid"),
                    (datetime.datetime(2026, 10, 16, 23, 59), "Europe/Madrid")]
        with patch("brain._ahora", side_effect=momentos):
            kw1, kw2 = self.llamar(), self.llamar()
        self.assertEqual(kw1["system"][0], kw2["system"][0])
        self.assertEqual(kw1["tools"], kw2["tools"])
        con_cache = [b for b in kw1["system"] if "cache_control" in b]
        self.assertEqual(con_cache, [kw1["system"][0]])
        self.assertIn("jueves 15/10/2026 09:05 (Europe/Madrid)", kw1["system"][1]["text"])
        self.assertIn("viernes 16/10/2026 23:59 (Europe/Madrid)", kw2["system"][1]["text"])

    def test_modo_dev_tambien_lleva_la_fecha(self):
        from types import SimpleNamespace as NS
        fin = NS(stop_reason="end_turn", content=[NS(type="text", text="ok")], usage=None)

        def enrutar(texto, skills, traza, conversacion_reciente=False):
            traza["router"] = "dev"
        with patch.object(brain.cliente.messages, "create", return_value=fin) as create, \
             patch("brain._enrutar", side_effect=enrutar):
            brain.pensar("por qué falla kafka")
        system = create.call_args.kwargs["system"]
        self.assertIn(brain.SYSTEM_DEV, system[0]["text"])
        self.assertIn("Fecha y hora actuales:", system[1]["text"])
        self.assertNotIn("cache_control", system[1])

    def test_con_zona_disponible_la_etiqueta_es_europe_madrid(self):
        import datetime
        with patch("zoneinfo.ZoneInfo", return_value=datetime.timezone.utc):
            ahora, zona = brain._ahora()
            texto = brain._bloque_fecha()["text"]
        self.assertEqual(zona, "Europe/Madrid")
        self.assertIn("(Europe/Madrid)", texto)

    def test_zona_no_encontrada_usa_hora_local(self):
        import zoneinfo
        with patch("zoneinfo.ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("Europe/Madrid")):
            ahora, zona = brain._ahora()
            texto = brain._bloque_fecha()["text"]
        self.assertEqual(zona, "hora local")
        self.assertIn("(hora local)", texto)
        self.assertIsNotNone(ahora.tzinfo)

    def test_sin_tzdata_usa_hora_local(self):
        with patch.dict("sys.modules", {"zoneinfo": None}):
            ahora, zona = brain._ahora()
        self.assertEqual(zona, "hora local")
        self.assertIsNotNone(ahora.tzinfo)


if __name__ == "__main__":
    unittest.main()
