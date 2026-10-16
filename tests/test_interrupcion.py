"""Interrumpir a JARVIS mientras habla o genera: órdenes de parada por voz, botón/atajo
de la UI, vaciado de la cola de frases, cancelación del streaming, eco del propio TTS
y métricas. Todo con mocks: sin micrófono, sin Whisper, sin red y sin audio real.
"""
import atexit
import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics  # noqa: E402
import parada  # noqa: E402
import tts_elevenlabs  # noqa: E402
import ui_bridge  # noqa: E402

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

FRASES = ["Primera frase bastante larga.", "Segunda frase también larga.", "Tercera y última frase aquí."]


def whisper(texto, logprob=-0.2, no_speech=0.01):
    """Modelo de Whisper falso que transcribe siempre `texto`."""
    modelo = MagicMock()
    modelo.transcribe.return_value = {"text": texto, "segments": [
        {"avg_logprob": logprob, "compression_ratio": 1.1, "no_speech_prob": no_speech}]}
    return modelo


SEGMENTO = np.full(4800, 3000, dtype=np.int16)


class Base(unittest.TestCase):
    def setUp(self):
        audio.sd.reset_mock()
        audio.sd.play.side_effect = None
        audio.sd.get_stream.return_value.active = False
        for nombre, valor in (("_INTERRUMPIR", False), ("_parada", None), ("BARGE_IN_ACTIVO", True),
                              ("JARVIS_HABLANDO", False), ("_texto_en_voz", ""), ("al_parar", [])):
            p = patch.object(audio, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        self.interrupcion = patch.object(metrics, "registrar_interrupcion").start()
        self.addCleanup(patch.stopall)
        brain.reiniciar_cancelacion()
        self.addCleanup(brain.reiniciar_cancelacion)
        self.salida = io.StringIO()

    def hablar_frases(self, sintetizar):
        with patch.object(audio, "_sintetizar", side_effect=sintetizar) as sint, \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit") as emit, contextlib.redirect_stdout(self.salida):
            audio.hablar_frases(FRASES, texto_pantalla="pantalla")
        return sint, emit

    def oir(self, texto, **kw):
        """La escucha de parada oye un segmento corto que Whisper transcribe como `texto`."""
        with patch.object(audio, "modelo_whisper", whisper(texto, **kw)), contextlib.redirect_stdout(self.salida):
            return audio._comprobar_orden(SEGMENTO, time.monotonic())


# ── Órdenes ───────────────────────────────────────────────────────
class TestOrdenes(unittest.TestCase):
    def test_ordenes_de_parada(self):
        for texto in ("para", "Para.", "Jarvis, para", "para ya", "cállate", "¡Cállate, por favor!", "silencio",
                      "basta", "Jarvis, apágate", "apágate", "stop", "párate", "para, para", "calla"):
            self.assertTrue(parada.es_orden_parada(texto), texto)

    def test_no_son_ordenes_de_parada(self):
        for texto in ("para la música", "¿para qué sirve esto?", "apaga el ordenador", "apaga jarvis del todo",
                      "confirmo envío", "confirmo apagado del pc", "jarvis", "", None, "basta de tonterías ya",
                      "silencio en la sala por favor", "para que me digas la hora"):
            self.assertFalse(parada.es_orden_parada(texto), texto)

    def test_ninguna_orden_es_frase_de_confirmacion(self):
        for orden in parada.ORDENES:
            self.assertIsNone(brain._frase_confirmacion(orden), orden)
            self.assertIsNone(brain._frase_confirmacion(f"jarvis {orden}"), orden)


# ── Parada durante el TTS ─────────────────────────────────────────
class TestParadaDuranteTTS(Base):
    def test_orden_por_voz_corta_el_audio_y_no_dice_nada_mas(self):
        def play(*_):
            audio.sd.get_stream.return_value.active = True
            self.assertTrue(self.oir("Jarvis, para."))   # la oye la escucha de parada mientras suena
        audio.sd.play.side_effect = play
        sint, emit = self.hablar_frases(lambda f: (np.zeros(4, dtype=np.int16), 22050))
        self.assertEqual(audio.sd.play.call_count, 1)
        audio.sd.stop.assert_called_once()
        emit.assert_any_call("state", state="interrupted")
        self.assertLessEqual(sint.call_count, 3)
        self.assertFalse(any(t.name == "voz-frases" and t.is_alive() for t in threading.enumerate()))
        self.assertFalse(audio.JARVIS_HABLANDO)
        origen, ms, fase = self.interrupcion.call_args.args
        self.assertEqual((origen, fase), ("voz", "hablando"))
        self.assertGreaterEqual(ms, 0)

    def test_parada_mientras_se_sintetiza_la_siguiente_vacia_la_cola(self):
        sintetizadas = []

        def sintetizar(frase):
            sintetizadas.append(frase)
            if frase.startswith("Segunda"):
                limite = time.monotonic() + 1
                while not audio.sd.play.called and time.monotonic() < limite:
                    time.sleep(0.005)             # ya suena la 1.ª
                audio.parar_voz("teclado")        # llega mientras se genera la 2.ª frase
                time.sleep(0.3)
            return np.zeros(4, dtype=np.int16), 22050
        t0 = time.monotonic()
        self.hablar_frases(sintetizar)
        self.assertLess(time.monotonic() - t0, 0.6)
        self.assertEqual(audio.sd.play.call_count, 1)    # la 2.ª y la 3.ª no suenan
        self.assertNotIn(FRASES[2], sintetizadas)        # el productor no sintetiza más
        self.assertEqual(self.interrupcion.call_args.args[0], "teclado")
        self.assertEqual(self.interrupcion.call_args.args[2], "hablando")

    def test_hablar_entero_tambien_se_corta(self):
        def play(*_):
            audio.sd.get_stream.return_value.active = True
            audio.parar_voz("ui")
        audio.sd.play.side_effect = play
        with patch.object(audio, "_sintetizar", return_value=(np.zeros(4, dtype=np.int16), 22050)), \
             patch.object(audio, "_track_rms_salida"), patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit"), contextlib.redirect_stdout(self.salida):
            audio.hablar("Una respuesta larga.")
        audio.sd.stop.assert_called_once()
        self.assertEqual(self.interrupcion.call_args.args[0], "ui")

    def test_anuncio_de_confirmacion_no_se_interrumpe(self):
        audio.BARGE_IN_ACTIVO = False
        with contextlib.redirect_stdout(self.salida):
            self.assertFalse(audio.parar_voz("voz"))
        self.assertFalse(audio._INTERRUMPIR)
        self.interrumpcion_no_registrada()

    def interrumpcion_no_registrada(self):
        self.interrupcion.assert_not_called()

    def test_parar_llama_a_cancelar_la_generacion(self):
        audio.al_parar.append(brain.cancelar_generacion)
        with contextlib.redirect_stdout(self.salida):
            audio.parar_voz("voz")
        self.assertTrue(brain.generacion_cancelada())
        self.assertEqual(self.interrupcion.call_args.args[2], "generando")  # no hablaba: se mide ya

    def test_escucha_de_parada_es_anidable_y_se_cierra(self):
        arrancado = threading.Event()

        def escucha():
            arrancado.set()
            while audio._escucha_refs > 0:
                time.sleep(0.01)
        with patch.object(audio, "_escucha_parada", side_effect=escucha):
            with audio.escucha_de_parada():
                with audio.escucha_de_parada():
                    self.assertTrue(arrancado.wait(1))
                self.assertTrue(audio._hilo_escucha.is_alive())  # sigue el bloque exterior
            audio._hilo_escucha.join(1)
            self.assertFalse(audio._hilo_escucha.is_alive())
        self.assertEqual(audio._escucha_refs, 0)


# ── Eco y confianza ───────────────────────────────────────────────
class TestEco(Base):
    def test_eco_continuo_no_produce_segmentos(self):
        detector = parada.DetectorVoz(1000)
        rng = np.random.default_rng(0)
        trama = np.zeros(480, dtype=np.int16)
        for i in range(200):                              # 6 s de JARVIS hablando por altavoces
            ref = 3000 + 1500 * np.sin(i / 3)             # habla con picos y valles
            eco = 0.4 * ref * rng.uniform(0.8, 1.2)
            self.assertIsNone(detector.trama(eco, ref, trama), i)

    def test_voz_del_usuario_sobre_el_eco_si(self):
        detector = parada.DetectorVoz(1000)
        trama = np.zeros(480, dtype=np.int16)
        for _ in range(30):
            self.assertIsNone(detector.trama(1200, 3000, trama))  # calibración con eco
        segmentos = [detector.trama(6000, 3000, trama) for _ in range(15)]        # "para" (450 ms)
        segmentos += [detector.trama(1200, 3000, trama) for _ in range(10)]       # vuelve el eco
        self.assertEqual(sum(s is not None for s in segmentos), 1)

    def test_habla_larga_se_descarta(self):
        detector = parada.DetectorVoz(1000)
        trama = np.zeros(480, dtype=np.int16)
        resultados = [detector.trama(6000, 0, trama) for _ in range(120)]         # 3,6 s seguidos
        resultados += [detector.trama(100, 0, trama) for _ in range(20)]
        self.assertTrue(all(r is None for r in resultados))

    def test_sin_calibrar_no_interpreta_nada_mientras_suena(self):
        detector = parada.DetectorVoz(1000)
        trama = np.zeros(480, dtype=np.int16)
        self.assertEqual(detector.umbral(3000), float("inf"))
        self.assertTrue(all(detector.trama(9000, 3000, trama) is None for _ in range(9)))

    def test_jarvis_diciendo_una_orden_no_se_para_a_si_mismo(self):
        audio._texto_en_voz = "Silencio. Así se llama la película que buscas."
        self.assertFalse(self.oir("Silencio."))
        self.assertFalse(audio._INTERRUMPIR)
        self.assertIn("eco", self.salida.getvalue())

    def test_frases_del_propio_tts_no_disparan_nada(self):
        audio._texto_en_voz = "Para la música primero y luego confirmo envío."
        for oido in ("Para la música primero", "y luego confirmo envío", "confirmo borrado"):
            self.assertFalse(self.oir(oido), oido)
        self.assertFalse(audio._INTERRUMPIR)
        self.interrupcion.assert_not_called()

    def test_baja_confianza_o_sin_voz_no_para(self):
        self.assertFalse(self.oir("para", logprob=-1.5))
        self.assertFalse(self.oir("para", no_speech=0.9))
        self.assertFalse(audio._INTERRUMPIR)

    def test_whisper_sin_prompt_de_ordenes(self):
        modelo = whisper("para")
        with patch.object(audio, "modelo_whisper", modelo), contextlib.redirect_stdout(self.salida):
            audio._comprobar_orden(SEGMENTO, time.monotonic())
        self.assertNotIn("initial_prompt", modelo.transcribe.call_args.kwargs)

    def test_tracker_al_ritmo_real_del_audio(self):
        esperas = []
        audio.JARVIS_HABLANDO = True
        with patch.object(audio.time, "sleep", side_effect=esperas.append):
            audio._track_rms_salida(np.ones(22050, dtype=np.int16), 22050)
        self.assertAlmostEqual(sum(esperas), 1.0, places=2)       # 1 s de audio = 1 s de referencia
        self.assertEqual(audio._rms_salida, 0.0)


# ── Cancelación de la generación ──────────────────────────────────
class StreamFalso:
    def __init__(self, eventos, final, al_evento=None):
        self.eventos, self.final, self.al_evento, self.cerrado = eventos, final, al_evento, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.cerrado = True

    def __iter__(self):
        for i, evento in enumerate(self.eventos):
            if self.al_evento:
                self.al_evento(i)
            yield evento

    def get_final_message(self):
        return self.final


class TestCancelacion(Base):
    def setUp(self):
        super().setUp()
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        p = patch.object(brain, "al_texto_parcial", lambda t: None)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("JARVIS_STREAMING", None)

    def pensar(self, texto="explícame la historia de Roma con detalle"):
        with contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills={})

    def test_parada_durante_el_streaming_lo_corta_y_no_reintenta(self):
        eventos = [NS(type="text", text=f"trozo {i} ") for i in range(10)]
        final = NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="todo")])
        stream = StreamFalso(eventos, final, lambda i: i == 3 and brain.cancelar_generacion())
        with patch.object(brain.cliente.messages, "stream", return_value=stream), \
             patch.object(brain.cliente.messages, "create") as create, \
             patch.object(brain, "_clasificar_peticion", return_value="claude"):
            self.assertIsNone(self.pensar())
        self.assertTrue(stream.cerrado)
        create.assert_not_called()                       # no se repite sin streaming
        self.assertEqual(brain.ultima_traza()["stop_final"], "cancelada")

    def test_parada_antes_de_ejecutar_tools_no_ejecuta_nada(self):
        def crear(**_):
            brain.cancelar_generacion()                  # llega mientras el modelo decide
            return NS(stop_reason="tool_use", usage=None,
                      content=[NS(type="tool_use", id="t1", name="apagar_pc", input={"accion": "apagar"})])
        os.environ["JARVIS_STREAMING"] = "0"
        self.addCleanup(os.environ.pop, "JARVIS_STREAMING", None)
        with patch.object(brain.cliente.messages, "create", side_effect=crear), \
             patch.object(brain, "_ejecutar") as ejecutar, \
             patch.object(brain, "_clasificar_peticion", return_value="claude"):
            self.assertIsNone(self.pensar("apaga el ordenador"))
        ejecutar.assert_not_called()
        self.assertIsNone(brain._pendiente)

    def test_turno_cancelado_no_habla_ni_ejecuta_d1_ni_anuncio(self):
        def pensar(*_, **__):
            brain.cancelar_generacion()
            return "Una respuesta larguísima que ya no se dice."
        jarvis.audio.reset_mock()
        with patch.object(jarvis.brain, "pensar", side_effect=pensar), \
             patch.object(jarvis, "_d1_fix") as d1, patch.object(jarvis, "_decir_respuesta") as decir, \
             patch.object(jarvis.brain, "anuncio_confirmacion") as anuncio, \
             contextlib.redirect_stdout(self.salida):
            self.assertEqual(jarvis._turno("cuéntame algo largo"), (None, False))
        d1.assert_not_called()
        anuncio.assert_not_called()
        decir.assert_not_called()
        jarvis.audio.hablar.assert_not_called()


