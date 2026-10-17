"""Historial de conversación (memoria.json): nunca guarda direcciones ni el texto de
un turno privado, y el modelo sigue teniendo contexto para continuar. El destinatario
dictado antes lo resuelve el sistema ("dictada"), no el historial.

Turnos completos de jarvis._turno con brain.pensar real, el modelo y Gmail mockeados,
y memoria en un dict (memoria.guardar no escribe en disco)."""
import atexit
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
import limpiar_memoria  # noqa: E402
import memoria as memoria_real  # noqa: E402
from skills import direccion, gmail  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail", "skills.google_auth")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

PROPIA  = "ana@example.com"
DICTADA = "pepe.garcia@correo.es"
CONF_E  = "confirmo envío"
RE_EMAIL = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+|arroba", re.I)


def respuestas(*llamadas, final="Hecho."):
    """Respuestas del modelo: estas llamadas a tools y luego el texto final."""
    r = [NS(stop_reason="tool_use", usage=None,
            content=[NS(type="tool_use", id=f"t{i}", name=n, input=dict(p))])
         for i, (n, p) in enumerate(llamadas)]
    return r + [NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text=final)])]


class Base(unittest.TestCase):
    def setUp(self):
        for nombre in ("_pendiente", "_dictada"):
            setattr(brain, nombre, None)
            self.addCleanup(setattr, brain, nombre, None)
        gmail._mi_email = None
        self.addCleanup(setattr, gmail, "_mi_email", None)
        self.service = MagicMock()
        self.service.users().getProfile.return_value.execute.return_value = {"emailAddress": PROPIA}
        for p in (patch.object(brain, "DESTINATARIOS_PERMITIDOS", []),
                  patch.object(gmail, "get_service", return_value=self.service),
                  patch.object(memoria_real, "guardar"),                 # nunca a disco
                  patch.object(jarvis, "mem", memoria_real),
                  patch.object(jarvis, "memoria", memoria_real._memoria_vacia()),
                  patch.dict(jarvis.SKILLS, {"gmail": gmail}),
                  patch.object(brain, "al_texto_parcial", None),
                  patch.object(brain, "_enrutar", return_value=None)):   # todo a Claude (mockeado)
            p.start()
            self.addCleanup(p.stop)
        self.notas = [{"texto": "comprar pan", "fecha": "2026-10-16 10:00"}]
        jarvis.memoria["notas"] = list(self.notas)
        jarvis.audio.reset_mock()
        self.contextos = []

    @property
    def send(self):
        return self.service.users().messages().send

    def turno(self, texto, *llamadas, final="Hecho."):
        """Un turno de voz completo; guarda el contexto que recibió el modelo."""
        crear = MagicMock(side_effect=respuestas(*llamadas, final=final))

        def create(**kw):
            self.contextos.append([dict(m) for m in kw["messages"][:-1]])
            return crear(**kw)
        with patch.object(brain.cliente.messages, "create", side_effect=create), \
             contextlib.redirect_stdout(io.StringIO()):
            return jarvis._turno(texto)

    @property
    def historial(self):
        return [h["texto"] for h in jarvis.memoria["historial"]]

    def sin_direcciones(self):
        volcado = json.dumps(jarvis.memoria, ensure_ascii=False)
        self.assertIsNone(RE_EMAIL.search(volcado), "el historial guarda una dirección")
        self.assertEqual(jarvis.memoria["notas"], self.notas)


# ── Nunca una dirección en el historial ──────────────────────────
class TestSinDirecciones(Base):
    def test_respuesta_normal_con_una_direccion(self):
        self.turno("¿cómo se escribe el correo de soporte?", final=f"Es soporte@empresa.es, señor.")
        self.assertEqual(self.historial, ["¿cómo se escribe el correo de soporte?",
                                          "Jarvis: Es <email>, señor."])
        self.sin_direcciones()

    def test_envio_a_mi_propia_direccion_y_confirmacion(self):
        params = {"destinatario": "yo", "asunto": "prueba", "cuerpo": "hola"}
        self.turno("mándame un correo a mi dirección con el asunto prueba", ("enviar_email", params))
        self.assertIn(PROPIA, jarvis.audio.hablar.call_args.args[0])   # el anuncio sí lo dice en voz
        self.turno(CONF_E, ("enviar_email", params), final="Enviado.")
        self.send.assert_called_once()
        self.sin_direcciones()

    def test_direccion_dictada_y_confirmacion(self):
        dictado = "mándalo a pepe punto garcia arroba correo punto es"
        params = {"destinatario": DICTADA, "asunto": "hola", "cuerpo": "qué tal"}
        self.turno(dictado, ("enviar_email", params))
        self.turno(CONF_E, ("enviar_email", params), final="Enviado.")
        self.send.assert_called_once()
        self.sin_direcciones()

    def test_un_turno_cancelado_guarda_solo_la_pregunta(self):
        with patch.object(jarvis.brain, "generacion_cancelada", return_value=True):
            self.turno("cuéntame un chiste largo", final="No digo nada.")
        self.assertEqual(self.historial, ["cuéntame un chiste largo"])

    def test_una_direccion_en_la_pregunta_hace_el_turno_privado(self):
        self.turno("¿es válido soporte@empresa.es?", final="Sí, tiene buen formato.")
        self.assertEqual(self.historial[0], jarvis.TURNO_PRIVADO)
        self.sin_direcciones()

    def test_el_historial_que_ve_el_modelo_tampoco(self):
        self.turno("dime mi correo", ("get_mi_email", {}), final=f"Tu correo es {PROPIA}.")
        self.turno("¿y qué tiempo hace?", final="Soleado.")
        self.assertIsNone(RE_EMAIL.search(json.dumps(self.contextos[-1], ensure_ascii=False)))


