"""Memoria entre sesiones (memoria_sesiones.py): qué se guarda, qué nunca, recordar,
olvidar, retención, "olvida todo" con el gate, métricas, doctor y ciclo de vida en jarvis.
Todo con mocks y una base SQLite temporal: sin micrófono, sin red y sin modelo real.
"""
import atexit
import contextlib
import datetime
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)
import rutas  # noqa: E402

import brain  # noqa: E402
import doctor  # noqa: E402
import memoria_sesiones as ms  # noqa: E402
import metrics  # noqa: E402

_JV = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail", "skills.google_auth")}
with patch.dict(sys.modules, _JV), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

DIRECCION   = "ana.garcia@empresa.com"
DICTADA     = "pepe arroba gmail punto com"
SECRETO     = "sk-live-4f9a8b7c6d5e4f3a2b1c"
PRIVADO     = "presupuesto confidencial de la reforma"
FECHA       = datetime.datetime(2026, 10, 17, 10, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ruta = os.path.join(self.dir, "memoria_sesiones.db")
        for p in (patch.object(ms, "RUTA_DB", self.ruta), patch.object(ms, "_sesion", ms.Sesion())):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.salida = io.StringIO()

    def bytes_db(self):
        with open(self.ruta, "rb") as f:
            return f.read()

    def filas(self):
        con = sqlite3.connect(self.ruta)
        try:
            return con.execute("SELECT fecha, resumen, temas, tools FROM sesiones").fetchall()
        finally:
            con.close()

    def sesion(self, resumen, temas=("código",), tools=("git_log",), dias=0):
        ms.guardar(resumen, list(temas), list(tools), fecha=FECHA - datetime.timedelta(days=dias))

    def pensar(self, texto):
        with patch.object(brain.cliente.messages, "create", side_effect=AssertionError("llamó al modelo")), \
             patch.object(brain.cliente.messages, "stream", side_effect=AssertionError("llamó al modelo")), \
             contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills={})


