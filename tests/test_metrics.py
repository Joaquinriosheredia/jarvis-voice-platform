"""Observabilidad por turno (metrics.py) y replay.py. Sin red ni disco real:
el JSONL va a una carpeta temporal y ui_bridge.emit está mockeado."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import metrics  # noqa: E402
import replay  # noqa: E402
import tts_elevenlabs  # noqa: E402


def uso(i=0, o=0, cr=0, cc=0):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cc)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ruta = os.path.join(tmp.name, "logs", "turnos.jsonl")
        for objetivo, valor in (("RUTA_TURNOS", self.ruta),):
            p = patch.object(metrics, objetivo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.emit = patch.object(metrics.ui_bridge, "emit").start()
        self.addCleanup(patch.stopall)
        p = patch("builtins.print")
        p.start()
        tts_elevenlabs.nuevo_turno()
        self.addCleanup(tts_elevenlabs.nuevo_turno)
        self.addCleanup(metrics.descartar_turno)

    def lineas(self):
        with open(self.ruta, encoding="utf-8") as f:
            return [json.loads(l) for l in f]


class TestCoste(Base):
    def test_coste_con_usage_conocido(self):
        # (1000*1 + 500*5 + 4000*0.10 + 2000*1.25) / 1e6
        self.assertAlmostEqual(metrics.coste_usd(1000, 500, 4000, 2000), 0.0064)
        self.assertEqual(metrics.coste_usd(), 0)

    def test_suma_usage_de_varias_iteraciones(self):
        metrics.iniciar_turno()
        metrics.registrar_uso_llm(uso(100, 20, 5000, 0))
        metrics.registrar_uso_llm(uso(50, 30, 5000, 200))
        metrics.registrar_uso_llm(None)
        metrics.registrar_uso_llm(NS())  # usage sin campos: no rompe
        reg = metrics.cerrar_turno("hola", "adiós")
        self.assertEqual((reg["input"], reg["output"], reg["cache_read"], reg["cache_creation"]),
                         (150, 50, 10000, 200))
        self.assertAlmostEqual(reg["coste_usd"], metrics.coste_usd(150, 50, 10000, 200))

    def test_fases_tools_ruta_y_tts(self):
        metrics.iniciar_turno()
        metrics.registrar_ruta("claude")
        metrics.marcar("whisper", 400.4)
        with metrics.cronometro("llm"):
            pass
        metrics.marcar("tools", 30)
        metrics.registrar_tool("hora")
        metrics.registrar_tts("piper", 12, 200)
        metrics.registrar_tts("piper", 8, 100)
        reg = metrics.cerrar_turno("qué hora es", "Son las diez")
        self.assertEqual(reg["ruta"], "claude")
        self.assertEqual(reg["tools"], ["hora"])
        self.assertEqual((reg["whisper_ms"], reg["tools_ms"], reg["tts_ms"]), (400, 30, 300))
        self.assertEqual((reg["tts_provider"], reg["chars_tts"]), ("piper", 20))
        self.assertFalse(reg["privado"])

    def test_fuera_de_turno_no_hace_nada(self):
        metrics.marcar("llm", 10)
        metrics.registrar_tool("hora")
        self.assertIsNone(metrics.cerrar_turno("x", "y"))
        self.assertFalse(os.path.exists(self.ruta))


class TestPersistencia(Base):
    def test_jsonl_valido_una_linea_por_turno(self):
        for i in range(3):
            metrics.iniciar_turno()
            metrics.cerrar_turno(f"texto {i}", "ok")
        lineas = self.lineas()
        self.assertEqual(len(lineas), 3)
        self.assertEqual([l["texto_usuario"] for l in lineas], ["texto 0", "texto 1", "texto 2"])
        for clave in ("ts", "ruta", "tools", "privado", "whisper_ms", "router_ms", "llm_ms",
                      "tools_ms", "tts_ms", "total_ms", "input", "output", "cache_read",
                      "cache_creation", "coste_usd", "tts_provider", "chars_tts"):
            self.assertIn(clave, lineas[0])

    def test_turno_privado_sin_textos(self):
        metrics.iniciar_turno()
        metrics.registrar_tool("leer_emails")
        tts_elevenlabs.marcar_privado()
        metrics.cerrar_turno("lee mis emails", "Tienes un email de Ana: Contrato")
        linea = self.lineas()[0]
        self.assertTrue(linea["privado"])
        self.assertNotIn("texto_usuario", linea)
        self.assertNotIn("respuesta", linea)
        with open(self.ruta, encoding="utf-8") as f:
            crudo = f.read()
        self.assertNotIn("Ana", crudo)
        self.assertNotIn("emails", crudo.replace("leer_emails", ""))

    def test_turno_normal_trunca_a_500(self):
        metrics.iniciar_turno()
        metrics.cerrar_turno("a" * 800, "b" * 499)
        linea = self.lineas()[0]
        self.assertEqual(linea["texto_usuario"], "a" * 500 + "…")
        self.assertEqual(linea["respuesta"], "b" * 499)

    def test_jarvis_log_texto_0_y_1(self):
        for valor, con_texto in (("0", False), ("1", True), (None, True)):
            entorno = {k: v for k, v in os.environ.items() if k != "JARVIS_LOG_TEXTO"}
            if valor is not None:
                entorno["JARVIS_LOG_TEXTO"] = valor
            with patch.dict(os.environ, entorno, clear=True):  # se lee al escribir, no al importar
                metrics.iniciar_turno()
                metrics.cerrar_turno("qué hora es", "Son las diez")
            linea = self.lineas()[-1]
            self.assertEqual("texto_usuario" in linea, con_texto, valor)
            self.assertEqual("respuesta" in linea, con_texto, valor)
            self.assertIn("total_ms", linea)

    def test_tools_de_codigo_sin_respuesta_en_el_log(self):
        for tool in ("leer_archivo", "git_diff", "buscar_en_codigo", "ejecutar_tests"):
            metrics.iniciar_turno()
            metrics.registrar_tool(tool)
            reg = metrics.cerrar_turno(f"usa {tool}", "def secreto(): return 42")
            linea = self.lineas()[-1]
            self.assertEqual(linea["texto_usuario"], f"usa {tool}", tool)
            self.assertNotIn("respuesta", linea, tool)
            self.assertEqual(reg["tools"], [tool])
        with open(self.ruta, encoding="utf-8") as f:
            self.assertNotIn("def secreto", f.read())

    def test_rotacion_al_superar_5_mb(self):
        self.assertEqual(metrics.MAX_BYTES_LOG, 5 * 1024 * 1024)
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write("x" * (metrics.MAX_BYTES_LOG + 1))
        backup = self.ruta.replace(".jsonl", ".1.jsonl")
        with open(backup, "w", encoding="utf-8") as f:
            f.write("backup viejo\n")
        metrics.iniciar_turno()
        metrics.cerrar_turno("hola", "ok")
        self.assertEqual(os.path.getsize(backup), metrics.MAX_BYTES_LOG + 1)  # el viejo se sustituye
        self.assertEqual(len(self.lineas()), 1)

    def test_disco_roto_nunca_lanza(self):
        metrics.iniciar_turno()
        with patch("builtins.open", side_effect=OSError("disco lleno")), \
             patch("metrics.os.makedirs", side_effect=OSError("sin permisos")):
            reg = metrics.cerrar_turno("hola", "ok")
            self.assertEqual(metrics.cargar(), [])
            self.assertIsNotNone(reg)
        self.emit.assert_called_once()  # la UI recibe el turno aunque no se haya guardado


class TestEventoUI(Base):
    def test_evento_metrics_sin_texto(self):
        metrics.iniciar_turno()
        metrics.registrar_uso_llm(uso(10, 5, 90, 0))
        metrics.cerrar_turno("mi texto secreto", "respuesta secreta")
        tipo, datos = self.emit.call_args.args[0], self.emit.call_args.kwargs
        self.assertEqual(tipo, "metrics")
        self.assertNotIn("texto_usuario", datos)
        self.assertNotIn("respuesta", datos)
        self.assertNotIn("secret", json.dumps(datos))
        self.assertEqual(datos["cache_read"], 90)
        self.assertIn("total_ms", datos)


class TestReplay(Base):
    def escribir(self, turnos):
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            for t in turnos:
                f.write(json.dumps(t) + "\n")
            f.write("{linea corrupta\n")

    def test_resumen_p50_p95_y_cache(self):
        hoy = datetime.date(2026, 10, 15)
        turnos = [{"ts": f"2026-10-15T10:{i:02d}:00+02:00", "ruta": "claude" if i % 2 else "atajo",
                   "total_ms": (i + 1) * 100, "coste_usd": 0.001, "input": 100, "cache_read": 300,
                   "cache_creation": 0} for i in range(10)]
        turnos.append({"ts": "2026-10-10T09:00:00+02:00", "ruta": "local", "total_ms": 5000,
                       "coste_usd": 0, "input": 0, "cache_read": 0, "cache_creation": 0})
        turnos.append({"ts": "2026-09-01T09:00:00+02:00", "ruta": "dev", "total_ms": 1})  # fuera de 7 días
        self.escribir(turnos)
        cargados = metrics.cargar()
        self.assertEqual(len(cargados), 12)
        r = metrics.resumen(metrics.turnos_desde(cargados, 1, hoy))
        self.assertEqual((r["turnos"], r["p50_ms"], r["p95_ms"]), (10, 500, 1000))
        self.assertAlmostEqual(r["cache_ratio"], 0.75)
        self.assertAlmostEqual(r["coste_usd"], 0.01)
        self.assertEqual(r["rutas_pct"], {"atajo": 50.0, "claude": 50.0})
        semana = metrics.resumen(metrics.turnos_desde(cargados, 7, hoy))
        self.assertEqual((semana["turnos"], semana["p95_ms"]), (11, 5000))
        texto = replay.texto_resumen(cargados, hoy)
        self.assertIn("Hoy: 10 turnos · $0.0100 · p50 500 ms · p95 1000 ms · cache 75.0%", texto)
        self.assertIn("Últimos 7 días: 11 turnos", texto)

    def test_tabla_oculta_texto_privado(self):
        self.escribir([{"ts": "2026-10-15T10:00:00+02:00", "ruta": "claude", "tools": ["leer_emails"],
                        "privado": True, "total_ms": 900, "coste_usd": 0.002},
                       {"ts": "2026-10-15T10:01:00+02:00", "ruta": "atajo", "tools": ["hora"],
                        "privado": False, "total_ms": 50, "coste_usd": 0, "texto_usuario": "qué hora es"}])
        tabla = replay.tabla(metrics.cargar(), 10)
        self.assertIn("(privado)", tabla)
        self.assertIn("qué hora es", tabla)
        self.assertEqual(len(tabla.splitlines()), 3)


class TestGastoHoy(Base):
    def test_gasto_hoy_es_read_y_devuelve_texto(self):
        self.assertEqual(brain.TOOL_RISK["gasto_hoy"], "READ")
        self.assertEqual(metrics.gasto_hoy(), (True, "Hoy no hay turnos registrados."))
        for _ in range(2):
            metrics.iniciar_turno()
            metrics.cerrar_turno("x", "y")
        ok, msg = metrics.gasto_hoy()
        self.assertTrue(ok)
        self.assertIn("Hoy llevas 2 turnos", msg)
        res = brain._ejecutar("gasto_hoy", {}, {})
        self.assertTrue(res["ok"])
        self.assertIn("2 turnos", res["message"])

    def test_router_manda_gasto_a_claude(self):
        for texto in ("cuánto he gastado", "cuánto llevo gastado", "coste de hoy"):
            self.assertEqual(brain._clasificar_peticion(texto), "claude", texto)


if __name__ == "__main__":
    unittest.main()
