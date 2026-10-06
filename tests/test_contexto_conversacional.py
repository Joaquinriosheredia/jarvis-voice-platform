"""Contexto conversacional: los últimos pares (usuario → JARVIS) se pasan a
Claude como mensajes reales; las preguntas de seguimiento con conversación
reciente van a Claude (qwen no ve el historial); "quién es X" va a Claude
para que pueda buscar en Wikipedia. Sin red: Claude y Ollama mockeados."""
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import memoria  # noqa: E402

AHORA = datetime(2026, 10, 6, 16, 0)


def _h(texto, minutos_antes=0):
    return {"texto": texto, "fecha": (AHORA - timedelta(minutes=minutos_antes)).strftime("%Y-%m-%d %H:%M")}


def _mem(*entradas):
    return {"historial": list(entradas)}


def _resp_claude(texto="Respuesta de Claude"):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=texto)], usage=None)


def _skills():
    return {"sistema": MagicMock()}


HISTORIAL_BRUNO = _mem(
    _h("quién es Bruno Mars", 1),
    _h("Jarvis: Bruno Mars es un cantante estadounidense.", 1),
    _h("dónde nació", 0),  # pregunta actual: ya en el historial, sin respuesta
)


class TestObtenerMensajes(unittest.TestCase):
    def test_tres_pares_como_mensajes_reales(self):
        mem = _mem(_h("hola"), _h("Jarvis: Hola, señor."),
                   _h("qué hora es"), _h("Jarvis: Son las cuatro."),
                   _h("pon música"), _h("Jarvis: Reproduciendo."))
        self.assertEqual(memoria.obtener_mensajes(mem), [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "Hola, señor."},
            {"role": "user", "content": "qué hora es"},
            {"role": "assistant", "content": "Son las cuatro."},
            {"role": "user", "content": "pon música"},
            {"role": "assistant", "content": "Reproduciendo."},
        ])

    def test_solo_los_ultimos_tres_pares(self):
        entradas = []
        for i in range(5):
            entradas += [_h(f"pregunta {i}"), _h(f"Jarvis: respuesta {i}")]
        mensajes = memoria.obtener_mensajes(_mem(*entradas))
        self.assertEqual(len(mensajes), 6)
        self.assertEqual(mensajes[0]["content"], "pregunta 2")
        self.assertEqual(mensajes[-1]["content"], "respuesta 4")

    def test_pregunta_actual_sin_respuesta_se_excluye(self):
        mensajes = memoria.obtener_mensajes(HISTORIAL_BRUNO)
        self.assertEqual([m["content"] for m in mensajes],
                         ["quién es Bruno Mars", "Bruno Mars es un cantante estadounidense."])

    def test_entradas_sin_pareja_se_ignoran_y_alterna(self):
        mem = _mem(_h("Jarvis: Buenos días."),          # saludo inicial sin pregunta
                   _h("eh"), _h("abre youtube"),        # pregunta sin respuesta + otra
                   _h("Jarvis: Abriendo YouTube."))
        mensajes = memoria.obtener_mensajes(mem)
        self.assertEqual(mensajes, [{"role": "user", "content": "abre youtube"},
                                    {"role": "assistant", "content": "Abriendo YouTube."}])

    def test_historial_vacio(self):
        self.assertEqual(memoria.obtener_mensajes(_mem()), [])


class TestConversacionReciente(unittest.TestCase):
    def test_respuesta_hace_1_min(self):
        self.assertTrue(memoria.conversacion_reciente(HISTORIAL_BRUNO, ahora=AHORA))

    def test_respuesta_hace_10_min(self):
        mem = _mem(_h("hola", 10), _h("Jarvis: Hola.", 10), _h("qué es una API"))
        self.assertFalse(memoria.conversacion_reciente(mem, ahora=AHORA))

    def test_sin_respuestas_de_jarvis(self):
        self.assertFalse(memoria.conversacion_reciente(_mem(_h("hola")), ahora=AHORA))

    def test_fecha_corrupta(self):
        mem = _mem({"texto": "Jarvis: Hola.", "fecha": "ayer"})
        self.assertFalse(memoria.conversacion_reciente(mem, ahora=AHORA))


