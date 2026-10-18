"""Lo que dependía del autor (AuthorityEngine y los comandos de tests del operador) sale del perfil
de rutas.json. Con un perfil limpio no se ejecuta nada (ni WSL, ni git, ni push, ni búsquedas) y
el sistema dice tal cual qué falta y dónde ponerlo, como con Gmail o GitHub sin configurar.
Con perfil, los mismos comandos de siempre. Claude y los procesos mockeados: nada real.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import brain  # noqa: E402
import rutas  # noqa: E402
from skills import authority, operador  # noqa: E402

PUBLICAS = ("siguiente_racha", "actualizar_inventario", "contar_documentos", "documentos_en_review",
            "documentos_s3", "resumen_scores", "progreso_semanal", "git_status", "temas_pendientes",
            "autopiloto", "estado_global")


class Base(unittest.TestCase):
    """Carpeta de secretos temporal propia; ningún proceso real (cualquier intento falla el test)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.procesos = []

        def prohibido(*a, **k):
            self.procesos.append(a[0] if a else k.get("args"))
            raise AssertionError(f"proceso lanzado: {a[0] if a else k}")
        for p in (patch.dict(os.environ, {"JARVIS_SECRETOS": self.dir.name, "JARVIS_STREAMING": "0"}),
                  patch.object(subprocess, "run", side_effect=prohibido),
                  patch.object(subprocess, "Popen", side_effect=prohibido),
                  patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("JARVIS_AUTHORITY_BUCKET", None)
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)

    def perfil(self, datos):
        with open(os.path.join(self.dir.name, "rutas.json"), "w", encoding="utf-8") as f:
            json.dump({"perfil": datos}, f)


class TestPerfilLimpio(Base):
    def test_authority_no_ejecuta_nada_y_lo_dice(self):
        self.assertIsNone(rutas.authority())
        for nombre in PUBLICAS:
            self.assertEqual(getattr(authority, nombre)(), authority.NO_CONFIGURADO, nombre)
        self.assertEqual(authority.lanzar_racha("tema"), authority.NO_CONFIGURADO)
        self.assertFalse(authority.racha_activa())
        self.assertEqual(self.procesos, [])
        self.assertIn('"authority" con "motor" y "roadmap"', authority.NO_CONFIGURADO)
        self.assertIn("rutas.json", authority.NO_CONFIGURADO)

    def test_el_modelo_la_pide_y_ni_se_anuncia_ni_se_ejecuta(self):
        skills = {"authority": NS(siguiente_racha=MagicMock(), estado_global=MagicMock())}
        for tool in brain.TOOLS_AUTHORITY:
            res = brain._ejecutar(tool, {}, skills, "lanza la siguiente racha")
            self.assertEqual((res["ok"], res["message"]), (False, authority.NO_CONFIGURADO))
        self.assertIsNone(brain._pendiente)   # el gate no ha dejado nada que confirmar
        skills["authority"].siguiente_racha.assert_not_called()
        skills["authority"].estado_global.assert_not_called()
        # Las listas del gate siguen igual
        self.assertEqual(brain.TOOL_RISK["siguiente_racha"], "EXTERNAL")
        self.assertEqual(brain.CONFIRM_PHRASES_ESTRICTO["siguiente_racha"], "confirmo racha")

    def test_el_sistema_lo_dice_tal_cual(self):
        respuestas = [NS(stop_reason="tool_use", usage=None,
                         content=[NS(type="tool_use", name="siguiente_racha", id="t1", input={})])]
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas) as create:
            final = brain.pensar("lanza la siguiente racha")
        self.assertEqual(final, authority.NO_CONFIGURADO)
        self.assertEqual(create.call_count, 1)   # el modelo no llega a parafrasear
        self.assertIsNone(brain._pendiente)

    def test_solo_los_tests_de_este_repo(self):
        self.assertEqual(list(operador.comandos_test()), ["jarvis"])
        with patch.object(operador, "COMANDOS_TEST", operador.comandos_test()):
            for alias in ("authority", "dam", "a;rm -rf /"):
                ok, msg = operador.ejecutar_tests(alias)
                self.assertFalse(ok)
                nombre = alias if alias != "a;rm -rf /" else "ese proyecto"
                self.assertTrue(msg.startswith(operador.TESTS_NO_CONFIGURADOS.format(proyecto=nombre)), msg)
                self.assertEqual(brain.aviso_literal(msg), operador.TESTS_NO_CONFIGURADOS.format(proyecto=nombre))
        self.assertEqual(self.procesos, [])

    def test_tests_no_configurados_literal_en_el_turno(self):
        respuestas = [NS(stop_reason="tool_use", usage=None,
                         content=[NS(type="tool_use", name="ejecutar_tests", id="t1", input={"proyecto": "dam"})])]
        os.environ.pop("JARVIS_GATE_ESTRICTO", None)   # gate por defecto: ejecutar_tests pide confirmación
        with patch.object(operador, "COMANDOS_TEST", operador.comandos_test()), \
             patch.object(brain.cliente.messages, "create", side_effect=respuestas):
            final = brain.pensar("ejecuta los tests de dam")
        self.assertEqual(final, operador.TESTS_NO_CONFIGURADOS.format(proyecto="dam"))
        self.assertIsNone(brain._pendiente)   # no pide confirmar algo que no puede ejecutarse
        self.assertEqual(self.procesos, [])

    def test_los_tests_configurados_siguen_pidiendo_confirmacion(self):
        os.environ.pop("JARVIS_GATE_ESTRICTO", None)
        res = brain._ejecutar("ejecutar_tests", {"proyecto": "jarvis"}, {}, "ejecuta los tests")
        self.assertIn("confirmo tests", res["message"])
        self.assertEqual(self.procesos, [])


