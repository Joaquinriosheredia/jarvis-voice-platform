"""Órdenes de silencio y de apagado (parada.SILENCIO / parada.APAGADO), la respuesta
a "¿cómo te callo / te cierro?" y las variantes de la frase de confirmación.
Todo con mocks: sin micrófono, sin Whisper, sin red y sin audio real.
"""
import atexit
import contextlib
import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parada  # noqa: E402

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

SEGMENTO = np.full(4800, 3000, dtype=np.int16)
FRASES_LARGAS_CON_APAGATE = (
    "creo que ya deberías apagarte", "apágate cuando termines de leer el correo",
    "no te apágate todavía que quiero otra cosa", "Jarvis, apágate ahora mismo que me voy a dormir",
    "mi madre dice apágate", "si te digo apágate te apagas",
)


def whisper(texto, logprob=-0.2):
    modelo = MagicMock()
    modelo.transcribe.return_value = {"text": texto, "segments": [
        {"avg_logprob": logprob, "compression_ratio": 1.1, "no_speech_prob": 0.01}]}
    return modelo


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        brain._cerrar = False
        brain.reiniciar_cancelacion()
        for limpiar in ((setattr, brain, "_pendiente", None), (setattr, brain, "_cerrar", False),
                        (brain.reiniciar_cancelacion,)):
            self.addCleanup(*limpiar)
        self.salida = io.StringIO()

    def pensar(self, texto):
        """brain.pensar con un modelo que falla si se le llama."""
        with patch.object(brain.cliente.messages, "create", side_effect=AssertionError("llamó al modelo")), \
             patch.object(brain.cliente.messages, "stream", side_effect=AssertionError("llamó al modelo")), \
             contextlib.redirect_stdout(self.salida):
            return brain.pensar(texto, skills={})


# ── Las dos listas ────────────────────────────────────────────────
class TestListas(unittest.TestCase):
    def test_listas_pedidas_y_disjuntas(self):
        self.assertEqual(parada.SILENCIO, ("cállate", "no hables", "escucha", "silencio", "para", "basta"))
        self.assertEqual(parada.APAGADO, ("apágate", "apaga JARVIS", "apaga JARVIS del todo", "adiós",
                                          "hasta luego", "desconéctate", "cierra JARVIS"))
        normal = lambda frases: {parada.normalizar(f) for f in frases}  # noqa: E731
        self.assertFalse(normal(parada.SILENCIO) & normal(parada.APAGADO))

    def test_cada_orden_de_silencio(self):
        for orden in parada.SILENCIO:
            for texto in (orden, orden.upper() + ".", f"Jarvis, {orden}", f"{orden}, por favor", f"{orden} ya"):
                self.assertEqual(parada.clasificar(texto), "silencio", texto)

    def test_cada_orden_de_apagado(self):
        for orden in parada.APAGADO:
            for texto in (orden, orden.upper() + ".", f"Jarvis, {orden}", f"vale, {orden}"):
                self.assertEqual(parada.clasificar(texto), "apagado", texto)

    def test_apagate_apaga_y_no_silencia(self):
        self.assertEqual(parada.clasificar("apágate"), "apagado")
        self.assertFalse(parada.es_orden_silencio("apágate"))
        self.assertTrue(parada.es_orden_apagado("Jarvis, apágate"))

    def test_frase_larga_con_apagate_no_apaga(self):
        for texto in FRASES_LARGAS_CON_APAGATE:
            self.assertIsNone(parada.clasificar(texto), texto)

    def test_maximo_cuatro_palabras_sin_contar_jarvis(self):
        self.assertEqual(parada.clasificar("Jarvis, apaga Jarvis del todo"), "apagado")   # 3 + nombre
        self.assertEqual(parada.clasificar("apaga Jarvis del todo ya"), "apagado")         # 4
        self.assertIsNone(parada.clasificar("vale vale apaga Jarvis del todo ya"))         # 6

    def test_no_son_ordenes(self):
        for texto in ("apaga", "cierra", "apaga el ordenador", "cierra el navegador", "para la música",
                      "escucha esta canción", "hasta luego Lucas", "adiós mundo cruel", "sí", "vale", "jarvis",
                      "stop", "calla", "", None):
            self.assertIsNone(parada.clasificar(texto), texto)