# ── Qué se guarda y qué nunca ─────────────────────────────────────
class TestNuncaSeGuarda(Base):
    def cerrar(self, resumir):
        with contextlib.redirect_stdout(self.salida):
            return ms.cerrar_sesion(resumir=resumir, fecha=FECHA)

    def test_nunca_una_direccion_aunque_el_modelo_la_copie(self):
        ms.registrar_turno(f"mándale el informe a {DIRECCION}", f"Enviado a {DIRECCION}.", [], False)
        ms.registrar_turno(f"y otro a {DICTADA}", "Vale.", [], False)
        visto = []
        resumen = self.cerrar(lambda instr, texto: visto.append(texto) or f"Informe enviado a {DIRECCION}.")
        self.assertNotIn(DIRECCION, visto[0])          # el modelo ya recibe el texto enmascarado
        self.assertNotIn("arroba", visto[0])
        self.assertIn("<email>", resumen)              # y lo que devuelve se vuelve a enmascarar
        datos = self.bytes_db()
        for prohibido in (DIRECCION, "ana.garcia", "empresa.com", "pepe arroba"):
            self.assertNotIn(prohibido.encode(), datos, prohibido)
        self.assertIsNone(re.search(rb"[\w.+-]+@[\w-]+\.[a-z]{2,}", datos))

    def test_un_turno_privado_solo_deja_tema_y_tools(self):
        ms.registrar_turno(f"lee el correo del {PRIVADO}", f"El {PRIVADO} sube a 20.000 euros.",
                           ["leer_emails"], True)
        visto = []
        self.cerrar(lambda instr, texto: visto.append(texto) or "x")
        self.assertEqual(visto, [])                    # sin turnos no privados no se llama al modelo
        (fecha, resumen, temas, tools), = self.filas()
        self.assertEqual((resumen, temas, tools), ("Temas: correo.", "correo", "leer_emails"))
        self.assertNotIn(b"presupuesto", self.bytes_db())
        self.assertNotIn(b"20.000", self.bytes_db())

    def test_privado_y_normal_en_la_misma_sesion(self):
        ms.registrar_turno(f"qué dice el correo del {PRIVADO}", f"El {PRIVADO}...", ["leer_email"], True)
        ms.registrar_turno("explícame el error de kafka del consumidor", "Falta el group id.", [], False)
        visto = []
        self.cerrar(lambda instr, texto: visto.append(texto) or "Kafka: faltaba el group id.")
        self.assertIn("kafka", visto[0])
        self.assertNotIn("presupuesto", visto[0])
        self.assertIn("Otros temas tratados (sin detalle): correo", visto[0])
        self.assertNotIn(b"presupuesto", self.bytes_db())

    def test_nunca_secretos(self):
        ms.registrar_turno(f"guarda que la clave es API_KEY = \"{SECRETO}\"", f"Clave {SECRETO} anotada.", [], False)
        self.cerrar(lambda instr, texto: f"Clave anotada: {SECRETO}")
        self.assertNotIn(SECRETO.encode(), self.bytes_db())
        self.assertIn("SECRETO-REDACTADO", self.filas()[0][1])

    def test_nunca_contenido_de_archivos_diffs_ni_tests(self):
        for tool in ("leer_archivo", "git_diff", "ejecutar_tests"):
            ms._sesion.vaciar()
            ms.registrar_turno(f"usa {tool} en jarvis", "def funcion_secreta_interna(): ...", [tool], False)
            visto = []
            self.cerrar(lambda instr, texto: visto.append(texto) or "Revisión de código.")
            self.assertIn(tool.split("_")[0], visto[0], tool)          # la pregunta sí
            self.assertNotIn("funcion_secreta_interna", visto[0], tool)  # la respuesta no
        self.assertNotIn(b"funcion_secreta_interna", self.bytes_db())

    def test_si_el_resumen_falla_guarda_solo_los_temas(self):
        ms.registrar_turno("pon música de los beatles", "Pongo a los Beatles.", ["reproducir_musica"], False)
        def falla(instr, texto):
            raise RuntimeError("sin red")
        resumen = self.cerrar(falla)
        self.assertEqual(resumen, "Temas: música.")
        self.assertEqual(self.filas()[0][1:], ("Temas: música.", "música", "reproducir_musica"))

    def test_sin_resumidor_tambien_temas_y_resumen_de_cinco_lineas_como_mucho(self):
        ms.registrar_turno("hola", "Hola.", [], False)
        self.assertEqual(self.cerrar(None), "Temas: conversación.")
        ms.registrar_turno("hola", "Hola.", [], False)
        resumen = self.cerrar(lambda i, t: "\n".join(f"- línea {n}" for n in range(9)))
        self.assertEqual(resumen.splitlines(), [f"Línea {n}" for n in range(5)])   # con mayúscula inicial

    def test_sesion_vacia_no_guarda_nada(self):
        self.assertIsNone(self.cerrar(lambda i, t: "x"))
        self.assertFalse(os.path.exists(self.ruta))

    def test_solo_guardar_crea_la_base(self):
        self.assertEqual(ms.recordar("kafka"), [])
        self.assertEqual(ms.olvidar("kafka"), 0)
        self.assertEqual(ms.olvidar_todo(), 0)
        self.assertEqual(ms.purgar_caducados(), 0)
        self.assertEqual(ms.contar(), 0)
        self.assertFalse(os.path.exists(self.ruta))
        ms.registrar_turno("hola", "Hola.", [], False)
        self.cerrar(None)
        self.assertTrue(os.path.exists(self.ruta))

    def test_la_base_vive_fuera_del_repo_y_la_ruta_es_configurable(self):
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(ms.RUTA_DB.startswith(self.dir))
        with patch.dict(os.environ, {"JARVIS_MEMORIA_DB": os.path.join(self.dir, "otra.db")}):
            import importlib
            recargado = importlib.reload(ms)
            self.assertEqual(recargado.RUTA_DB, os.path.join(self.dir, "otra.db"))
        with patch.dict(os.environ):
            os.environ.pop("JARVIS_MEMORIA_DB", None)
            importlib.reload(ms)
        self.assertEqual(ms.RUTA_DB, rutas.en_secretos("memoria_sesiones.db"))
        self.assertFalse(os.path.normcase(ms.RUTA_DB).startswith(os.path.normcase(raiz) + os.sep))