# ── Un turno privado se guarda sin texto ─────────────────────────
class TestTurnoPrivado(Base):
    def test_lectura_de_correo(self):
        self.turno("léeme los correos de hoy", ("leer_emails", {}), final="Tienes uno de Ana sobre la cena.")
        self.assertEqual(self.historial, [jarvis.TURNO_PRIVADO,
                                          "Jarvis: [Respuesta privada no guardada. Tema: correo. Tools: leer_emails]"])
        self.assertNotIn("Ana", json.dumps(jarvis.memoria, ensure_ascii=False))

    def test_agenda(self):
        self.turno("¿qué tengo hoy?", ("ver_agenda", {}), final="A las diez, dentista.")
        self.assertEqual(self.historial[-1], "Jarvis: [Respuesta privada no guardada. Tema: agenda. Tools: ver_agenda]")
        self.assertNotIn("dentista", json.dumps(jarvis.memoria, ensure_ascii=False))

    def test_direccion_dictada_sin_tools(self):
        self.turno("es pepe punto garcia arroba correo punto es", final="¿Qué asunto le pongo?")
        self.assertEqual(self.historial, [jarvis.TURNO_PRIVADO,
                                          "Jarvis: [Respuesta privada no guardada. Tema: datos privados]"])

    def test_anuncio_pendiente_de_confirmar(self):
        params = {"destinatario": "yo", "asunto": "cena", "cuerpo": "a las nueve"}
        self.turno("mándame un correo con el asunto cena", ("enviar_email", params))
        self.assertEqual(self.historial[-1], "Jarvis: [Respuesta privada no guardada. Tema: correo. "
                                             "Tools: enviar_email. Pendiente de confirmar por voz: enviar_email]")
        self.assertNotIn("cena", json.dumps(jarvis.memoria, ensure_ascii=False))

    def test_un_turno_normal_guarda_el_texto(self):
        self.turno("¿cuánto es dos más dos?", final="Cuatro.")
        self.assertEqual(self.historial, ["¿cuánto es dos más dos?", "Jarvis: Cuatro."])


# ── El modelo sigue teniendo contexto ────────────────────────────
class TestContexto(Base):
    def test_recibe_los_turnos_anteriores_alternados(self):
        self.turno("¿cuánto es dos más dos?", final="Cuatro.")
        self.turno("léeme los correos", ("leer_emails", {}), final="Tienes uno de Ana.")
        self.turno("¿y el de ayer?", final="No hay más.")
        contexto = self.contextos[-1]
        self.assertEqual([m["role"] for m in contexto], ["user", "assistant"] * 2)
        self.assertEqual(contexto[0]["content"], "¿cuánto es dos más dos?")
        self.assertEqual(contexto[1]["content"], "Cuatro.")
        self.assertEqual(contexto[2]["content"], jarvis.TURNO_PRIVADO)
        self.assertIn("Tema: correo. Tools: leer_emails", contexto[3]["content"])
        self.assertTrue(jarvis.mem.conversacion_reciente(jarvis.memoria))  # sigue siendo un seguimiento

    def test_la_misma_direccion_en_el_turno_siguiente_la_pone_el_sistema(self):
        self.turno("es pepe punto garcia arroba correo punto es", final="¿Qué asunto le pongo?")
        # El modelo no ve la dirección en el historial: usa "dictada" y el sistema la recupera
        self.assertNotIn(DICTADA, json.dumps(self.contextos, ensure_ascii=False))
        params = {"destinatario": "dictada", "asunto": "hola", "cuerpo": "qué tal"}
        self.turno("el asunto es hola y el texto qué tal", ("enviar_email", params))
        anuncio = jarvis.audio.hablar.call_args.args[0]
        self.assertIn(f"Vas a enviar un email a {DICTADA}. Se deletrea", anuncio)
        self.send.assert_not_called()                               # el gate sigue pidiendo confirmación
        self.turno(CONF_E, ("enviar_email", params), final="Enviado.")
        self.send.assert_called_once()
        self.sin_direcciones()

    def test_el_marcador_email_del_historial_tambien_se_resuelve(self):
        brain._registrar_dictada("a pepe punto garcia arroba correo punto es")
        with contextlib.redirect_stdout(io.StringIO()):
            res = brain._ejecutar("enviar_email", {"destinatario": "<email>", "asunto": "a", "cuerpo": "b"},
                                  {"gmail": gmail}, "envíalo a la misma")
        self.assertIn(CONF_E, res["message"])
        self.assertEqual(brain.pendiente_info()["params"]["destinatario"], DICTADA)

    def test_sin_dictada_reciente_no_se_inventa(self):
        brain._registrar_dictada("a pepe punto garcia arroba correo punto es")
        with patch("brain.time.monotonic", return_value=brain._dictada["ts"] + brain.DICTADA_TTL + 1), \
             contextlib.redirect_stdout(io.StringIO()):
            res = brain._ejecutar("enviar_email", {"destinatario": "la misma", "asunto": "a", "cuerpo": "b"},
                                  {"gmail": gmail}, "envíalo a la misma")
        self.assertEqual(res, brain._err(brain.MSG_SIN_DICTADA, "sin_direccion_dictada"))
        self.assertIsNone(brain._pendiente)
        self.send.assert_not_called()