class TestQuienEs(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None

    def test_quien_es_bruno_mars_es_claude(self):
        for txt in ("quién es Bruno Mars", "¿Quién es Bruno Mars?", "oye, quién es Bruno Mars"):
            with self.subTest(txt=txt):
                self.assertEqual(brain._clasificar_peticion(txt), "claude")

    def test_otras_preguntas_siguen_local(self):
        self.assertEqual(brain._clasificar_peticion("qué es una API"), "local")
        self.assertEqual(brain._clasificar_peticion("quiénes son los Beatles"), "local")

    def test_quien_es_no_llama_a_ollama(self):
        with patch.object(brain.cliente.messages, "create", return_value=_resp_claude()) as create, \
             patch("brain.requests.post") as post, contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("quién es Bruno Mars", skills=_skills())
        post.assert_not_called()
        create.assert_called_once()


class TestContextoEnClaude(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_cerrar", False)

    def _pensar(self, texto, **kwargs):
        with patch.object(brain.cliente.messages, "create", return_value=_resp_claude()) as create, \
             patch("brain.requests.post") as post, contextlib.redirect_stdout(io.StringIO()) as out:
            brain.pensar(texto, skills=_skills(), **kwargs)
        return create, post, out.getvalue()

    def test_historial_con_3_pares_se_pasa_como_mensajes(self):
        mem = _mem(_h("hola"), _h("Jarvis: Hola, señor."),
                   _h("qué hora es"), _h("Jarvis: Son las cuatro."),
                   _h("pon música"), _h("Jarvis: Reproduciendo."),
                   _h("abre gmail"))
        create, _, _ = self._pensar("abre gmail", contexto=memoria.obtener_mensajes(mem))
        messages = create.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "user", "assistant", "user", "assistant", "user"])
        self.assertEqual(messages[-1], {"role": "user", "content": "abre gmail"})
        self.assertNotIn("[Contexto:", messages[-1]["content"])

    def test_donde_nacio_tras_bruno_mars_claude_ve_el_contexto(self):
        create, post, log = self._pensar(
            "dónde nació", contexto=memoria.obtener_mensajes(HISTORIAL_BRUNO),
            conversacion_reciente=memoria.conversacion_reciente(HISTORIAL_BRUNO, ahora=AHORA))
        post.assert_not_called()  # no va a qwen
        self.assertIn("seguimiento de conversación → claude", log)
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(messages[0], {"role": "user", "content": "quién es Bruno Mars"})
        self.assertIn("Bruno Mars", messages[1]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "dónde nació"})

    def test_donde_nacio_sin_conversacion_reciente_va_a_local(self):
        ok = MagicMock()
        ok.json.return_value = {"response": "No sé de quién me hablas."}
        with patch.object(brain.cliente.messages, "create") as create, \
             patch("brain.requests.post", return_value=ok) as post, \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("dónde nació", contexto=[], skills=_skills(), conversacion_reciente=False)
        post.assert_called_once()
        create.assert_not_called()

    def test_no_muta_la_lista_de_contexto(self):
        contexto = memoria.obtener_mensajes(HISTORIAL_BRUNO)
        copia = [dict(m) for m in contexto]
        self._pensar("dónde nació", contexto=contexto, conversacion_reciente=True)
        self.assertEqual(contexto, copia)

    def test_contexto_texto_plano_sigue_funcionando(self):
        create, _, _ = self._pensar("abre gmail", contexto="hola | Jarvis: Hola")
        messages = create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]["content"].startswith("[Contexto: hola | Jarvis: Hola]"))


if __name__ == "__main__":
    unittest.main()