class TestConPerfil(Base):
    PERFIL = {"authority": {"motor": "MotorPrueba", "roadmap": "docs/repo-prueba"},
              "proyectos": {"web": "wsl:web"},
              "tests": {"web": {"wsl": "web/api", "comando": ["venv/bin/python", "-m", "pytest", "-q"]},
                        "local": {"comando": ["venv\\Scripts\\python.exe", "-m", "unittest"]}}}

    def setUp(self):
        super().setUp()
        self.perfil(self.PERFIL)
        os.environ.update(JARVIS_WSL_HOME="/home/prueba", JARVIS_WSL_DISTRO="Ubuntu")

    def test_comandos_de_authority_con_las_carpetas_del_perfil(self):
        llamadas = []

        def run(cmd, *a, **k):
            llamadas.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="" if "ps aux" in cmd[-1] else "Tema uno", stderr="")
        with patch.object(subprocess, "run", side_effect=run), \
             patch.object(subprocess, "Popen", side_effect=lambda cmd, *a, **k: llamadas.append(cmd)):
            self.assertEqual(authority.siguiente_racha(), "Lanzando racha sobre: Tema uno")
        bash = [c[-1] for c in llamadas]
        self.assertIn("/home/prueba/docs/repo-prueba/ROADMAP_TEMAS.md", bash[1])
        self.assertEqual(bash[2], "cd ~/MotorPrueba && source venv/bin/activate && python3 racha.py 'Tema uno'")
        self.assertIn("mv /tmp/roadmap_tmp.md /home/prueba/docs/repo-prueba/ROADMAP_TEMAS.md", bash[3])
        self.assertEqual(str(authority.ruta_historico()),
                         os.path.join(os.path.expanduser("~"), "MotorPrueba", "score_historico.json"))

    def test_scores_del_historico_del_motor(self):
        carpeta = os.path.join(self.dir.name, "MotorPrueba")
        os.makedirs(carpeta)
        with open(os.path.join(carpeta, "score_historico.json"), "w", encoding="utf-8") as f:
            json.dump([{"tema": "Outbox", "score": 8, "fecha": "2026-01-01 10:00"}], f)
        with patch.object(authority.Path, "home", return_value=authority.Path(self.dir.name)):
            self.assertEqual(authority.resumen_scores(),
                             "Tienes 1 documentos generados. Score medio 8.0. Último: Outbox con 8 puntos.")
            self.assertEqual(authority.progreso_semanal(), "No has generado documentos esta semana")

    def test_anuncio_con_el_repo_del_perfil(self):
        anuncio = brain._anuncio_estricto("siguiente_racha", {})
        self.assertIn("publicará un push en un repo público de GitHub, repo-prueba.", anuncio)

    def test_comandos_de_tests(self):
        comandos = operador.comandos_test()
        self.assertEqual(comandos["web"], ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/prueba/web/api",
                                           "--", "venv/bin/python", "-m", "pytest", "-q"])
        self.assertEqual(comandos["local"], ["venv\\Scripts\\python.exe", "-m", "unittest"])
        self.assertEqual(comandos["jarvis"][0], "venv\\Scripts\\python.exe")

    def test_entradas_no_validas_se_ignoran(self):
        for malo in ({"motor": "../fuera", "roadmap": "docs"}, {"motor": "a;rm -rf ~", "roadmap": "docs"},
                     {"motor": "motor"}, {"motor": "con espacio", "roadmap": "docs"}, "AuthorityEngine"):
            self.perfil({"authority": malo})
            self.assertIsNone(rutas.authority(), malo)
        self.perfil({"tests": {"a": {"wsl": "../x", "comando": ["pytest"]}, "b": {"comando": ["pytest", "-k", "a;b"]},
                               "c": {"comando": "pytest -q"}, "d": {"comando": []}, "e": "pytest"}})
        self.assertEqual(rutas.tests(), {})


class TestSinDatosDelAutorEnElCodigo(unittest.TestCase):
    def test_ni_rutas_ni_repos_de_authorityengine(self):
        raiz = rutas.RAIZ
        archivos = subprocess.run(["git", "ls-files", "*.py"], cwd=raiz, capture_output=True, text=True).stdout.split()
        if not archivos:
            self.skipTest("sin git")
        prohibidos = (".open" + "claw", "DAM-Java" + "-Mastery", "jarvis" + "-runtime", "~/Authority" + "Engine")
        encontrados = []
        for a in archivos:
            if a.startswith("tests/"):
                continue
            with open(os.path.join(raiz, a), encoding="utf-8") as f:
                texto = f.read()
            encontrados += [f"{a}: {p}" for p in prohibidos if p in texto]
        self.assertEqual(encontrados, [])


if __name__ == "__main__":
    unittest.main()