# ── Recordar ──────────────────────────────────────────────────────
class TestRecordar(Base):
    def setUp(self):
        super().setUp()
        self.sesion("Revisamos el consumidor de Kafka: faltaba el group id.", dias=8)
        self.sesion("Preparamos la entrevista con Acme para el viernes.", ("agenda",), ("ver_agenda",), dias=1)
        self.sesion("Ajustamos el portfolio y la sección de Java.", dias=3)

    def test_encuentra_con_fecha(self):
        r = ms.recordar("¿te acuerdas de lo de kafka?", hoy=FECHA)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["fecha"][:10], "2026-10-09")
        self.assertIn("Kafka", r[0]["resumen"])
        self.assertIn("El 9 de octubre:", ms.texto_resultados(r))

    def test_sin_tildes_ni_plurales(self):
        self.assertEqual(len(ms.recordar("entrevistas", hoy=FECHA)), 1)
        self.assertEqual(len(ms.recordar("sección java", hoy=FECHA)), 1)

    def test_no_encuentra(self):
        self.assertEqual(ms.recordar("recetas de lentejas", hoy=FECHA), [])
        self.assertEqual(ms.recordar("¿te acuerdas?", hoy=FECHA), [])   # sin tema ni fecha
        self.assertEqual(ms.texto_resultados([], "lentejas"), "No tengo nada guardado sobre lentejas.")

    def test_por_fecha(self):
        r = ms.recordar("qué hicimos ayer", hoy=FECHA)
        self.assertEqual([x["resumen"][:10] for x in r], ["Preparamos"])
        r = ms.recordar("lo de kafka de la semana pasada", hoy=FECHA)
        self.assertEqual(len(r), 1)
        self.assertEqual(ms.recordar("kafka de ayer", hoy=FECHA), [])

    def test_como_mucho_tres(self):
        for n in range(5):
            self.sesion(f"Más kafka, sesión {n}.")
        self.assertEqual(len(ms.recordar("kafka", hoy=FECHA)), 3)

    def test_consulta_con_comillas_o_operadores_no_rompe_fts(self):
        for raro in ('kafka" OR 1=1 --', "NEAR(kafka)", "kafka AND NOT java", "*", "'; DROP TABLE sesiones;"):
            ms.recordar(raro, hoy=FECHA)
        self.assertEqual(len(self.filas()), 3)

    def test_tool_recordar_es_read_y_registra_metricas(self):
        self.assertEqual(brain.TOOL_RISK["recordar"], "READ")
        with patch.object(metrics, "registrar_memoria") as reg, contextlib.redirect_stdout(self.salida):
            res = brain._ejecutar("recordar", {"consulta": "kafka"}, {}, "te acuerdas de lo de kafka")
            vacio = brain._ejecutar("recordar", {"consulta": "lentejas"}, {}, "te acuerdas de las lentejas")
        self.assertTrue(res["ok"])
        self.assertIn("Kafka", res["message"])
        self.assertIn("No tengo nada guardado sobre", vacio["message"])
        self.assertEqual([len(c.args[0]) for c in reg.call_args_list], [1, 0])

    def test_no_se_inyecta_en_cada_turno(self):
        respuesta = MagicMock(stop_reason="end_turn", usage=None, content=[MagicMock(text="Hola.")])
        with patch.object(brain.cliente.messages, "create", return_value=respuesta) as create, \
             patch.dict(os.environ, {"JARVIS_STREAMING": "0"}), contextlib.redirect_stdout(self.salida):
            brain.pensar("hola, qué tal", skills={})
        llamada = str(create.call_args)
        for de_la_memoria in ("group id", "Acme", "Preparamos", "Revisamos"):
            self.assertNotIn(de_la_memoria, llamada)

    def test_el_router_manda_el_pasado_a_claude(self):
        for texto in ("qué hablamos ayer", "te acuerdas de lo de kafka", "qué te dije la semana pasada"):
            self.assertEqual(brain._clasificar_peticion(texto), "claude", texto)