# ── Por la capa de brain: sin modelo, sin gate ────────────────────
class TestBrain(Base):
    def test_silencio_no_cierra(self):
        for orden in parada.SILENCIO:
            self.assertIsNone(self.pensar(f"Jarvis, {orden}"), orden)
            self.assertFalse(brain.debe_cerrar(), orden)
            self.assertEqual(brain.ultima_traza()["router"], "parada")

    def test_apagado_cierra(self):
        for orden in parada.APAGADO:
            self.assertIsNone(self.pensar(orden), orden)
            self.assertTrue(brain.debe_cerrar(), orden)
            self.assertEqual(brain.ultima_traza()["router"], "apagado")

    def test_frase_larga_con_apagate_va_al_modelo_que_no_puede_cerrar(self):
        self.assertNotIn("cerrar_jarvis", [t["name"] for t in brain.TOOLS])
        self.assertNotIn("cerrar_jarvis", brain.TOOL_RISK)
        respuesta = MagicMock(stop_reason="end_turn", usage=None, content=[MagicMock(text="Vale.")])
        for texto in FRASES_LARGAS_CON_APAGATE:
            with patch.object(brain.cliente.messages, "create", return_value=respuesta) as create, \
                 patch.dict(os.environ, {"JARVIS_STREAMING": "0"}), contextlib.redirect_stdout(self.salida):
                brain.pensar(texto, skills={})
            create.assert_called_once()
            self.assertFalse(brain.debe_cerrar(), texto)

    def test_ordenes_no_confirman_ni_gastan_la_pendiente(self):
        with contextlib.redirect_stdout(self.salida):
            brain._gate("apagar_pc", {"accion": "apagar"}, "apaga el ordenador")
        firma = brain._pendiente["firma"]
        with patch("brain.subprocess.run") as run:
            for orden in parada.SILENCIO + parada.APAGADO:
                self.pensar(orden)
                self.assertIsNone(brain._frase_confirmacion(orden), orden)
        run.assert_not_called()
        self.assertEqual(brain._pendiente["firma"], firma)

    def test_sin_atajos_de_cierre_fuera_de_la_lista(self):
        atajos = [f for frases, _ in brain.ATAJOS for f in frases]
        for orden in parada.SILENCIO + parada.APAGADO:
            self.assertNotIn(parada.normalizar(orden), atajos, orden)
        self.assertNotIn("cerrar_jarvis", [tool for _, tool in brain.ATAJOS])


# ── "¿Cómo te callo / te cierro?" ─────────────────────────────────
class TestAyuda(Base):
    def test_respuesta_desde_la_lista_sin_modelo(self):
        casos = {"¿Cómo te callo?": "silencio", "qué tengo que decir para que te calles": "silencio",
                 "cómo te interrumpo": "silencio", "¿Cómo te cierro?": "apagado", "cómo apago Jarvis": "apagado",
                 "qué palabras uso para apagarte": "apagado", "cómo te silencio o te apago": "ambas"}
        for pregunta, tipo in casos.items():
            respuesta = self.pensar(pregunta)
            self.assertEqual(respuesta, parada.texto_ayuda(tipo), pregunta)
            self.assertEqual(brain.ultima_traza()["router"], "atajo")
            self.assertFalse(brain.debe_cerrar())
            if tipo in ("silencio", "ambas"):
                for orden in parada.SILENCIO:
                    self.assertIn(f"«{orden}»", respuesta)
            if tipo in ("apagado", "ambas"):
                for orden in parada.APAGADO:
                    self.assertIn(f"«{orden}»", respuesta)
            if tipo == "silencio":
                self.assertNotIn("«apágate»", respuesta)
            if tipo == "apagado":
                self.assertNotIn("«cállate»", respuesta)

    def test_texto_cambia_si_cambia_la_lista(self):
        with patch.object(parada, "SILENCIO", ("chitón",)):
            self.assertIn("«chitón»", parada.texto_ayuda("silencio"))

    def test_otras_preguntas_no_son_ayuda(self):
        for texto in ("cómo apago el ordenador", "cómo te llamas", "cómo cierro el navegador",
                      "cómo paro la música", "qué tiempo hace"):
            self.assertIsNone(parada.pregunta_de_ayuda(texto), texto)

    def test_system_usa_el_mismo_texto(self):
        self.assertIn(parada.texto_ayuda(), brain.SYSTEM)
        self.assertNotIn("{ORDENES_DE_VOZ}", brain.SYSTEM)

    def test_jarvis_leyendo_la_ayuda_no_se_para_con_su_eco(self):
        texto = parada.texto_ayuda()
        for orden in parada.SILENCIO + parada.APAGADO:
            self.assertTrue(parada.eco_probable(orden, texto), orden)


