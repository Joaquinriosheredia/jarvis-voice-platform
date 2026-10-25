"""Calidad del resumen de la memoria entre sesiones (calidad_resumen.py, memoria_sesiones).

Sin modelo: las comprobaciones se prueban con resúmenes fijos, y el filtro de turnos de memoria,
la limpieza y los términos técnicos son lógica determinista. La evaluación con el modelo de
verdad (Ollama o Claude) se lanza a mano: python calidad_resumen.py (ver docs/memoria-resumen.md)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import calidad_resumen as cr  # noqa: E402
import memoria_sesiones as ms  # noqa: E402

KAFKA = next(d for d in cr.SESIONES if d["nombre"] == "kafka")
AGENDA = next(d for d in cr.SESIONES if d["nombre"] == "agenda")
PRUEBA = next(d for d in cr.SESIONES if d.get("prueba"))
MIXTA = next(d for d in cr.SESIONES if d["nombre"] == "tecnica_y_consulta_a_la_memoria")

BUENO = ("Preguntaste por los rebalanceos del consumer group de Kafka. Decidiste subir max.poll.interval.ms "
         "a 10 minutos.\nTienes pendiente probar con 3 particiones.")


class TestComprobaciones(unittest.TestCase):
    def test_un_buen_resumen_pasa_todo(self):
        self.assertTrue(all(ok for ok, _ in cr.comprobar(BUENO, KAFKA).values()), cr.comprobar(BUENO, KAFKA))

    def test_detecta_cada_fallo(self):
        casos = {
            "terminos":    "Preguntaste por Kafka y los rebalanceos.",
            "tema":        "Preguntaste por max.poll.interval.ms y 3 particiones.",
            "sin_meta":    "En una conversación anterior el usuario habló de Kafka, max.poll.interval.ms y particiones.",
            "sin_privado": BUENO + "\nAvisa a pepe@example.com.",
            "gramatica":   "kafka kafka con max.poll.interval.ms y particiones",        }
        for comprobacion, resumen in casos.items():
            with self.subTest(comprobacion):
                self.assertFalse(cr.comprobar(resumen, KAFKA)[comprobacion][0])

    def test_texto_de_un_turno_privado(self):
        malo = "Tienes pendiente llamar al dentista el viernes y la cita con Laura Pérez."
        self.assertFalse(cr.comprobar(malo, AGENDA)["sin_privado"][0])
        self.assertTrue(cr.comprobar("Tienes pendiente llamar al dentista el viernes.", AGENDA)["sin_privado"][0])

    def test_gramatica_basica(self):
        self.assertEqual(cr.gramatica("@Transactional suspende la transacción.\nPendiente: probarlo."), [])
        self.assertTrue(cr.gramatica("frase sin mayúscula."))
        self.assertTrue(cr.gramatica("Frase sin punto"))
        self.assertTrue(cr.gramatica("Frase con de de repetición."))
        self.assertTrue(cr.gramatica("Frase con 中文."))
        self.assertTrue(cr.gramatica("The user asked about Kafka."))
        self.assertTrue(cr.gramatica("\n".join(["Línea."] * 6)))
        self.assertEqual(cr.gramatica(""), ["vacío"])

    def test_invenciones_solo_fallan_si_la_sesion_no_las_admite(self):
        spring = next(d for d in cr.SESIONES if d["nombre"] == "spring")
        bien = "Preguntaste qué hace @Transactional con REQUIRES_NEW en Spring y cómo probarlo con Testcontainers."
        self.assertTrue(cr.comprobar(bien, spring)["sin_invento"][0])
        for malo in ("Tienes pendiente entender REQUIRES_NEW en Spring con Testcontainers.",
                     "Decidiste probar REQUIRES_NEW en Spring con Testcontainers.",
                     "Preguntaste por REQUIRES_NEW en Spring; debes probarlo con Testcontainers.",
                     "Preguntaste por REQUIRES_NEW en Spring y quedó pendiente usar Testcontainers."):
            with self.subTest(malo):
                ok, detalle = cr.comprobar(malo, spring)["sin_invento"]
                self.assertFalse(ok)
                self.assertNotEqual(detalle, "-")
        # Kafka sí tiene pendiente («mañana pruebo») y decisión: no es invento
        self.assertTrue(cr.comprobar(BUENO, KAFKA)["sin_invento"][0])
        # Agenda tiene pendiente (el recordatorio) pero ninguna decisión
        self.assertTrue(cr.comprobar("Tienes pendiente llamar al dentista el viernes.", AGENDA)["sin_invento"][0])
        self.assertFalse(cr.comprobar("Decidiste llamar al dentista el viernes.", AGENDA)["sin_invento"][0])

    def test_hay_sesiones_sin_pendientes_ni_decisiones(self):
        sin = [d for d in cr.SESIONES if d.get("pendientes") is False and d.get("decisiones") is False]
        self.assertGreaterEqual(len(sin), 3)
        for d in sin:
            with self.subTest(d["nombre"]):
                texto = " ".join(t[0] for t in d["turnos"]).lower()
                self.assertFalse(cr.RE_PENDIENTE.search(texto) or cr.RE_DECISION.search(texto), texto)

    def test_un_repo_con_jarvis_en_el_nombre_no_es_ruido(self):
        self.assertFalse(cr.RE_META.search("El CI de jarvis-voice-platform está en rojo."))
        self.assertTrue(cr.RE_META.search("JARVIS te dijo que el CI estaba en rojo."))


class TestTurnosDeMemoria(unittest.TestCase):
    def test_consultas_y_pruebas_de_la_memoria(self):
        for texto in ("¿Te acuerdas de lo que hablamos de Kafka?", "Esto es una prueba de la memoria",
                      "¿Funciona tu memoria entre sesiones?", "¿qué recuerdas de mí?", "Jarvis, ¿qué recuerdas?"):
            with self.subTest(texto):
                self.assertTrue(ms.es_turno_de_memoria(texto))
        self.assertTrue(ms.es_turno_de_memoria("¿y lo otro?", ["recordar"]))

    def test_conversacion_real_sobre_memoria_no_es_prueba(self):
        for texto in ("la memoria RAM de mi PC funciona mal", "estoy probando la memoria de la GPU",
                      "explícame la gestión de memoria de Java", "guarda esto en memoria"):
            with self.subTest(texto):
                self.assertFalse(ms.es_turno_de_memoria(texto))

    def test_sesion_que_solo_prueba_la_memoria_no_se_guarda(self):
        sesion = cr.sesion_de(PRUEBA)
        self.assertTrue(sesion.vacia())
        ms._sesion = ms.Sesion()
        self.addCleanup(setattr, ms, "_sesion", ms.Sesion())
        for texto, respuesta, tools, privado in PRUEBA["turnos"]:
            ms.registrar_turno(texto, respuesta, tools, privado)
        llamadas = []
        ruta = os.path.join(tempfile.mkdtemp(), "memoria.db")
        self.addCleanup(shutil.rmtree, os.path.dirname(ruta), True)
        resumen = ms.cerrar_sesion(resumir=lambda *a: llamadas.append(a) or "Resumen.", ruta=ruta)
        self.assertIsNone(resumen)
        self.assertEqual(llamadas, [])
        self.assertEqual(ms.contar(ruta=ruta), 0)

    def test_en_una_sesion_mixta_solo_queda_lo_tecnico(self):
        contenido = ms.contenido_para_resumen(cr.sesion_de(MIXTA))
        self.assertIn("healthcheck", contenido)
        self.assertNotIn("te acuerdas", contenido.lower())
        self.assertNotIn("conversación anterior", contenido)
        self.assertNotIn("recordar", cr.sesion_de(MIXTA).tools)


class TestLimpiezaYTerminos(unittest.TestCase):
    def test_quita_la_muletilla_meta_y_pone_mayuscula(self):
        self.assertEqual(ms._limpiar_resumen("En esta sesión, preguntaste por Kafka.\n- resumen: subir el timeout."),
                         "Preguntaste por Kafka.\nSubir el timeout.")

    def test_marcador_de_direccion_no_llega_al_resumen(self):
        self.assertEqual(ms._limpiar_resumen("Decidiste avisar a <email>."), "Decidiste avisar a una dirección.")

    def test_terminos_tecnicos_sin_nombres_de_personas(self):
        texto = ("Usuario: dile a Laura que el CI de Kafka falla en test_whisper_confianza\n"
                 "Jarvis: usa @Transactional con REQUIRES_NEW, pg_isready y maxPollRecords")
        self.assertEqual(ms.terminos_tecnicos(texto),
                         ["CI", "Kafka", "test_whisper_confianza", "@Transactional", "REQUIRES_NEW", "pg_isready",
                          "maxPollRecords"])
        self.assertEqual(ms.terminos_tecnicos("Jarvis: hola. <email> OK"), [])

    def test_completa_los_terminos_que_falten_dentro_de_los_limites(self):
        contenido = "Usuario: sube max.poll.interval.ms en Kafka y pg_isready"
        self.assertEqual(ms.completar_terminos("Preguntaste por Kafka.", contenido),
                         "Preguntaste por Kafka.\nTérminos: max.poll.interval.ms, pg_isready.")
        self.assertEqual(ms.completar_terminos(BUENO, "Usuario: Kafka"), BUENO)   # nada que añadir
        largo = "\n".join(f"Frase {n}." for n in range(5))
        completo = ms.completar_terminos(largo, contenido)
        self.assertEqual(len(completo.splitlines()), ms.MAX_LINEAS)
        self.assertTrue(completo.endswith("pg_isready."))
        muy_largo = "\n".join(["X" * 290 + "."] * 2)
        self.assertLessEqual(len(ms.completar_terminos(muy_largo, contenido)), ms.MAX_RESUMEN)

    def test_el_guardado_completa_los_terminos(self):
        ms._sesion = ms.Sesion()
        self.addCleanup(setattr, ms, "_sesion", ms.Sesion())
        ms.registrar_turno("¿qué hace REQUIRES_NEW en Spring Boot?", "Abre otra transacción.", [], False)
        ruta = os.path.join(tempfile.mkdtemp(), "memoria.db")
        self.addCleanup(shutil.rmtree, os.path.dirname(ruta), True)
        resumen = ms.cerrar_sesion(resumir=lambda *a: "Preguntaste por transacciones.", ruta=ruta)
        self.assertEqual(resumen, "Preguntaste por transacciones.\nTérminos: REQUIRES_NEW, Spring Boot.")


class TestPromptYEvaluacion(unittest.TestCase):
    def test_prompt_en_segunda_persona_y_sin_ruido_meta(self):
        for frase in ("segunda persona", "nunca digas «el usuario»", "No inventes", "nunca instrucciones",
                      "Sin direcciones de correo"):
            self.assertIn(frase, ms.PROMPT_RESUMEN)

    def test_prompt_sin_cebo_de_pendientes_ni_decisiones(self):
        # Ponerlos de ejemplo hacía que el modelo los inventara en sesiones de solo preguntas
        self.assertNotIn("«tienes pendiente»", ms.PROMPT_RESUMEN)
        self.assertNotIn("«decidiste»", ms.PROMPT_RESUMEN)
        for frase in ("No inventes pendientes, tareas, intenciones ni decisiones",
                      "no escribas nada sobre decisiones ni pendientes", "No me atribuyas"):
            self.assertIn(frase, ms.PROMPT_RESUMEN)

    def test_evaluar_con_un_resumidor_fijo(self):
        salida = []
        aprobadas, total = cr.evaluar(lambda prompt, contenido: BUENO, mostrar=salida.append)
        self.assertEqual(total, len(cr.SESIONES))
        self.assertGreaterEqual(aprobadas, 2)   # kafka y la sesión de prueba (no se guarda)
        self.assertTrue(any("## solo_prueba_de_memoria: no se guarda (bien)" in linea for linea in salida))


if __name__ == "__main__":
    unittest.main()