# ── Las órdenes de parada nunca confirman ni cierran ──────────────
class TestNoConfirma(Base):
    def setUp(self):
        super().setUp()
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)

    def test_parada_con_accion_pendiente_no_la_confirma_ni_la_gasta(self):
        with contextlib.redirect_stdout(self.salida):
            res = brain._ejecutar("apagar_pc", {"accion": "apagar"}, {}, "apaga el ordenador")
        self.assertIn("confirmo apagado del pc", res["message"])
        firma = brain._pendiente["firma"]
        with patch("brain.subprocess.run") as run, \
             patch.object(brain.cliente.messages, "create") as create, contextlib.redirect_stdout(self.salida):
            for orden in ("para", "Jarvis, cállate", "apágate", "basta"):
                self.assertIsNone(brain.pensar(orden, skills={}), orden)
        run.assert_not_called()
        create.assert_not_called()                       # ni modelo ni gate
        self.assertEqual(brain._pendiente["firma"], firma)
        self.assertEqual(brain.ultima_traza()["router"], "parada")

    def test_orden_oida_mientras_habla_no_llega_al_gate(self):
        with patch.object(brain, "_gate_core") as gate, patch.object(brain, "pensar") as pensar:
            self.oir("confirmo envío")
            self.oir("para")
        gate.assert_not_called()
        pensar.assert_not_called()

    def test_apagate_no_cierra_jarvis(self):
        with patch.object(brain.cliente.messages, "create") as create, contextlib.redirect_stdout(self.salida):
            brain.pensar("Jarvis, apágate", skills={})
        create.assert_not_called()
        self.assertFalse(brain.debe_cerrar())
        self.assertNotIn("apagate", [f for frases, _ in brain.ATAJOS for f in frases])
        self.assertEqual(brain._match_atajo(brain._normalizar("apaga jarvis del todo")), "cerrar_jarvis")
        self.assertIn("nunca cierres Jarvis por ellas", brain.SYSTEM)

    def test_orden_sin_voz_activa_solo_para_la_musica_de_jarvis(self):
        musica = MagicMock(musica_proceso=object())
        with patch.dict(jarvis.SKILLS, {"musica": musica}), contextlib.redirect_stdout(self.salida):
            jarvis._callar_sin_voz()
        musica.parar.assert_called_once()
        musica = MagicMock(musica_proceso=None)          # VLC del usuario: no se toca
        with patch.dict(jarvis.SKILLS, {"musica": musica}), contextlib.redirect_stdout(self.salida):
            jarvis._callar_sin_voz()
        musica.parar.assert_not_called()