# ── Órdenes de voz y olvidar ──────────────────────────────────────
class TestRecordarAproximado(Base):
    """Si la búsqueda exacta no encuentra nada, se prueba una por parecido fonético sobre
    temas y resúmenes, y el resultado se marca como aproximado."""

    def setUp(self):
        super().setUp()
        self.sesion("Revisamos el consumidor de Kafka: faltaba el group id.", ("kafka",), dias=8)
        self.sesion("Desplegamos en Kubernetes el servicio de pagos.", ("kubernetes",), dias=2)

    def test_whisper_escribe_cazca_y_se_encuentra_kafka(self):
        self.assertEqual(ms.recordar("cazca"), [])
        r = ms.buscar_recuerdos("¿te acuerdas de lo de cazca?")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["parecido"], "kafka")
        texto = ms.texto_resultados(r, "cazca")
        self.assertTrue(texto.startswith("No tengo nada guardado exactamente sobre cazca, pero esto se parece a "
                                         "«kafka» (resultado aproximado): El 9 de octubre: Revisamos"), texto)

    def test_cubernetes(self):
        r = ms.buscar_recuerdos("cubernetes")
        self.assertEqual([x["parecido"] for x in r], ["kubernetes"])

    def test_lo_exacto_no_se_marca_como_aproximado(self):
        r = ms.buscar_recuerdos("kafka")
        self.assertEqual(len(r), 1)
        self.assertNotIn("parecido", r[0])
        self.assertNotIn("aproximado", ms.texto_resultados(r, "kafka"))

    def test_lo_que_no_se_parece_no_sale(self):
        for consulta in ("recetas de lentejas", "casa", "la semana pasada", "eso"):
            with self.subTest(consulta):
                self.assertEqual(ms.recordar_aproximado(consulta), [])

    def test_sin_base_no_falla(self):
        os.remove(self.ruta)
        self.assertEqual(ms.buscar_recuerdos("cazca"), [])

    def test_clave_fonetica(self):
        self.assertEqual(ms._clave_fonetica("Cazca"), "kaska")
        self.assertEqual(ms._clave_fonetica("Kafka"), "kafka")
        self.assertEqual(ms._clave_fonetica("quiero"), "kiero")

    def test_por_voz_sin_modelo(self):
        respuesta = self.pensar("¿te acuerdas de lo que hablamos de cazca?")
        self.assertIn("esto se parece a «kafka»", respuesta)
        self.assertIn("Revisamos el consumidor de Kafka", respuesta)


