"""Recuerdo sin depender del modelo, guardado robusto de la sesión y causa del fallo del tiempo.

1. "¿te acuerdas de...?" lo responde el sistema llamando a recordar; sin resultados dice
   "No tengo nada guardado sobre X" (sin pedir fecha ni tema). Si el modelo niega recordar
   algo del pasado sin haber consultado, consulta el sistema.
2. La sesión se guarda cada pocos turnos, tras un rato sin hablar y en cualquier cierre
   (adiós, consola cerrada, bandeja), en una sola fila y con las mismas reglas de privacidad.
3. El tiempo dice la causa concreta del fallo y reintenta una vez los transitorios.
Todo con mocks y una base temporal: sin micrófono, sin red y sin modelo real.
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
import briefing  # noqa: E402
import doctor  # noqa: E402
import memoria_sesiones as ms  # noqa: E402
import metrics  # noqa: E402
import ui_bridge  # noqa: E402
from skills import sistema  # noqa: E402

_JV = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail", "skills.google_auth")}
with patch.dict(sys.modules, _JV), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

DIRECCION = "ana.garcia@empresa.com"
PRIVADO   = "presupuesto confidencial de la reforma"

# Frases reales de logs/turnos.jsonl del 17/10 (transcripción de Whisper incluida)
FRASES_DEL_LOG = {
    "¿Te acuerdas de lo que hablábamos de Kafka?": "kafka",
    "¿Te acuerdas cuando te pregunté si no te acordabas del error del consumidor de Kafka?":
        "error del consumidor de kafka",
    "digo es que si no te acuerdas del error del consumidor de Cazca": "error del consumidor de cazca",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ruta = os.path.join(self.dir, "memoria_sesiones.db")
        for p in (patch.object(ms, "RUTA_DB", self.ruta), patch.object(ms, "_sesion", ms.Sesion()),
                  patch.object(ms, "CADA_TURNOS", aislar_memoria.CADA_TURNOS),
                  patch.object(ms, "INACTIVIDAD_S", aislar_memoria.INACTIVIDAD_S)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.salida = io.StringIO()

    def filas(self):
        if not os.path.exists(self.ruta):
            return []
        con = sqlite3.connect(self.ruta)
        try:
            return con.execute("SELECT rowid, fecha, resumen, temas, tools FROM sesiones").fetchall()
        finally:
            con.close()

    def bytes_db(self):
        with open(self.ruta, "rb") as f:
            return f.read()

    def pensar_sin_modelo(self, texto):
        with patch.object(brain.cliente.messages, "create", side_effect=AssertionError("llamó al modelo")), \
             patch.object(brain.cliente.messages, "stream", side_effect=AssertionError("llamó al modelo")), \
             contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills={})

    def pensar_con_modelo(self, texto, dice):
        respuesta = MagicMock(stop_reason="end_turn", usage=None, content=[MagicMock(text=dice)])
        with patch.object(brain.cliente.messages, "create", return_value=respuesta) as create, \
             patch.dict(os.environ, {"JARVIS_STREAMING": "0"}), contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills={}), create

    def tools_del_turno(self):
        return [t.get("nombre") for t in (brain.ultima_traza() or {}).get("tools_ejecutadas") or []]


# ── 1. Recordar sin depender del modelo ───────────────────────────
class TestAislamiento(unittest.TestCase):
    def test_los_tests_nunca_usan_la_base_real(self):
        self.assertNotIn("Jarvis-secrets", ms.RUTA_DB)
        self.assertTrue(ms.RUTA_DB.startswith(aislar_memoria.CARPETA))
        self.assertEqual((aislar_memoria.CADA_TURNOS, aislar_memoria.INACTIVIDAD_S), (4, 120))


class TestPreguntaRecuerdo(Base):
    def test_las_frases_de_hoy_se_reconocen_con_su_tema(self):
        for texto, tema in FRASES_DEL_LOG.items():
            self.assertEqual(ms.pregunta_recuerdo(texto), tema, texto)
            self.assertEqual(ms.orden(texto), ("recordar", tema), texto)

    def test_otras_formas(self):
        casos = {
            "¿Recuerdas lo de Docker?": "docker",
            "qué recuerdas de la entrevista": "entrevista",
            "lo que hablamos del portfolio, ¿te acuerdas?": "portfolio",
            "la semana pasada hablamos de la entrevista": "entrevista de la semana pasada",
            "qué hablamos ayer": "lo de ayer",
            "¿te acuerdas de lo que hablamos de kafka la semana pasada?": "kafka de la semana pasada",
            "Jarvis, ¿te acuerdas de Kafka, o no?": "kafka",
        }
        for texto, tema in casos.items():
            self.assertEqual(ms.pregunta_recuerdo(texto), tema, texto)

    def test_recordatorios_y_preguntas_normales_no_son_recuerdos(self):
        for texto in ("acuérdate de comprar pan", "recuérdame mañana llamar a Ana", "qué sabes de kafka",
                      "qué es kafka", "qué tiempo hizo ayer", "¿te acuerdas?", "hazme un resumen de lo que hablamos",
                      "olvida lo de kafka", "no recuerdo dónde dejé las llaves", ""):
            self.assertIsNone(ms.pregunta_recuerdo(texto), texto)

    def test_responde_lo_guardado_sin_modelo_y_por_la_tool_recordar(self):
        ms.guardar("Revisamos el consumidor de Kafka: faltaba el group id.", ["código"], [])
        with patch.object(metrics, "registrar_memoria") as reg, \
             patch.object(brain, "_gate", wraps=brain._gate) as gate:
            r = self.pensar_sin_modelo("¿Te acuerdas de lo que hablábamos de Kafka?")
        self.assertIn("group id", r)
        self.assertEqual(brain.ultima_traza()["router"], "memoria")
        self.assertEqual(self.tools_del_turno(), ["recordar"])
        self.assertEqual(gate.call_args.args[0], "recordar")       # pasa por el gate (READ)
        self.assertEqual(len(reg.call_args.args[0]), 1)

    def test_sin_nada_guardado_no_pide_fecha_ni_tema(self):
        for texto, tema in FRASES_DEL_LOG.items():
            r = self.pensar_sin_modelo(texto)
            self.assertEqual(r, f"No tengo nada guardado sobre {tema}.", texto)
            self.assertNotIn("?", r)
            for pide in ("cuándo", "fecha", "dime", "más detalles"):
                self.assertNotIn(pide, r.lower())

    def test_el_caso_de_hoy_encuentra_la_sesion_aunque_whisper_oyera_cazca(self):
        # La sesión de las 13:06 se guardó con "Cazca"; la pregunta por el error del consumidor la encuentra
        ms.guardar("El usuario preguntó por un error del consumidor de Cazca.", ["conversación"], [])
        r = self.pensar_sin_modelo(
            "¿Te acuerdas cuando te pregunté si no te acordabas del error del consumidor de Kafka?")
        self.assertIn("consumidor de Cazca", r)

    def test_si_falla_la_base_lo_dice_sin_inventar(self):
        with patch.object(ms, "recordar", side_effect=sqlite3.OperationalError("x")):
            r = self.pensar_sin_modelo("¿te acuerdas de lo de kafka?")
        self.assertIn("No puedo leer la memoria", r)


class TestElModeloNoNiegaSinConsultar(Base):
    def test_no_recuerdo_sin_llamar_a_recordar_consulta_el_sistema(self):
        ms.guardar("Hablamos del despliegue del viernes.", ["conversación"], [])
        r, _ = self.pensar_con_modelo("y lo que te dije el otro día del despliegue",
                                      "No recuerdo nada de eso, ¿cuándo fue?")
        self.assertIn("despliegue del viernes", r)
        self.assertIn("recordar", self.tools_del_turno())

    def test_sin_nada_guardado_responde_sin_pedir_fecha(self):
        r, _ = self.pensar_con_modelo("y lo que te dije el otro día del despliegue",
                                      "No tengo ese registro. Dime cuándo fue y lo busco.")
        self.assertEqual(r, "No tengo nada guardado sobre despliegue.")
        self.assertNotIn("cuándo", r)

    def test_no_toca_respuestas_que_no_hablan_del_pasado(self):
        r, _ = self.pensar_con_modelo("cómo se llamaba la película de Tom Hanks en una isla",
                                      "No recuerdo el título exacto, pero creo que es Náufrago.")
        self.assertIn("Náufrago", r)
        self.assertNotIn("recordar", self.tools_del_turno())

    def test_el_system_prompt_lo_exige(self):
        self.assertIn("antes de decir que no recuerdas", brain.SYSTEM)
        self.assertIn("no pidas la fecha", brain.SYSTEM)


# ── 2. Guardado robusto ───────────────────────────────────────────
class TestGuardadoPeriodico(Base):
    def turnos(self, n, privado=False, texto="hablamos de kafka y del group id"):
        for i in range(n):
            ms.registrar_turno(f"{texto} {i}", f"respuesta {i}", [], privado)

    def test_cada_pocos_turnos_y_tras_inactividad(self):
        self.assertFalse(ms.debe_guardar())
        self.turnos(1)
        self.assertFalse(ms.debe_guardar())
        self.assertTrue(ms.debe_guardar(ahora=time.monotonic() + ms.INACTIVIDAD_S + 1))
        self.turnos(ms.CADA_TURNOS - 1)
        self.assertTrue(ms.debe_guardar())

    def test_una_sola_fila_por_sesion(self):
        resumir = MagicMock(side_effect=["Hablamos de Kafka.", "Kafka y el group id.", "Kafka, group id y offsets."])
        self.turnos(4)
        self.assertEqual(ms.guardar_parcial(resumir=resumir), "Hablamos de Kafka.")
        self.assertFalse(ms.debe_guardar())
        self.turnos(4, texto="y los offsets")
        ms.guardar_parcial(resumir=resumir)
        self.assertEqual(len(self.filas()), 1)
        self.assertEqual(self.filas()[0][2], "Kafka y el group id.")
        # Al cerrar sin turnos nuevos no se vuelve a resumir ni se duplica
        self.assertEqual(ms.cerrar_sesion(resumir=resumir), "Kafka y el group id.")
        self.assertEqual(resumir.call_count, 2)
        self.assertEqual(len(self.filas()), 1)
        self.assertTrue(ms._sesion.vacia())
        # La siguiente sesión es otra fila
        self.turnos(1, texto="docker")
        ms.cerrar_sesion(resumir=None)
        self.assertEqual(len(self.filas()), 2)

    def test_cerrar_con_turnos_nuevos_actualiza_la_misma_fila(self):
        self.turnos(4)
        ms.guardar_parcial(resumir=lambda p, t: "Primera parte.")
        self.turnos(1, texto="y luego docker")
        ms.cerrar_sesion(resumir=lambda p, t: "Kafka y después Docker.")
        self.assertEqual([f[2] for f in self.filas()], ["Kafka y después Docker."])

    def test_privacidad_igual_en_los_guardados_periodicos(self):
        ms.registrar_turno(f"lee el correo de {DIRECCION}: {PRIVADO}", f"Es de {DIRECCION}", ["leer_emails"], True)
        ms.registrar_turno(f"escribe a {DIRECCION} sobre kafka", f"Vale, {DIRECCION}", [], False)
        ms.registrar_turno("y lo de kafka", "Sí.", [], False)
        ms.registrar_turno("vale", "Bien.", [], False)
        vistos = []
        ms.guardar_parcial(resumir=lambda p, t: vistos.append(t) or f"Copio {DIRECCION} y {PRIVADO}.")
        self.assertNotIn(DIRECCION, vistos[0])
        self.assertNotIn(PRIVADO, vistos[0])
        datos = self.bytes_db()
        self.assertNotIn(DIRECCION.encode(), datos)
        self.assertNotIn(b"empresa.com", datos)
        self.assertIn(b"<email>", datos)

    def test_un_guardado_a_la_vez(self):
        self.turnos(4)
        with ms._lock_guardar:
            self.assertIsNone(ms.guardar_parcial(resumir=lambda p, t: "x"))
        self.assertEqual(self.filas(), [])

    def test_olvida_todo_durante_un_guardado_no_vuelve_a_guardar_nada(self):
        self.turnos(4)
        def resumir_y_olvidar(prompt, texto):
            ms.olvidar_todo()
            return "Hablamos de Kafka."
        self.assertIsNone(ms.guardar_parcial(resumir=resumir_y_olvidar))
        self.assertEqual(self.filas(), [])
        self.assertTrue(ms._sesion.vacia())

    def test_olvida_lo_de_x_tambien_borra_la_fila_de_la_sesion_en_curso(self):
        ms.registrar_turno("explícame kafka", "Es un log.", [], False)
        ms.registrar_turno("y el portfolio", "Bien.", [], False)
        ms.guardar_parcial(resumir=lambda p, t: "Un sistema de mensajería y el portfolio.")  # sin "kafka"
        ms.olvidar("kafka")
        self.assertEqual(self.filas(), [])
        self.assertNotIn(b"mensajer", self.bytes_db())
        self.assertTrue(ms.debe_guardar(ahora=time.monotonic() + ms.INACTIVIDAD_S + 1))
        vistos = []
        ms.cerrar_sesion(resumir=lambda p, t: vistos.append(t) or "El portfolio.")
        self.assertNotIn("kafka", vistos[0].lower())
        self.assertEqual([f[2] for f in self.filas()], ["El portfolio."])

    def test_si_la_fila_desaparece_se_crea_otra(self):
        rowid = ms.guardar("uno", ["x"], [])
        ms.olvidar_todo()
        nuevo = ms.guardar("dos", ["x"], [], rowid=rowid)
        self.assertEqual([f[2] for f in self.filas()], ["dos"])
        self.assertIsNotNone(nuevo)


class TestCierreRapido(Base):
    def test_sin_modelo_conserva_el_ultimo_resumen(self):
        for i in range(4):
            ms.registrar_turno(f"kafka {i}", "ok", [], False)
        ms.guardar_parcial(resumir=lambda p, t: "Hablamos de Kafka.")
        ms.registrar_turno("abre la agenda", "Hoy nada.", ["ver_agenda"], True)
        with patch.object(brain, "resumir_sesion", side_effect=AssertionError("usó el modelo")):
            ms.cierre_rapido()
        filas = self.filas()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0][2], "Hablamos de Kafka.")
        self.assertIn("agenda", filas[0][3])
        self.assertTrue(ms._sesion.vacia())

    def test_sin_resumen_previo_guarda_los_temas(self):
        ms.registrar_turno("pon música", "Vale.", ["reproducir_musica"], False)
        ms.cierre_rapido()
        self.assertEqual(self.filas()[0][2], "Temas: música.")

    def test_no_espera_indefinidamente_a_otro_guardado(self):
        ms.registrar_turno("kafka", "ok", [], False)
        with ms._lock_guardar:
            inicio = time.monotonic()
            self.assertIsNone(ms.cierre_rapido(espera=0.2))
        self.assertLess(time.monotonic() - inicio, 2)


class TestCiclosDeCierreEnJarvis(Base):
    def setUp(self):
        super().setUp()
        jarvis._cerrado.clear()
        jarvis._parar_vigilancia.clear()
        self.addCleanup(jarvis._cerrado.clear)
        self.addCleanup(jarvis._parar_vigilancia.clear)

    def traza(self, router="claude", tools=()):
        return {"router": router, "tools_ejecutadas": [{"nombre": t} for t in tools]}

    def test_cada_turno_comprueba_si_toca_guardar_en_otro_hilo(self):
        hilos = []
        with patch.object(jarvis.brain, "ultima_traza", return_value=self.traza()), \
             patch.object(jarvis.tts_elevenlabs, "turno_privado", return_value=False), \
             patch.object(jarvis, "memoria_sesiones", ms), \
             patch.object(jarvis.threading, "Thread", side_effect=lambda **kw: hilos.append(kw) or MagicMock()):
            for i in range(ms.CADA_TURNOS):
                jarvis._registrar_en_memoria_sesiones(f"hablamos de kafka {i}", "ok")
        self.assertEqual(len(hilos), 1)
        self.assertIs(hilos[0]["target"], jarvis._guardado_periodico)
        self.assertTrue(hilos[0]["daemon"])

    def test_guardado_periodico_usa_el_resumidor_barato_y_nunca_lanza(self):
        with patch.object(jarvis.memoria_sesiones, "guardar_parcial", return_value="x") as g, \
             contextlib.redirect_stdout(self.salida):
            jarvis._guardado_periodico()
        g.assert_called_once_with(resumir=brain.resumir_sesion)
        with patch.object(jarvis.memoria_sesiones, "guardar_parcial", side_effect=sqlite3.Error), \
             contextlib.redirect_stdout(self.salida):
            jarvis._guardado_periodico()

    def test_vigilancia_de_inactividad(self):
        llamadas = []
        jarvis._parar_vigilancia.clear()
        with patch.object(jarvis, "VIGILANCIA_S", 0.01), \
             patch.object(jarvis, "_guardar_si_toca", side_effect=lambda: llamadas.append(1) or (
                 len(llamadas) >= 3 and jarvis._parar_vigilancia.set())):
            jarvis._vigilar_inactividad()
        self.assertEqual(len(llamadas), 3)

    def test_cerrar_la_consola_guarda_sin_modelo(self):
        for evento in (1, 2, 5, 6):   # Ctrl+Break, ventana cerrada, cierre de sesión, apagado
            jarvis._cerrado.clear()
            with patch.object(jarvis.memoria_sesiones, "cierre_rapido", return_value="x") as rapido, \
                 patch.object(jarvis, "mem") as mem, contextlib.redirect_stdout(self.salida):
                self.assertFalse(jarvis._al_evento_consola(evento))
            rapido.assert_called_once_with()
            mem.guardar.assert_called_once()

    def test_ctrl_c_sigue_su_camino_normal(self):
        with patch.object(jarvis.memoria_sesiones, "cierre_rapido") as rapido:
            self.assertFalse(jarvis._al_evento_consola(0))
        rapido.assert_not_called()

    def test_un_solo_cierre_aunque_lleguen_varios(self):
        with patch.object(jarvis, "_cerrar_memoria_sesiones") as cerrar, patch.object(jarvis, "musica"), \
             patch.object(jarvis, "mem"), patch.object(jarvis.memoria_sesiones, "cierre_rapido") as rapido, \
             contextlib.redirect_stdout(self.salida):
            jarvis.cleanup()
            jarvis.cleanup()
            jarvis._al_evento_consola(2)
        cerrar.assert_called_once_with()
        rapido.assert_not_called()
        self.assertTrue(jarvis._parar_vigilancia.is_set())

    def test_cerrar_desde_la_bandeja_guarda_y_termina(self):
        class HiloInmediato:
            def __init__(self, target, **kw):
                self.target = target
            def start(self):
                self.target()
        with patch.object(jarvis.threading, "Thread", HiloInmediato), \
             patch.object(jarvis, "cleanup") as cleanup, patch.object(jarvis.os, "_exit") as salir, \
             contextlib.redirect_stdout(self.salida):
            jarvis._cerrar_desde_ui()
        cleanup.assert_called_once_with()
        salir.assert_called_once_with(0)

    def test_main_instala_los_cierres(self):
        with open(os.path.join(RAIZ, "jarvis.py"), encoding="utf-8") as f:
            codigo = f.read()
        principal = codigo[codigo.index("def main():"):]
        for llamada in ("_instalar_cierre_consola()", "ui_bridge.al_cerrar = _cerrar_desde_ui",
                        "target=_vigilar_inactividad"):
            self.assertIn(llamada, principal)


class TestCierreDesdeLaUI(unittest.TestCase):
    def tearDown(self):
        ui_bridge.al_cerrar = None
        ui_bridge.al_parar = None

    def test_cerrar_llama_a_al_cerrar_y_stop_sigue_igual(self):
        ui_bridge.al_cerrar, ui_bridge.al_parar = MagicMock(), MagicMock()
        ui_bridge.recibir(json.dumps({"type": "cerrar"}))
        ui_bridge.al_cerrar.assert_called_once_with()
        ui_bridge.al_parar.assert_not_called()
        ui_bridge.recibir(json.dumps({"type": "stop", "origen": "teclado"}))
        ui_bridge.al_parar.assert_called_once_with("teclado")
        for otro in ('{"type": "confirmar"}', "cerrar", "[]", '{"type": "cerrar"' + " " * 300 + "}"):
            ui_bridge.recibir(otro)
        self.assertEqual(ui_bridge.al_cerrar.call_count, 1)

    def test_sin_jarvis_conectado_no_lanza(self):
        ui_bridge.al_cerrar = None
        ui_bridge.recibir(json.dumps({"type": "cerrar"}))

    def test_la_app_pide_el_cierre_antes_de_forzar(self):
        def leer(nombre):
            with open(os.path.join(RAIZ, "app", nombre), encoding="utf-8") as f:
                return f.read()
        main, preload, renderer = leer("main.js"), leer("preload.js"), leer("renderer.js")
        self.assertIn("webContents.send('cerrar')", main)
        self.assertIn("Wait-Process", main)
        self.assertIn("ESPERA_CIERRE_S", main)
        self.assertIn("alCerrar", preload)
        self.assertIn("type: 'cerrar'", renderer)


# ── 3. El tiempo: causa concreta y un reintento ───────────────────
def _resp(codigo, datos=None):
    r = MagicMock(status_code=codigo)
    if isinstance(datos, Exception):
        r.json.side_effect = datos
    else:
        r.json.return_value = datos or {}
    return r


BIEN = _resp(200, {"current_weather": {"temperature": 21.4, "windspeed": 8.0, "winddirection": 270,
                                       "weathercode": 1}})


class TestTiempo(unittest.TestCase):
    def setUp(self):
        p = patch.object(sistema.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"JARVIS_CIUDAD": "Villaprueba", "JARVIS_LATITUD": "37.5",
                                    "JARVIS_LONGITUD": "-5.25"})
        p.start()
        self.addCleanup(p.stop)

    def consultar(self, *respuestas):
        with patch.object(sistema.req, "get", side_effect=list(respuestas)) as get:
            return sistema.consultar_tiempo(), get.call_count

    def test_bien(self):
        (ok, texto, causa), n = self.consultar(BIEN)
        self.assertTrue(ok)
        self.assertIsNone(causa)
        self.assertEqual(n, 1)
        self.assertIn("21.4 grados", texto)
        self.assertIn("poniente", texto)

    def test_hoy_503_saturado_y_reintento_que_sale_bien(self):
        (ok, texto, _), n = self.consultar(_resp(503, {"reason": "The service is overloaded"}), BIEN)
        self.assertTrue(ok)
        self.assertEqual(n, 2)
        self.sleep.assert_called_once_with(sistema.PAUSA_TIEMPO)

    def test_causas_concretas_tras_un_solo_reintento(self):
        casos = [
            ((sistema.req.exceptions.ConnectionError("dns"),) * 2, "No hay conexión para el tiempo", sistema.SIN_RED),
            ((sistema.req.exceptions.ConnectTimeout("t"),) * 2, "El servicio del tiempo no responde",
             sistema.SIN_RESPUESTA),
            ((_resp(503),) * 2, "El servicio del tiempo está saturado ahora mismo", sistema.SATURADO),
            ((_resp(502),) * 2, "El servicio del tiempo está caído ahora mismo", sistema.CAIDO),
        ]
        for respuestas, mensaje, causa in casos:
            (ok, texto, c), n = self.consultar(*respuestas)
            self.assertEqual((ok, texto, c, n), (False, mensaje, causa, 2), causa)

    def test_lo_no_transitorio_no_se_reintenta(self):
        for respuesta, causa in ((_resp(400), sistema.RECHAZADO), (_resp(429), sistema.LIMITADO),
                                 (_resp(200, ValueError("no json")), sistema.DATOS),
                                 (_resp(200, {"otra": 1}), sistema.DATOS)):
            (ok, _, c), n = self.consultar(respuesta)
            self.assertEqual((ok, c, n), (False, causa, 1), causa)

    def test_tiempo_local_nunca_lanza_ni_da_el_mensaje_generico(self):
        with patch.object(sistema.req, "get", side_effect=sistema.req.exceptions.ConnectionError("x")):
            self.assertEqual(sistema.tiempo_local(), "No hay conexión para el tiempo")
        with patch.object(sistema, "consultar_tiempo", side_effect=RuntimeError):
            self.assertNotIn("No puedo obtener el tiempo ahora mismo", sistema.tiempo_local())

    def test_el_briefing_dice_la_causa(self):
        with patch.object(sistema.req, "get", side_effect=sistema.req.exceptions.ConnectionError("x")):
            texto = briefing.generar({"tiempo": briefing.fuentes(sistema, lambda: "", list, list)["tiempo"]}, hora=9)
        self.assertIn("No hay conexión para el tiempo.", texto)
        self.assertNotIn("No puedo obtener", texto)

    def test_doctor(self):
        normal = dict((i, f) for i, _, f in doctor.COMPROBACIONES)
        vivo = dict((i, f) for i, _, f in doctor.COMPROBACIONES_LIVE)
        r = normal["tiempo"]()
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("no verificado", r["detalle"])
        with patch.object(sistema.req, "get", side_effect=AssertionError("sin --live no hay red")):
            normal["tiempo"]()
        with patch.object(sistema.req, "get", return_value=BIEN):
            self.assertEqual(vivo["live_tiempo"]()["estado"], doctor.OK)
        with patch.object(sistema.req, "get", return_value=_resp(503)):
            r = vivo["live_tiempo"]()
        self.assertEqual(r["estado"], doctor.AVISO)
        self.assertIn("saturado", r["detalle"])
        self.assertIn("Open-Meteo", r["accion"])


if __name__ == "__main__":
    unittest.main()
