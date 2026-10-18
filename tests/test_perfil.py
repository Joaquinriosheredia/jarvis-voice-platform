"""Datos personales en el perfil de rutas.json, no en el código.

Saludo, ciudad y coordenadas del tiempo (y la línea de ubicación del prompt) y proyectos del
operador salen del perfil. Sin perfil: saludo sin nombre, sin ciudad (el tiempo dice que no
está configurado y el briefing lo omite) y como proyecto solo este repo. configurar.py los
pregunta (opcional) y los añade sin sustituir nada. Los datos de prueba son inventados.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401

import brain  # noqa: E402
import briefing  # noqa: E402
import configurar  # noqa: E402
import rutas  # noqa: E402
from skills import sistema  # noqa: E402

PERFIL = {"saludo": "doña Prueba", "ciudad": "Villaprueba", "provincia": "Provincia Inventada",
          "pais": "Paísficticio", "latitud": 12.3456, "longitud": -7.25}


class Perfil(unittest.TestCase):
    """rutas.json de una carpeta temporal, sin variables de perfil en el entorno."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = patch.dict(os.environ, {"JARVIS_SECRETOS": self.dir.name})
        p.start()
        self.addCleanup(p.stop)
        for v in rutas.PERFIL.values():
            os.environ.pop(v, None)
        self.addCleanup(setattr, briefing, "_tiempo_avisado", False)
        briefing._tiempo_avisado = False

    def perfil(self, datos):
        with open(os.path.join(self.dir.name, "rutas.json"), "w", encoding="utf-8") as f:
            json.dump({"perfil": datos}, f)


class TestSinPerfil(Perfil):
    def test_saludo_sin_nombre(self):
        self.assertEqual([briefing.saludo(h) for h in (8, 15, 22)],
                         ["Buenos días.", "Buenas tardes.", "Buenas noches."])

    def test_sin_ciudad_ni_ubicacion_en_el_prompt(self):
        self.assertEqual(rutas.ubicacion(), "")
        self.assertIsNone(rutas.coordenadas())
        self.assertEqual(brain._linea_ubicacion(), "")
        self.assertIn("la ciudad del usuario", brain._descripcion_tiempo())

    def test_tiempo_desactivado_sin_red(self):
        with patch.object(sistema.req, "get") as get:
            self.assertFalse(sistema.tiempo_configurado())
            self.assertEqual(sistema.tiempo_local(), sistema.TIEMPO_NO_CONFIGURADO)
            self.assertEqual(sistema.consultar_tiempo(), (False, sistema.TIEMPO_NO_CONFIGURADO, "no_configurado"))
        get.assert_not_called()
        self.assertIn("configurar.py", sistema.TIEMPO_NO_CONFIGURADO)

    def test_el_briefing_omite_el_tiempo_y_lo_registra_una_vez(self):
        salida = io.StringIO()
        with redirect_stdout(salida), patch.object(sistema.req, "get") as get:
            textos = [briefing.generar({"tiempo": lambda: briefing.frase_tiempo(sistema)}, hora=9) for _ in range(2)]
        get.assert_not_called()
        self.assertEqual(textos, ["Buenos días. ¿Qué puedo hacer por usted hoy?"] * 2)
        self.assertEqual(salida.getvalue().count("[BRIEFING] tiempo no configurado"), 1)

    def test_solo_el_repo_como_proyecto(self):
        self.assertEqual(rutas.proyectos(), {"jarvis": rutas.RAIZ})

    def test_ciudad_sin_coordenadas_no_hay_tiempo(self):
        self.perfil({"ciudad": "Villaprueba"})
        self.assertEqual(rutas.ubicacion(), "Villaprueba")
        self.assertFalse(sistema.tiempo_configurado())
        for malas in ({"latitud": "abc", "longitud": 1}, {"latitud": 95, "longitud": 0}, {"latitud": True, "longitud": 0}):
            self.perfil({"ciudad": "Villaprueba", **malas})
            self.assertIsNone(rutas.coordenadas(), malas)


class TestConPerfil(Perfil):
    def setUp(self):
        super().setUp()
        self.perfil(PERFIL)

    def test_saludo(self):
        self.assertEqual(briefing.saludo(8), "Buenos días, doña Prueba.")
        os.environ["JARVIS_SALUDO"] = "jefa"   # el entorno manda
        self.assertEqual(briefing.saludo(21), "Buenas noches, jefa.")

    def test_ubicacion_en_el_prompt_y_en_la_tool(self):
        self.assertEqual(brain._linea_ubicacion(), "- Ubicación: Villaprueba, Provincia Inventada, Paísficticio\n")
        self.assertEqual(brain._descripcion_tiempo(), "Tiempo meteorológico actual en Villaprueba, Provincia Inventada")

    def test_tiempo_con_las_coordenadas_y_la_ciudad(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 20.5, "windspeed": 7.0, "weathercode": 0}}
        with patch.object(sistema.req, "get", return_value=resp) as get:
            texto = sistema.tiempo_local()
        self.assertIn("latitude=12.3456&longitude=-7.25&", get.call_args.args[0])
        self.assertTrue(texto.startswith("En Villaprueba hay 20.5 grados"))

    def test_proyectos_del_perfil(self):
        self.perfil({"proyectos": {"web": r"C:\code\web", "api": "wsl:api"}})
        with patch.dict(os.environ, {"JARVIS_WSL_HOME": "/home/prueba", "JARVIS_WSL_DISTRO": "Ubuntu"}):
            self.assertEqual(rutas.proyectos(), {"jarvis": rutas.RAIZ, "web": r"C:\code\web",
                                                 "api": "\\\\wsl.localhost\\Ubuntu\\home\\prueba\\api"})