class TestOrdenes(Base):
    def test_reconoce_solo_la_frase_entera(self):
        casos = {
            "Olvida lo de Kafka": ("olvidar", "kafka"),
            "Jarvis, olvida todo lo de la entrevista": ("olvidar", "entrevista"),
            "olvida todo": ("olvidar_todo", None),
            "Jarvis, olvida todo, por favor": ("olvidar_todo", None),
            "¿Qué recuerdas de kafka?": ("recordar", "kafka"),
            "qué recuerdas sobre el portfolio": ("recordar", "portfolio"),
        }
        for texto, esperado in casos.items():
            self.assertEqual(ms.orden(texto), esperado, texto)
        for texto in ("olvida", "no olvides lo de kafka", "olvida todo lo que te he dicho de mi jefe y llama a Ana",
                      "recuerdas lo de kafka", "qué es kafka", "olvídate", ""):
            self.assertNotEqual(ms.orden(texto) and ms.orden(texto)[0], "olvidar_todo", texto)
        self.assertIsNone(ms.orden("no olvides lo de kafka"))
        # Cualquier pregunta por algo hablado antes es recordar (sin modelo)
        self.assertEqual(ms.orden("recuerdas lo de kafka"), ("recordar", "kafka"))

    def test_olvidar_borra_del_indice_y_del_archivo(self):
        self.sesion("Revisamos el consumidor de Kafka.")
        self.sesion("Ajustamos el portfolio.")
        self.assertEqual(ms.olvidar("kafka"), 1)
        self.assertEqual(ms.recordar("kafka"), [])
        con = sqlite3.connect(self.ruta)
        try:
            self.assertEqual(con.execute("SELECT count(*) FROM sesiones WHERE sesiones MATCH 'kafka'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM sesiones").fetchone()[0], 1)
        finally:
            con.close()
        self.assertNotIn(b"Kafka", self.bytes_db())
        self.assertNotIn(b"kafka", self.bytes_db())
        self.assertEqual(ms.olvidar("kafka"), 0)

    def test_olvidar_tambien_lo_de_la_sesion_en_curso(self):
        ms.registrar_turno("explícame kafka", "Kafka es un log distribuido.", [], False)
        ms.registrar_turno("y el portfolio", "Está bien.", [], False)
        ms.olvidar("kafka")
        self.assertNotIn("afka", ms._sesion.texto())
        self.assertIn("portfolio", ms._sesion.texto())

    def test_voz_olvidar_y_que_recuerdas_sin_modelo(self):
        self.sesion("Revisamos el consumidor de Kafka.")
        with patch.object(metrics, "registrar_memoria") as reg:
            self.assertIn("Kafka", self.pensar("qué recuerdas de kafka"))
            self.assertEqual(brain.ultima_traza()["router"], "memoria")
            self.assertEqual(self.pensar("olvida lo de kafka"), "Hecho: he olvidado 1 sesión sobre kafka.")
            self.assertEqual(self.pensar("qué recuerdas de kafka"), "No tengo nada guardado sobre kafka.")
        self.assertEqual([len(c.args[0]) for c in reg.call_args_list], [1, 0])

    def test_las_ordenes_de_memoria_no_se_guardan_en_la_sesion(self):
        self.sesion("Revisamos el consumidor de Kafka.")
        with patch.object(jarvis, "memoria_sesiones", ms):
            self.pensar("olvida lo de kafka")
            jarvis._registrar_en_memoria_sesiones("olvida lo de kafka", "Hecho.")
        self.assertTrue(ms._sesion.vacia())


# ── "Olvida todo" pasa por el gate ────────────────────────────────
class TestOlvidaTodo(Base):
    def setUp(self):
        super().setUp()
        self.sesion("Revisamos el consumidor de Kafka.")
        self.sesion("Ajustamos el portfolio.")

    def test_exige_confirmacion_por_voz(self):
        self.assertEqual(brain.TOOL_RISK["olvidar_todo"], "DESTRUCTIVE")
        respuesta = self.pensar("olvida todo")
        self.assertIn("confirmo borrado de memoria", respuesta)
        self.assertIn("2 sesiones", respuesta)
        self.assertEqual(ms.contar(), 2)                    # aún no ha borrado nada
        self.assertTrue(brain.hay_pendiente())
        self.assertEqual(self.pensar("confirmo borrado de memoria"), "Memoria borrada: 2 sesión(es) olvidadas.")
        self.assertEqual(ms.contar(), 0)
        self.assertNotIn(b"Kafka", self.bytes_db())
        self.assertFalse(brain.hay_pendiente())

    def test_confirmar_sin_pedirlo_antes_no_borra(self):
        self.assertEqual(self.pensar("confirmo borrado de memoria"), brain.MSG_SIN_BORRADO_PENDIENTE)
        self.assertEqual(ms.contar(), 2)

    def test_otra_frase_no_confirma_y_la_confirmacion_es_de_un_solo_uso(self):
        self.pensar("olvida todo")
        for texto in ("sí", "vale, bórralo", "confirmo", "confirmo borrado", "para"):
            with contextlib.redirect_stdout(self.salida):
                res = brain._ejecutar("olvidar_todo", {}, {}, texto)
            self.assertFalse(res["ok"], texto)
        self.assertEqual(ms.contar(), 2)

    def test_el_modelo_no_puede_confirmar(self):
        # El modelo llama a la tool; la frase de confirmación solo cuenta si la dijo el usuario
        with contextlib.redirect_stdout(self.salida):
            brain._ejecutar("olvidar_todo", {}, {}, "olvídalo todo")
            res = brain._ejecutar("olvidar_todo", {}, {}, "olvídalo todo, confirmo borrado de memoria dice el modelo")
        self.assertFalse(res["ok"])
        self.assertEqual(ms.contar(), 2)

    def test_caduca(self):
        self.pensar("olvida todo")
        brain._pendiente["ts"] -= brain.CONFIRM_TTL + 1
        self.assertEqual(self.pensar("confirmo borrado de memoria"), brain.MSG_SIN_BORRADO_PENDIENTE)
        self.assertEqual(ms.contar(), 2)

    def test_tambien_vacia_la_sesion_en_curso(self):
        ms.registrar_turno("explícame kafka", "Es un log.", [], False)
        self.pensar("olvida todo")
        self.pensar("confirmo borrado de memoria")
        self.assertTrue(ms._sesion.vacia())