# ── Botón y atajo de la UI ────────────────────────────────────────
class TestUI(unittest.TestCase):
    def test_solo_acepta_stop(self):
        parar = MagicMock()
        with patch.object(ui_bridge, "al_parar", parar):
            ui_bridge.recibir(json.dumps({"type": "stop", "origen": "ui"}))
            ui_bridge.recibir(json.dumps({"type": "stop", "origen": "teclado"}))
            ui_bridge.recibir(json.dumps({"type": "stop", "origen": "malicioso"}))
            for raro in ("no json", json.dumps({"type": "confirm", "texto": "confirmo envío"}),
                         json.dumps(["stop"]), json.dumps({"type": "stop", "x": "a" * 500}), None, b"stop"):
                ui_bridge.recibir(raro)
        self.assertEqual([c.args for c in parar.call_args_list], [("ui",), ("teclado",), ("ui",)])

    def test_sin_callback_no_lanza(self):
        with patch.object(ui_bridge, "al_parar", None):
            ui_bridge.recibir(json.dumps({"type": "stop"}))

    def test_la_app_envia_stop_con_boton_esc_y_atajo(self):
        app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
        leer = lambda n: open(os.path.join(app, n), encoding="utf-8").read()  # noqa: E731
        self.assertIn('id="parar"', leer("index.html"))
        renderer = leer("renderer.js")
        self.assertIn("type: 'stop'", renderer)
        self.assertIn("'Escape'", renderer)
        self.assertIn("globalShortcut.register(ATAJO_PARAR", leer("main.js"))
        self.assertIn("contextBridge.exposeInMainWorld('jarvis'", leer("preload.js"))