class TestConfigurarPerfil(Perfil):
    def ruta(self):
        return os.path.join(self.dir.name, "rutas.json")

    def leer(self):
        with open(self.ruta(), encoding="utf-8") as f:
            return json.load(f)

    def ejecutar(self, respuestas, lugares=None):
        respuestas = list(respuestas)
        self.geo = MagicMock(return_value=lugares)
        with redirect_stdout(io.StringIO()) as salida:
            añadidas = configurar.configurar_perfil(lambda _: respuestas.pop(0) if respuestas else "", self.geo,
                                                    ruta=self.ruta())
        self.salida = salida.getvalue()
        return añadidas

    LUGARES = [dict(PERFIL, ciudad="Villaprueba de Arriba", latitud=1.0, longitud=2.0),
               {k: PERFIL[k] for k in ("ciudad", "provincia", "pais", "latitud", "longitud")}]

    def test_todo_opcional(self):
        self.assertEqual(self.ejecutar(["", ""]), [])
        self.assertFalse(os.path.exists(self.ruta()))
        self.geo.assert_not_called()

    def test_nombre_y_ciudad_elegida(self):
        self.ejecutar(["Ana", "Villaprueba", "2"], lugares=self.LUGARES)
        perfil = self.leer()["perfil"]
        self.assertEqual(perfil, {"saludo": "Ana", "nombre": "Ana", **{k: PERFIL[k] for k in
                                  ("ciudad", "provincia", "pais", "latitud", "longitud")}})
        self.geo.assert_called_once_with("Villaprueba")
        self.assertEqual(briefing.saludo(8), "Buenos días, Ana.")
        self.assertTrue(sistema.tiempo_configurado())

    def test_ciudad_sin_coordenadas_si_falla_el_servicio_o_no_aparece(self):
        for lugares, eleccion in ((None, []), ([], []), (self.LUGARES, ["0"])):
            os.remove(self.ruta()) if os.path.exists(self.ruta()) else None
            self.ejecutar(["", "Villaprueba", *eleccion], lugares=lugares)
            self.assertEqual(self.leer()["perfil"], {"ciudad": "Villaprueba"})
            self.assertIn("el tiempo queda desactivado", self.salida)
            self.assertFalse(sistema.tiempo_configurado())

    def test_no_sobrescribe_nada(self):
        original = {"descargas": r"D:\x", "perfil": {"saludo": "señora X", "nombre": "Equis", "ciudad": "Otra",
                                                       "latitud": 1.5, "longitud": 2.5, "github_usuario": "x"}}
        with open(self.ruta(), "w", encoding="utf-8") as f:
            json.dump(original, f)
        self.assertEqual(self.ejecutar(["Ana", "Villaprueba"], lugares=self.LUGARES), [])
        self.assertEqual(self.leer(), original)
        self.geo.assert_not_called()
        self.assertIn("ya está configurado", self.salida)

    def test_nombre_existente_se_respeta_al_poner_saludo(self):
        with open(self.ruta(), "w", encoding="utf-8") as f:
            json.dump({"perfil": {"nombre": "Equis"}}, f)
        self.ejecutar(["Ana", ""])
        self.assertEqual(self.leer()["perfil"], {"nombre": "Equis", "saludo": "Ana"})

    def test_rutas_json_danado_no_se_toca(self):
        with open(self.ruta(), "w", encoding="utf-8") as f:
            f.write("{no es json")
        self.assertEqual(self.ejecutar(["Ana", "Villaprueba"], lugares=self.LUGARES), [])
        with open(self.ruta(), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{no es json")


class TestSinDatosPersonalesEnElCodigo(unittest.TestCase):
    def test_ni_saludo_ni_ciudad_ni_coordenadas_del_autor(self):
        raiz = rutas.RAIZ
        archivos = subprocess.run(["git", "ls-files"], cwd=raiz, capture_output=True, text=True).stdout.split()
        if not archivos:
            self.skipTest("sin git")
        # Escritos partidos para que este archivo no se encuentre a sí mismo
        prohibidos = ("Los " + "Corrales", "señor " + "Ríos", "37." + "1099", "-5." + "0147")
        encontrados = []
        for a in archivos:
            if not a.endswith((".py", ".md", ".ps1", ".cmd", ".yml", ".json", ".js")):
                continue
            try:
                with open(os.path.join(raiz, a), encoding="utf-8") as f:
                    texto = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            encontrados += [f"{a}: {p}" for p in prohibidos if p in texto]
        self.assertEqual(encontrados, [])


if __name__ == "__main__":
    unittest.main()