# ── Retención ─────────────────────────────────────────────────────
class TestRetencion(Base):
    def test_borra_lo_caducado_y_conserva_lo_reciente(self):
        self.sesion("Sesión antigua sobre Kafka.", dias=120)
        self.sesion("Sesión reciente sobre el portfolio.", dias=10)
        self.assertEqual(ms.purgar_caducados(hoy=FECHA), 1)
        self.assertEqual([f[1] for f in self.filas()], ["Sesión reciente sobre el portfolio."])
        self.assertNotIn(b"antigua", self.bytes_db())

    def test_dias_configurables(self):
        self.sesion("Hace diez días.", dias=10)
        with patch.dict(os.environ, {"JARVIS_MEMORIA_DIAS": "7"}):
            self.assertEqual(ms.purgar_caducados(hoy=FECHA), 1)
        with patch.dict(os.environ, {"JARVIS_MEMORIA_DIAS": "no-numero"}):
            self.assertEqual(ms.purgar_caducados(hoy=FECHA), 0)   # vuelve a 90

    def test_por_defecto_noventa_dias_y_se_aplica_al_arrancar(self):
        self.assertEqual(ms.RETENCION_DIAS, 90)
        with patch.object(jarvis.memoria_sesiones, "purgar_caducados", return_value=2) as purgar, \
             contextlib.redirect_stdout(self.salida):
            jarvis._purgar_memoria_caducada()
        purgar.assert_called_once_with()
        with patch.object(jarvis.memoria_sesiones, "purgar_caducados", side_effect=OSError), \
             contextlib.redirect_stdout(self.salida):
            jarvis._purgar_memoria_caducada()                 # un fallo no impide arrancar