# ── Métricas ──────────────────────────────────────────────────────
class TestMetricas(unittest.TestCase):
    def setUp(self):
        metrics.descartar_turno()
        self.addCleanup(metrics.descartar_turno)
        tts_elevenlabs.nuevo_turno()
        self.addCleanup(tts_elevenlabs.nuevo_turno)

    def cerrar(self, texto, respuesta):
        with patch.object(metrics, "_escribir") as escribir, patch.object(metrics.ui_bridge, "emit"), \
             contextlib.redirect_stdout(io.StringIO()):
            metrics.cerrar_turno(texto, respuesta)
        return escribir.call_args.args[0]

    def test_interrupcion_en_el_turno_sin_texto(self):
        metrics.iniciar_turno()
        with contextlib.redirect_stdout(io.StringIO()):
            metrics.registrar_interrupcion("voz", 183.7, "hablando")
        reg = self.cerrar("cuéntame la historia de Roma", "Roma se fundó...")
        self.assertEqual(reg["interrupciones"], [{"origen": "voz", "fase": "hablando", "ms": 183}])

    def test_turno_privado_guarda_la_interrupcion_pero_no_el_texto(self):
        metrics.iniciar_turno()
        tts_elevenlabs.marcar_privado()
        with contextlib.redirect_stdout(io.StringIO()):
            metrics.registrar_interrupcion("teclado", 40, "hablando")
        reg = self.cerrar("lee mis emails", "Tienes 3 emails de Ana...")
        self.assertTrue(reg["privado"])
        self.assertNotIn("texto_usuario", reg)
        self.assertNotIn("respuesta", reg)
        self.assertEqual(reg["interrupciones"][0]["origen"], "teclado")

    def test_fuera_de_turno_no_lanza(self):
        with contextlib.redirect_stdout(io.StringIO()):
            metrics.registrar_interrupcion("ui", 10, "hablando")


if __name__ == "__main__":
    unittest.main()