# ── Mientras habla: silenciar y apagar a la primera ───────────────
class TestDuranteLaVoz(Base):
    def setUp(self):
        super().setUp()
        for nombre, valor in (("_INTERRUMPIR", False), ("_parada", None), ("BARGE_IN_ACTIVO", True),
                              ("JARVIS_HABLANDO", True), ("_texto_en_voz", "Roma se fundó en el 753."),
                              ("al_parar", [brain.cancelar_generacion]), ("_apagado", False)):
            p = patch.object(audio, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(audio.metrics, "registrar_interrupcion")
        p.start()
        self.addCleanup(p.stop)

    def oir(self, texto):
        with patch.object(audio, "modelo_whisper", whisper(texto)), contextlib.redirect_stdout(self.salida):
            return audio._comprobar_orden(SEGMENTO, time.monotonic())

    def test_silencio_corta_y_no_cierra(self):
        for orden in parada.SILENCIO:
            audio._INTERRUMPIR = False
            self.assertTrue(self.oir(orden), orden)
            self.assertTrue(audio._INTERRUMPIR)
            self.assertFalse(audio.apagado_pedido())
        self.assertTrue(brain.generacion_cancelada())

    def test_apagado_corta_la_voz_y_deja_pedido_el_cierre(self):
        for orden in parada.APAGADO:
            audio._INTERRUMPIR, audio._apagado = False, False
            self.assertTrue(self.oir(orden), orden)
            self.assertTrue(audio._INTERRUMPIR, orden)     # primero corta el audio
            self.assertTrue(audio.apagado_pedido(), orden)  # y jarvis.py cierra después

    def test_frase_larga_con_apagate_no_hace_nada(self):
        for texto in FRASES_LARGAS_CON_APAGATE:
            self.assertFalse(self.oir(texto), texto)
        self.assertFalse(audio._INTERRUMPIR)
        self.assertFalse(audio.apagado_pedido())

    def test_apagado_durante_un_anuncio_cierra_al_terminarlo(self):
        audio.BARGE_IN_ACTIVO = False
        self.assertTrue(self.oir("apágate"))
        self.assertFalse(audio._INTERRUMPIR)                # el anuncio del gate no se corta
        self.assertTrue(audio.apagado_pedido())


# ── El bucle de jarvis.py ─────────────────────────────────────────
class TestBucle(Base):
    def correr(self, oidos, apagado_tras_turno=False):
        audio_mock = jarvis.audio
        audio_mock.reset_mock()
        audio_mock.escuchar.side_effect = list(oidos) + [KeyboardInterrupt()]
        audio_mock.apagado_pedido.side_effect = [False] + [apagado_tras_turno] * 10
        audio_mock.escucha_de_parada.return_value = contextlib.nullcontext()
        musica = MagicMock(musica_proceso=None)
        # main() conecta callbacks globales: se restauran para no afectar a otros tests
        with patch.object(jarvis.brain, "al_texto_parcial", None), patch("ui_bridge.al_parar", None), \
             patch("ui_bridge.start"), patch.object(jarvis, "resumen_diario", return_value="Hola."), \
             patch.object(jarvis.brain, "cargar_destinatarios_permitidos"), \
             patch.object(jarvis.brain, "pensar", return_value="Una respuesta.") as pensar, \
             patch.object(jarvis, "_decir_respuesta"), patch.object(jarvis, "_d1_fix", return_value=False), \
             patch.object(jarvis.brain, "anuncio_confirmacion", return_value=None), \
             patch.dict(jarvis.SKILLS, {"musica": musica}), contextlib.redirect_stdout(self.salida):
            jarvis.main()
        dichos = [c.args[0] for c in audio_mock.hablar.call_args_list]
        return pensar, dichos

    def test_silencio_sigue_escuchando(self):
        pensar, dichos = self.correr(["cállate", "escucha", "qué hora es"])
        pensar.assert_called_once()                       # solo "qué hora es" llega a brain
        self.assertNotIn(jarvis.DESPEDIDA, dichos)
        self.assertIn("Cerrando Jarvis.", dichos)          # salió por el KeyboardInterrupt del test

    def test_apagate_cierra(self):
        pensar, dichos = self.correr(["Jarvis, apágate", "qué hora es"])
        pensar.assert_not_called()
        self.assertEqual(dichos[-1], jarvis.DESPEDIDA)
        self.assertEqual(jarvis.audio.escuchar.call_count, 1)

    def test_frase_larga_con_apagate_no_cierra(self):
        pensar, dichos = self.correr(["apágate cuando termines de leer el correo"])
        pensar.assert_called_once()
        self.assertNotIn(jarvis.DESPEDIDA, dichos)

    def test_apagate_mientras_hablaba_cierra_tras_cortar(self):
        pensar, dichos = self.correr(["cuéntame la historia de Roma", "otra cosa"], apagado_tras_turno=True)
        pensar.assert_called_once()
        self.assertEqual(dichos[-1], jarvis.DESPEDIDA)
        self.assertEqual(jarvis.audio.escuchar.call_count, 1)


# ── Frase de confirmación ─────────────────────────────────────────
class TestConfirmacion(Base):
    def test_variantes_valen_solo_para_su_accion(self):
        for tool, frase in brain.CONFIRM_PHRASES.items():
            accion = frase.split(" ", 1)[1]
            for verbo in ("confirmo", "Confirma", "CONFIRMAR"):
                for articulo in ("", "el "):
                    for prefijo in ("", "Jarvis, "):
                        texto = f"{prefijo}{verbo} {articulo}{accion}."
                        self.assertEqual(brain._frase_confirmacion(texto), tool, texto)

    def test_sin_tildes_ni_mayusculas(self):
        self.assertEqual(brain._frase_confirmacion("CONFIRMA ENVIO"), "enviar_email")
        self.assertEqual(brain._frase_confirmacion("confirmar el apagado del PC"), "apagar_pc")

    def test_no_valen(self):
        for texto in ("sí", "si", "vale", "ok", "okay", "adelante", "sí, confirmo", "confirmo", "confirma",
                      "envío", "captura", "sí envío", "vale captura", "adelante con el envío", "confirmado envío",
                      "confirmo la captura", "confirmo envío y captura", "confirma envío por favor"):
            self.assertIsNone(brain._frase_confirmacion(texto), texto)

    def test_envio_no_confirma_captura_y_al_reves(self):
        dest = "yo@example.com"
        envio = {"destinatario": dest, "asunto": "x", "cuerpo": "y"}
        with patch.object(brain, "DESTINATARIOS_PERMITIDOS", [dest]), contextlib.redirect_stdout(self.salida):
            brain._gate_core("ver_pantalla", {}, "mira mi pantalla")
            self.assertIn("no corresponde", brain._gate_core("ver_pantalla", {}, "confirma envío"))
            brain._gate_core("enviar_email", envio, "mándamelo")
            self.assertIn("no corresponde", brain._gate_core("enviar_email", envio, "confirma captura"))
            brain._gate_core("enviar_email", envio, "mándamelo")
            self.assertIsNone(brain._gate_core("enviar_email", envio, "confirma el envío"))
            brain._gate_core("ver_pantalla", {}, "mira mi pantalla")
            self.assertIsNone(brain._gate_core("ver_pantalla", {}, "Confirmar captura"))

    def test_un_solo_uso_y_ttl_sin_cambios(self):
        self.assertEqual(brain.CONFIRM_TTL, 120)
        with contextlib.redirect_stdout(self.salida):
            brain._gate_core("ver_pantalla", {}, "mira mi pantalla")
            self.assertIsNone(brain._gate_core("ver_pantalla", {}, "confirma captura"))
            self.assertIn("ya usada", brain._gate_core("ver_pantalla", {}, "confirma captura"))
            brain._gate_core("ver_pantalla", {}, "mira mi pantalla")
            with patch("brain.time.monotonic", return_value=brain._pendiente["ts"] + 121):
                self.assertIn("caducada", brain._gate_core("ver_pantalla", {}, "confirmar captura"))

    def test_la_variante_no_vale_si_la_dice_el_modelo(self):
        with contextlib.redirect_stdout(self.salida):
            brain._gate_core("ver_pantalla", {"pregunta": "confirma captura"}, "mira mi pantalla")
            self.assertIsNotNone(brain._gate_core("ver_pantalla", {"pregunta": "confirma captura"}, "sí"))


if __name__ == "__main__":
    unittest.main()
