"""Streaming de Claude + voz por frases. Todo con mocks: stream falso, sin red, sin audio.

audio se importa real (con sounddevice/piper/pycaw/scipy sustituidos) para probar
hablar_frases; jarvis se importa con audio sustituido para probar _decir_respuesta."""
import atexit
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import frases  # noqa: E402
import metrics  # noqa: E402
import tts_elevenlabs  # noqa: E402

_HW = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw",
                                "scipy", "scipy.io", "scipy.io.wavfile")}
with patch.dict(sys.modules, _HW), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402

import brain  # noqa: E402

_JV = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _JV), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)


# ── Stream falso con la interfaz de MessageStream ─────────────────
def texto(t):
    return NS(type="text", text=t)


def inicio_tool():
    return NS(type="content_block_start", content_block=NS(type="tool_use"))


def mensaje(stop, *bloques):
    return NS(stop_reason=stop, content=list(bloques), usage=None)


def bloque_texto(t):
    return NS(type="text", text=t)


def bloque_tool(nombre, input=None):
    return NS(type="tool_use", name=nombre, id="t1", input=input or {})


class StreamFalso:
    def __init__(self, eventos, final, fallo_al_abrir=False, fallo_tras=None):
        self.eventos, self.final = eventos, final
        self.fallo_al_abrir, self.fallo_tras = fallo_al_abrir, fallo_tras
        self.cerrado = False

    def __enter__(self):
        if self.fallo_al_abrir:
            raise ConnectionError("sin red")
        return self

    def __exit__(self, *exc):
        self.cerrado = True
        return False

    def __iter__(self):
        for i, e in enumerate(self.eventos):
            if self.fallo_tras is not None and i == self.fallo_tras:
                raise ConnectionError("corte a mitad")
            yield e

    def get_final_message(self):
        return self.final


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.parciales = []
        brain.al_texto_parcial = self.parciales.append
        self.addCleanup(setattr, brain, "al_texto_parcial", None)
        entorno = {k: v for k, v in os.environ.items() if k not in ("JARVIS_STREAMING", "JARVIS_TTS")}
        p = patch.dict(os.environ, entorno, clear=True)
        p.start()
        self.addCleanup(p.stop)
        for objetivo in ("brain._enrutar", "brain._log_traza", "brain._print_seguro"):
            p = patch(objetivo, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        tts_elevenlabs.nuevo_turno()
        self.addCleanup(tts_elevenlabs.nuevo_turno)
        self.addCleanup(metrics.descartar_turno)

    def pensar(self, streams, create=None, texto="hola"):
        with patch.object(brain.cliente.messages, "stream", side_effect=streams) as stream, \
             patch.object(brain.cliente.messages, "create", side_effect=create or []) as crear:
            respuesta = brain.pensar(texto, skills={"x": 1})
        return respuesta, stream, crear


# ── 1. Troceador ──────────────────────────────────────────────────
class TestTroceador(unittest.TestCase):
    def test_frases_normales(self):
        self.assertEqual(frases.trocear("Son las diez de la mañana. Hace sol en Sevilla hoy. ¿Algo más por hoy?"),
                         ["Son las diez de la mañana.", "Hace sol en Sevilla hoy.", "¿Algo más por hoy?"])

    def test_decimales_y_abreviaturas_no_cortan(self):
        self.assertEqual(frases.trocear("La versión 3.5 cuesta 2.99 euros al mes. Usa p.ej. la rama main, etc. y listo."),
                         ["La versión 3.5 cuesta 2.99 euros al mes.", "Usa p.ej. la rama main, etc. y listo."])
        self.assertEqual(frases.trocear("Habla con el Sr. Gómez mañana temprano."),
                         ["Habla con el Sr. Gómez mañana temprano."])

    def test_trozo_corto_se_junta_con_el_siguiente(self):
        self.assertEqual(frases.trocear("Vale. Lo apunto ahora mismo en tus tareas."),
                         ["Vale. Lo apunto ahora mismo en tus tareas."])

    def test_texto_sin_puntuacion_final_y_saltos_de_linea(self):
        self.assertEqual(frases.trocear("Primero revisa los logs del servidor\nluego reinicia el servicio"),
                         ["Primero revisa los logs del servidor", "luego reinicia el servicio"])
        self.assertEqual(frases.trocear(""), [])

    def test_conserva_el_contenido(self):
        original = "Uno dos tres cuatro cinco. Seis siete ocho nueve diez! Once 1.5 doce trece"
        self.assertEqual(" ".join(frases.trocear(original)), original)


# ── 2-3, 6-7, 10. Streaming en brain ──────────────────────────────
class TestStreamBrain(Base):
    def test_texto_simple_respuesta_completa_y_deltas(self):
        final = "Son las diez de la mañana. Hace sol en Sevilla."
        stream = StreamFalso([texto("Son las diez "), texto("de la mañana. "), texto("Hace sol en Sevilla.")],
                             mensaje("end_turn", bloque_texto(final)))
        respuesta, s, crear = self.pensar([stream])
        self.assertEqual(respuesta, final)
        self.assertEqual(self.parciales[-1], final)
        self.assertEqual(self.parciales, sorted(self.parciales, key=len))  # solo crecen
        crear.assert_not_called()
        self.assertTrue(stream.cerrado)

    def test_tool_use_descarta_el_texto_previo_y_pasa_por_el_gate(self):
        it1 = StreamFalso([texto("Voy a mirar tu agenda. "), inicio_tool()],
                          mensaje("tool_use", bloque_texto("Voy a mirar tu agenda. "), bloque_tool("ver_agenda")))
        it2 = StreamFalso([texto("Mañana tienes dentista.")],
                          mensaje("end_turn", bloque_texto("Mañana tienes dentista.")))
        with patch("brain._gate", wraps=brain._gate) as gate, \
             patch.object(brain.calendario, "ver_agenda", return_value=(True, "- Mar 20/10: Dentista")):
            respuesta, _, _ = self.pensar([it1, it2])
        self.assertEqual(respuesta, "Mañana tienes dentista.")
        self.assertNotIn("Voy a mirar", respuesta)          # lo que se dirá
        self.assertEqual(gate.call_args.args[0], "ver_agenda")
        self.assertEqual(self.parciales, ["Voy a mirar tu agenda. ", "", "Mañana tienes dentista."])

    def test_excepcion_al_abrir_reintenta_sin_streaming(self):
        fallo = StreamFalso([], None, fallo_al_abrir=True)
        respuesta, _, crear = self.pensar([fallo], create=[mensaje("end_turn", bloque_texto("Hola, aquí estoy."))])
        self.assertEqual(respuesta, "Hola, aquí estoy.")
        crear.assert_called_once()

    def test_excepcion_a_mitad_limpia_la_pantalla_y_reintenta(self):
        fallo = StreamFalso([texto("Empiezo a "), texto("responder")], None, fallo_tras=1)
        respuesta, _, crear = self.pensar([fallo], create=[mensaje("end_turn", bloque_texto("Respuesta completa."))])
        self.assertEqual(respuesta, "Respuesta completa.")
        self.assertEqual(self.parciales, ["Empiezo a ", ""])
        crear.assert_called_once()

    def test_jarvis_streaming_0_usa_create(self):
        os.environ["JARVIS_STREAMING"] = "0"
        respuesta, stream, crear = self.pensar([], create=[mensaje("end_turn", bloque_texto("Sin streaming."))])
        self.assertEqual(respuesta, "Sin streaming.")
        stream.assert_not_called()
        self.assertEqual(self.parciales, [])

    def test_sin_gancho_usa_create(self):
        brain.al_texto_parcial = None
        _, stream, crear = self.pensar([], create=[mensaje("end_turn", bloque_texto("ok"))])
        stream.assert_not_called()
        crear.assert_called_once()

    def test_prefijo_cacheado_identico_en_stream_y_create(self):
        final = mensaje("end_turn", bloque_texto("ok"))
        _, stream, _ = self.pensar([StreamFalso([texto("ok")], final)])
        os.environ["JARVIS_STREAMING"] = "0"
        _, _, crear = self.pensar([], create=[final])
        kw_s, kw_c = stream.call_args.kwargs, crear.call_args.kwargs
        self.assertEqual(kw_s["tools"], kw_c["tools"])
        self.assertEqual(kw_s["system"][0], kw_c["system"][0])
        self.assertEqual([b for b in kw_s["system"] if "cache_control" in b], [kw_s["system"][0]])
        self.assertEqual(kw_s["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(set(kw_s), {"model", "max_tokens", "system", "tools", "messages"})


# ── 2, 4, 7. Qué dice jarvis con la respuesta final ───────────────
class TestDecirRespuesta(Base):
    def setUp(self):
        super().setUp()
        jarvis.audio.reset_mock()

    def test_texto_normal_se_dice_por_frases_en_orden(self):
        respuesta = "Son las diez de la mañana. Hace sol en Sevilla. ¿Quieres algo más?"
        jarvis._decir_respuesta(respuesta)
        jarvis.audio.hablar.assert_not_called()
        dichas = jarvis.audio.hablar_frases.call_args.args[0]
        self.assertEqual(dichas, ["Son las diez de la mañana.", "Hace sol en Sevilla.", "¿Quieres algo más?"])
        self.assertEqual(" ".join(dichas), respuesta)  # lo que se guarda en memoria es `respuesta`
        self.assertEqual(jarvis.audio.hablar_frases.call_args.kwargs["texto_pantalla"], respuesta)

    def test_codigo_largo_aviso_de_pantalla_como_hoy(self):
        codigo = "Aquí tienes:\n```python\n" + "\n".join(f"    linea_{i} = {i}" for i in range(30)) + "\n```"
        jarvis._decir_respuesta(codigo)
        jarvis.audio.hablar_frases.assert_not_called()
        jarvis.audio.hablar.assert_called_once_with(jarvis.AVISO_PANTALLA, texto_pantalla=codigo)

    def test_codigo_corto_no_se_trocea(self):
        corto = "Usa esto:\n    x = 1"
        jarvis._decir_respuesta(corto)
        jarvis.audio.hablar_frases.assert_not_called()
        jarvis.audio.hablar.assert_called_once_with(corto, texto_pantalla=corto)

    def test_jarvis_streaming_0_flujo_anterior(self):
        os.environ["JARVIS_STREAMING"] = "0"
        jarvis._decir_respuesta("Son las diez de la mañana. Hace sol en Sevilla.")
        jarvis.audio.hablar_frases.assert_not_called()
        jarvis.audio.hablar.assert_called_once()


# ── 5-6, 8. Cola de frases en audio ───────────────────────────────
class TestHablarFrases(Base):
    FRASES = ["Primera frase bastante larga.", "Segunda frase también larga.", "Tercera y última frase aquí."]

    def hablar(self, sintetizar, interrumpir_en_play=False):
        audio.sd.reset_mock()
        audio.sd.get_stream.return_value.active = False
        if interrumpir_en_play:
            def play(*_):
                audio._INTERRUMPIR = True
                audio.sd.get_stream.return_value.active = True
            audio.sd.play.side_effect = play
        else:
            audio.sd.play.side_effect = None
        with patch.object(audio, "_sintetizar", side_effect=sintetizar) as sint, \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit") as emit, contextlib.redirect_stdout(io.StringIO()):
            audio.hablar_frases(self.FRASES, texto_pantalla="pantalla")
        return sint, emit

    def test_frases_en_orden(self):
        sint, emit = self.hablar(lambda f: (np.full(4, len(f), dtype=np.int16), 22050))
        self.assertEqual([c.args[0] for c in sint.call_args_list], self.FRASES)
        tocados = [int(c.args[0][0]) for c in audio.sd.play.call_args_list]
        self.assertEqual(tocados, [len(f) for f in self.FRASES])
        emit.assert_any_call("assistant_message", text="pantalla")
        self.assertEqual(len([c for c in emit.call_args_list if c.args[0] == "assistant_message"]), 1)

    def test_barge_in_corta_y_no_dice_nada_mas(self):
        sint, emit = self.hablar(lambda f: (np.zeros(4, dtype=np.int16), 22050), interrumpir_en_play=True)
        self.assertEqual(audio.sd.play.call_count, 1)
        audio.sd.stop.assert_called_once()
        emit.assert_any_call("state", state="interrupted")
        self.assertFalse(any(t.name == "voz-frases" and t.is_alive() for t in threading.enumerate()))
        self.assertFalse(audio.JARVIS_HABLANDO)
        self.assertFalse(audio._INTERRUMPIR)

    def test_fallo_de_sintesis_a_mitad_conserva_lo_dicho(self):
        def sintetizar(f):
            if f.startswith("Segunda"):
                raise RuntimeError("piper roto")
            return np.zeros(4, dtype=np.int16), 22050
        _, emit = self.hablar(sintetizar)
        self.assertEqual(audio.sd.play.call_count, 1)
        emit.assert_any_call("error", message="No he podido decir el resto de la respuesta.")

    def test_turno_privado_todas_las_frases_con_piper(self):
        os.environ.update({"JARVIS_TTS": "elevenlabs", "ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_ID": "v"})
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")])
        tts_elevenlabs.marcar_privado()
        audio.sd.get_stream.return_value.active = False
        audio.sd.play.side_effect = None
        with patch("tts_elevenlabs.requests.post") as post, \
             patch.object(audio, "_sintetizar_piper", return_value=(np.zeros(4, dtype=np.int16), 22050)) as piper, \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit"), contextlib.redirect_stdout(io.StringIO()):
            audio.hablar_frases(self.FRASES)
        post.assert_not_called()
        self.assertEqual(piper.call_count, 3)


# ── 9. Métricas ───────────────────────────────────────────────────
class TestMetricasStreaming(Base):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ruta = os.path.join(tmp.name, "turnos.jsonl")
        for p in (patch.object(metrics, "RUTA_TURNOS", self.ruta), patch.object(metrics.ui_bridge, "emit")):
            p.start()
            self.addCleanup(p.stop)

    def test_ttft_y_primera_voz_presentes_y_solo_el_primero(self):
        metrics.iniciar_turno()
        metrics.registrar_ttft(350)
        metrics.registrar_ttft(9999)
        metrics.registrar_primera_voz()
        metrics.registrar_primera_voz()
        reg = metrics.cerrar_turno("x", "y")
        self.assertEqual(reg["ttft_ms"], 350)
        self.assertIsInstance(reg["primera_voz_ms"], int)
        self.assertIn("primera_voz_ms", metrics.evento_ui(reg))

    def test_opcionales_y_jsonl_antiguo(self):
        metrics.iniciar_turno()
        reg = metrics.cerrar_turno("x", "y")
        self.assertNotIn("ttft_ms", reg)
        self.assertNotIn("primera_voz_ms", reg)
        with open(self.ruta, "a", encoding="utf-8") as f:  # línea de antes de esta versión
            f.write(json.dumps({"ts": "2026-10-10T10:00:00+02:00", "ruta": "atajo", "total_ms": 80,
                                "coste_usd": 0}) + "\n")
        turnos = metrics.cargar()
        self.assertEqual(len(turnos), 2)
        self.assertEqual(metrics.resumen(turnos)["turnos"], 2)

    def test_texto_provisional_no_llega_al_jsonl_ni_a_lo_que_se_guarda(self):
        metrics.iniciar_turno()
        it1 = StreamFalso([texto("PROVISIONAL_DESCARTADO "), inicio_tool()],
                          mensaje("tool_use", bloque_texto("PROVISIONAL_DESCARTADO "), bloque_tool("hora")))
        it2 = StreamFalso([texto("Respuesta "), texto("final.")], mensaje("end_turn", bloque_texto("Respuesta final.")))
        with patch.object(brain, "_ejecutar", return_value=brain._ok("10:00")):
            respuesta, _, _ = self.pensar([it1, it2], texto="qué hora es")
        self.assertIn("PROVISIONAL_DESCARTADO ", self.parciales)  # solo pasó por la UI
        self.assertEqual(respuesta, "Respuesta final.")           # jarvis guarda esto en memoria
        metrics.cerrar_turno("qué hora es", respuesta)
        with open(self.ruta, encoding="utf-8") as f:
            crudo = f.read()
        self.assertNotIn("PROVISIONAL", crudo)
        self.assertNotIn("Respuesta ", crudo.replace("Respuesta final.", ""))  # ni deltas parciales
        self.assertIn("Respuesta final.", crudo)

    def test_metrics_no_tiene_entrada_para_deltas(self):
        # El único camino de assistant_delta es brain.al_texto_parcial → UI
        self.assertFalse([n for n in dir(metrics) if "delta" in n.lower() or "parcial" in n.lower()])

    def test_stream_registra_ttft(self):
        metrics.iniciar_turno()
        stream = StreamFalso([texto("Hola.")], mensaje("end_turn", bloque_texto("Hola.")))
        self.pensar([stream])
        reg = metrics.cerrar_turno("hola", "Hola.")
        self.assertIn("ttft_ms", reg)


if __name__ == "__main__":
    unittest.main()