# ── Ciclo de vida en jarvis.py ────────────────────────────────────
class TestJarvis(Base):
    def traza(self, router="claude", tools=()):
        return {"router": router, "tools_ejecutadas": [{"nombre": t} for t in tools]}

    def test_registra_el_turno_con_su_privacidad(self):
        with patch.object(jarvis.brain, "ultima_traza", return_value=self.traza(tools=["leer_emails"])), \
             patch.object(jarvis.tts_elevenlabs, "turno_privado", return_value=True), \
             patch.object(jarvis.memoria_sesiones, "registrar_turno") as reg:
            jarvis._registrar_en_memoria_sesiones("lee mi correo", "Tienes 2.")
        reg.assert_called_once_with("lee mi correo", "Tienes 2.", ["leer_emails"], True)

    def test_no_registra_ordenes_de_parada_ni_de_memoria(self):
        for router in ("parada", "apagado", "memoria"):
            with patch.object(jarvis.brain, "ultima_traza", return_value=self.traza(router)), \
                 patch.object(jarvis.memoria_sesiones, "registrar_turno") as reg:
                jarvis._registrar_en_memoria_sesiones("para", None)
            reg.assert_not_called()

    def test_al_cerrar_resume_con_el_modelo_barato_y_nunca_bloquea(self):
        with patch.object(jarvis.memoria_sesiones, "cerrar_sesion", return_value="x") as cerrar, \
             contextlib.redirect_stdout(self.salida):
            jarvis._cerrar_memoria_sesiones()
        cerrar.assert_called_once_with(resumir=brain.resumir_sesion)
        with patch.object(jarvis.memoria_sesiones, "cerrar_sesion", side_effect=sqlite3.Error), \
             contextlib.redirect_stdout(self.salida):
            jarvis._cerrar_memoria_sesiones()

    def test_cleanup_guarda_la_sesion(self):
        jarvis._cerrado.clear()
        self.addCleanup(jarvis._cerrado.clear)
        self.addCleanup(jarvis._parar_vigilancia.clear)
        with patch.object(jarvis, "_cerrar_memoria_sesiones") as cerrar, patch.object(jarvis, "musica"), \
             patch.object(jarvis, "mem"), contextlib.redirect_stdout(self.salida):
            jarvis.cleanup()
        cerrar.assert_called_once_with()

    def test_resumir_sesion_ollama_y_si_falla_haiku(self):
        ok = MagicMock()
        ok.json.return_value = {"response": "Hablamos de Kafka."}
        with patch.object(brain.requests, "post", return_value=ok):
            self.assertEqual(brain.resumir_sesion("instr", "texto"), "Hablamos de Kafka.")
        haiku = MagicMock(content=[MagicMock(text="Resumen de Haiku.")])
        with patch.object(brain.requests, "post", side_effect=OSError), \
             patch.object(brain.cliente.messages, "create", return_value=haiku) as create, \
             contextlib.redirect_stdout(self.salida):
            self.assertEqual(brain.resumir_sesion("instr", "texto"), "Resumen de Haiku.")
        self.assertEqual(create.call_args.kwargs["model"], brain.CLAUDE_MODEL)


# ── Métricas y doctor ─────────────────────────────────────────────
class TestMetricasYDoctor(Base):
    def test_metricas_cuentan_consultas_sin_texto(self):
        metrics.iniciar_turno()
        self.addCleanup(metrics.descartar_turno)
        metrics.registrar_memoria([{"fecha": "x", "resumen": "Kafka"}])
        metrics.registrar_memoria([])
        reg = metrics._registro(metrics._turno, None, None, True)
        self.assertEqual((reg["memoria_consultas"], reg["memoria_con_resultados"]), (2, 1))
        self.assertNotIn("Kafka", str(reg))

    def test_el_log_de_turnos_enmascara_direcciones(self):
        metrics.iniciar_turno()
        self.addCleanup(metrics.descartar_turno)
        with patch.dict(os.environ, {"JARVIS_LOG_TEXTO": "1"}):
            reg = metrics._registro(metrics._turno, f"escribe a {DICTADA}", f"Es {DIRECCION}", False)
        self.assertNotIn(DIRECCION, str(reg))
        self.assertNotIn("arroba", str(reg))
        self.assertIn("<email>", reg["respuesta"])

    def test_doctor(self):
        self.assertIn("memoria", [i for i, _, _ in doctor.COMPROBACIONES])
        self.assertEqual(doctor.check_memoria()["estado"], doctor.AVISO)   # aún no existe
        self.assertFalse(os.path.exists(self.ruta))                         # y el doctor no la crea
        self.sesion("Una sesión.")
        r = doctor.check_memoria()
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("1 sesión(es)", r["detalle"])
        with open(self.ruta, "wb") as f:
            f.write(b"esto no es una base sqlite" * 100)
        self.assertEqual(doctor.check_memoria()["estado"], doctor.ERROR)


if __name__ == "__main__":
    unittest.main()