# ── limpiar_memoria.py ────────────────────────────────────────────
class TestLimpiarMemoria(unittest.TestCase):
    MEMORIA = {
        "ultima_sesion": "2026-10-16 11:13", "documento_activo": None, "tareas_pendientes": [],
        "notas": [{"texto": "llamar a pepe.garcia@correo.es", "fecha": "2026-10-16 10:00"},
                  {"texto": "comprar pan", "fecha": "2026-10-16 10:05"}],
        "historial": [{"texto": "mándalo a pepearrobacorreo punto es", "fecha": "2026-10-16 10:16"},
                      {"texto": f"Jarvis: Enviado a {PROPIA} y a {DICTADA}.", "fecha": "2026-10-16 10:16"},
                      {"texto": "qué hora es", "fecha": "2026-10-16 10:17"},
                      {"texto": "Jarvis: Vas a enviar un email a <email>.", "fecha": "2026-10-16 10:18"}],
        "rachas_completadas": [],
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = tmp.name
        os.makedirs(os.path.join(self.raiz, "logs"))
        for nombre in limpiar_memoria.ARCHIVOS:
            with open(os.path.join(self.raiz, nombre), "w", encoding="utf-8") as f:
                json.dump(self.MEMORIA, f, ensure_ascii=False, indent=2)

    def leer(self, nombre):
        with open(os.path.join(self.raiz, nombre), encoding="utf-8") as f:
            return f.read()

    def main(self, respuesta, abierto=False):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = limpiar_memoria.main(entrada=lambda _: respuesta, raiz=self.raiz, abierto=lambda: abierto)
        self.salida = salida.getvalue()
        return codigo

    def test_recuento_sin_escribir_ni_mostrar_contenido(self):
        antes = self.leer("memoria.json")
        self.assertEqual(self.main("n"), 1)
        self.assertEqual(self.leer("memoria.json"), antes)
        self.assertIn("memoria.json: 3 dirección(es) → <email> en 2 entrada(s)", self.salida)
        self.assertIn("memoria.json.bak: 3 dirección(es)", self.salida)
        for privado in (PROPIA, DICTADA, "pepearroba"):
            self.assertNotIn(privado, self.salida)

    def test_aplica_con_copia_y_notas_intactas(self):
        originales = {n: self.leer(n) for n in limpiar_memoria.ARCHIVOS}
        self.assertEqual(self.main("s"), 0)
        copias = [d for d in os.listdir(os.path.join(self.raiz, "logs")) if d.startswith("backup-memoria-")]
        self.assertEqual(len(copias), 1)
        for nombre, original in originales.items():
            self.assertEqual(self.leer(os.path.join("logs", copias[0], nombre)), original)
            nueva = json.loads(self.leer(nombre))
            self.assertEqual(nueva["notas"], self.MEMORIA["notas"])        # intactas, aun con una dirección
            self.assertEqual({k: v for k, v in nueva.items() if k != "historial"},
                             {k: v for k, v in self.MEMORIA.items() if k != "historial"})
            textos = [h["texto"] for h in nueva["historial"]]
            self.assertEqual(textos[1], "Jarvis: Enviado a <email> y a <email>.")
            self.assertNotIn("arroba", textos[0])
            self.assertEqual(textos[2:], ["qué hora es", "Jarvis: Vas a enviar un email a <email>."])
            self.assertEqual([h["fecha"] for h in nueva["historial"]],
                             [h["fecha"] for h in self.MEMORIA["historial"]])
        self.assertEqual(self.main("s"), 0)
        self.assertIn("Nada que limpiar", self.salida)

    def test_con_jarvis_abierto_no_hace_nada(self):
        antes = self.leer("memoria.json")
        self.assertEqual(self.main("s", abierto=True), 2)
        self.assertEqual(self.leer("memoria.json"), antes)
        self.assertEqual(os.listdir(os.path.join(self.raiz, "logs")), [])

    def test_memoria_cargable_tras_limpiar(self):
        self.main("s")
        with patch.object(memoria_real, "MEMORIA_FILE", os.path.join(self.raiz, "memoria.json")):
            mem = memoria_real.cargar()
        self.assertEqual(len(mem["historial"]), 4)
        self.assertEqual(len(memoria_real.obtener_mensajes(mem)), 4)


if __name__ == "__main__":
    unittest.main()
