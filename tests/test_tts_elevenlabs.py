"""TTS ElevenLabs opcional con fallback a Piper. Sin red: requests.post mockeado.
Sin micro ni modelos: sounddevice, piper y pycaw sustituidos por mocks."""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import tts_elevenlabs  # noqa: E402  (antes de audio: así audio usa este mismo módulo)

# scipy también: otro test ya importó audio dentro de patch.dict y scipy no se puede reimportar
_STUBS = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw",
                                   "scipy", "scipy.io", "scipy.io.wavfile")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
import brain  # noqa: E402

CLAVE = "sk-SECRETO-no-imprimir-123"
VOZ   = "voz-eval-1"
PCM   = np.arange(10, dtype=np.int16).tobytes()
ENV_EL = {"JARVIS_TTS": "elevenlabs", "ELEVENLABS_API_KEY": CLAVE, "ELEVENLABS_VOICE_ID": VOZ}


def respuesta(status=200, content=PCM):
    return MagicMock(status_code=status, content=content)


class Base(unittest.TestCase):
    def setUp(self):
        tts_elevenlabs._fallos_seguidos   = 0
        tts_elevenlabs._desactivado_hasta = 0.0
        tts_elevenlabs._total_chars       = 0
        tts_elevenlabs._aviso_config      = False
        tts_elevenlabs.nuevo_turno()
        self.addCleanup(tts_elevenlabs.nuevo_turno)
        # Nunca leer el elevenlabs.json real
        p = patch.object(tts_elevenlabs, "ELEVENLABS_JSON", os.path.join("no", "existe.json"))
        p.start()
        self.addCleanup(p.stop)
        self.salida = io.StringIO()
        r = contextlib.redirect_stdout(self.salida)
        r.__enter__()
        self.addCleanup(r.__exit__, None, None, None)

    def env(self, valores):
        limpio = {k: v for k, v in os.environ.items()
                  if k not in ("JARVIS_TTS", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")}
        limpio.update(valores)
        p = patch.dict(os.environ, limpio, clear=True)
        p.start()
        self.addCleanup(p.stop)


class TestSeleccion(Base):
    def test_sin_jarvis_tts_es_piper_sin_tocar_requests(self):
        self.env({"ELEVENLABS_API_KEY": CLAVE, "ELEVENLABS_VOICE_ID": VOZ})
        with patch("tts_elevenlabs.requests.post") as post:
            self.assertIsNone(tts_elevenlabs.sintetizar("hola"))
        post.assert_not_called()

    def test_elevenlabs_sin_clave_es_piper_con_un_solo_aviso(self):
        self.env({"JARVIS_TTS": "elevenlabs", "ELEVENLABS_VOICE_ID": VOZ})
        with patch("tts_elevenlabs.requests.post") as post:
            self.assertIsNone(tts_elevenlabs.sintetizar("hola"))
            self.assertIsNone(tts_elevenlabs.sintetizar("adiós"))
        post.assert_not_called()
        self.assertEqual(self.salida.getvalue().count("falta ELEVENLABS_API_KEY"), 1)

    def test_clave_y_voz_desde_json(self):
        self.env({"JARVIS_TTS": "elevenlabs"})
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_el_tmp.json")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write('{"api_key": "%s", "voice_id": "%s"}' % (CLAVE, VOZ))
        self.addCleanup(os.remove, ruta)
        with patch.object(tts_elevenlabs, "ELEVENLABS_JSON", ruta), \
             patch("tts_elevenlabs.requests.post", return_value=respuesta()) as post:
            self.assertEqual(tts_elevenlabs.sintetizar("hola"), PCM)
        self.assertIn(VOZ, post.call_args.args[0])


class TestExito(Base):
    def test_llama_a_la_api_con_voz_modelo_y_texto(self):
        self.env(ENV_EL)
        with patch("tts_elevenlabs.requests.post", return_value=respuesta()) as post:
            self.assertEqual(tts_elevenlabs.sintetizar("Hola Ana"), PCM)
        args, kw = post.call_args.args, post.call_args.kwargs
        self.assertEqual(args[0], f"https://api.elevenlabs.io/v1/text-to-speech/{VOZ}")
        self.assertEqual(kw["json"], {"text": "Hola Ana", "model_id": "eleven_flash_v2_5"})
        self.assertEqual(kw["params"], {"output_format": "pcm_22050"})
        self.assertEqual(kw["headers"]["xi-api-key"], CLAVE)
        self.assertEqual(kw["timeout"], 10)
        self.assertIn("[TTS] elevenlabs chars=8 total=8", self.salida.getvalue())

    def test_hablar_reproduce_el_audio_de_elevenlabs(self):
        self.env(ENV_EL)
        audio.sd.get_stream.return_value.active = False
        with patch("tts_elevenlabs.requests.post", return_value=respuesta()), \
             patch.object(audio, "_sintetizar_piper") as piper, \
             patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit") as emit, \
             patch.object(audio.sd, "play") as play:
            audio.hablar("Te muestro el contenido en pantalla.", texto_pantalla="archivo completo")
        piper.assert_not_called()
        datos, rate = play.call_args.args
        self.assertEqual(rate, 22050)
        np.testing.assert_array_equal(datos, np.frombuffer(PCM, dtype=np.int16))
        emit.assert_any_call("assistant_message", text="archivo completo")

    def test_hablar_sin_elevenlabs_usa_piper(self):
        self.env({})
        audio.sd.get_stream.return_value.active = False
        piper_data = np.zeros(4, dtype=np.int16)
        with patch.object(audio, "_sintetizar_piper", return_value=(piper_data, 22050)) as piper, \
             patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit"), \
             patch.object(audio.sd, "play") as play:
            audio.hablar("hola")
        piper.assert_called_once_with("hola")
        self.assertIs(play.call_args.args[0], piper_data)


class TestFallback(Base):
    def test_errores_vuelven_a_piper(self):
        self.env(ENV_EL)
        casos = [
            (dict(return_value=respuesta(401)), "HTTP 401"),
            (dict(return_value=respuesta(429)), "HTTP 429"),
            (dict(side_effect=requests.Timeout()), "timeout"),
            (dict(return_value=respuesta(200, b"")), "respuesta vacía"),
            (dict(side_effect=requests.ConnectionError()), "error de red"),
        ]
        for kwargs, motivo in casos:
            tts_elevenlabs._fallos_seguidos = 0
            with patch("tts_elevenlabs.requests.post", **kwargs):
                self.assertIsNone(tts_elevenlabs.sintetizar("hola"), motivo)
            self.assertIn(f"[TTS] fallback a piper: {motivo}", self.salida.getvalue())

    def test_tres_fallos_seguidos_desactivan_elevenlabs(self):
        self.env(ENV_EL)
        with patch("tts_elevenlabs.requests.post", return_value=respuesta(500)) as post:
            for _ in range(3):
                self.assertIsNone(tts_elevenlabs.sintetizar("hola"))
            self.assertEqual(post.call_count, 3)
            self.assertIsNone(tts_elevenlabs.sintetizar("hola"))
            self.assertEqual(post.call_count, 3)  # la cuarta ni llama
        self.assertIn("desactivado 5 min", self.salida.getvalue())
        # Pasada la pausa vuelve a intentarlo
        with patch("tts_elevenlabs.time.monotonic", return_value=tts_elevenlabs._desactivado_hasta + 1), \
             patch("tts_elevenlabs.requests.post", return_value=respuesta()) as post:
            self.assertEqual(tts_elevenlabs.sintetizar("hola"), PCM)

    def test_un_exito_reinicia_la_cuenta_de_fallos(self):
        self.env(ENV_EL)
        respuestas = [respuesta(500), respuesta(500), respuesta(), respuesta(500), respuesta(500)]
        with patch("tts_elevenlabs.requests.post", side_effect=respuestas):
            for _ in respuestas:
                tts_elevenlabs.sintetizar("hola")
        self.assertEqual(tts_elevenlabs._desactivado_hasta, 0.0)

    def test_texto_de_600_caracteres_va_a_piper_sin_llamar(self):
        self.env(ENV_EL)
        with patch("tts_elevenlabs.requests.post") as post:
            self.assertIsNone(tts_elevenlabs.sintetizar("a" * 600))
        post.assert_not_called()


class TestClaveNuncaEnLogs(Base):
    def test_la_clave_no_aparece_en_la_salida(self):
        self.env(ENV_EL)
        error_con_clave = requests.ConnectionError(f"fallo con cabecera xi-api-key: {CLAVE}")
        for kwargs in (dict(return_value=respuesta()), dict(return_value=respuesta(401)),
                       dict(side_effect=requests.Timeout(CLAVE)), dict(side_effect=error_con_clave)):
            tts_elevenlabs._fallos_seguidos = 0
            with patch("tts_elevenlabs.requests.post", **kwargs):
                tts_elevenlabs.sintetizar("hola")
        self.env({"JARVIS_TTS": "elevenlabs", "ELEVENLABS_API_KEY": CLAVE})  # sin voz → aviso
        tts_elevenlabs.sintetizar("hola")
        salida = self.salida.getvalue()
        self.assertIn("[TTS]", salida)
        self.assertNotIn(CLAVE, salida)


class TestPrivacidad(Base):
    def turno(self, tool):
        """Un turno de pensar() en el que el modelo usa `tool` (o ninguna)."""
        from types import SimpleNamespace as NS
        fin = NS(stop_reason="end_turn", content=[NS(type="text", text="Hecho.")], usage=None)
        respuestas = [fin]
        if tool:
            uso = NS(type="tool_use", name=tool, id="t1", input={})
            respuestas.insert(0, NS(stop_reason="tool_use", content=[uso], usage=None))
        gmail = MagicMock(**{"leer_no_leidos.return_value": "Tienes 3 emails de Ana"})
        skills = {"gmail": gmail, "memoria": {}}
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             patch("brain._enrutar", return_value=None), patch("brain._log_traza"):
            brain.pensar("x", skills=skills)

    def test_tras_leer_emails_la_respuesta_va_con_piper(self):
        self.env(ENV_EL)
        self.turno("leer_emails")
        with patch("tts_elevenlabs.requests.post") as post:
            self.assertIsNone(tts_elevenlabs.sintetizar("Tienes 3 emails de Ana"))
        post.assert_not_called()
        self.assertIn("[TTS] turno con datos privados → piper", self.salida.getvalue())

    def test_turno_sin_tools_privadas_usa_elevenlabs(self):
        self.env(ENV_EL)
        self.turno("hora")
        with patch("tts_elevenlabs.requests.post", return_value=respuesta()) as post:
            self.assertEqual(tts_elevenlabs.sintetizar("Son las diez"), PCM)
        post.assert_called_once()

    def test_el_siguiente_turno_vuelve_a_elevenlabs(self):
        self.env(ENV_EL)
        self.turno("ver_agenda")
        self.turno(None)
        with patch("tts_elevenlabs.requests.post", return_value=respuesta()) as post:
            tts_elevenlabs.sintetizar("hola")
        post.assert_called_once()

    def test_todas_las_tools_privadas_marcan_el_turno(self):
        self.assertEqual(brain.TOOLS_PRIVADAS, {
            "leer_emails", "leer_email", "buscar_emails", "emails_recruiters", "borrar_email",
            "borrar_multiples_emails", "archivar_email", "enviar_email", "get_mi_email", "ver_agenda",
            "ver_pantalla", "briefing", "buscar_contacto"})
        for tool in brain.TOOLS_PRIVADAS:
            tts_elevenlabs.nuevo_turno()
            with patch("builtins.print"), patch.object(brain.briefing, "desde_skills", return_value="x"):
                brain._ejecutar(tool, {}, {"gmail": MagicMock()})  # aunque el gate la bloquee
            self.assertTrue(tts_elevenlabs.turno_privado(), tool)

    def test_d1_fix_tras_pensar_sigue_siendo_privado(self):
        # El D1-fix ejecuta borrar_email con _ejecutar DESPUÉS de pensar(): también cuenta
        self.env(ENV_EL)
        self.turno(None)
        with patch("builtins.print"):
            brain._ejecutar("borrar_email", {"msg_id": "x"}, {"gmail": MagicMock()})
        with patch("tts_elevenlabs.requests.post") as post:
            self.assertIsNone(tts_elevenlabs.sintetizar("Eliminado el email de Ana"))
        post.assert_not_called()


class TestRmsSalidaGlobal(Base):
    def hablar(self, **sintesis):
        audio.sd.get_stream.return_value.active = False
        with patch.object(audio, "_sintetizar", **sintesis), \
             patch.object(audio, "_track_rms_salida"), \
             patch.object(audio, "_escucha_parada"), \
             patch.object(audio, "ducking_on"), patch.object(audio, "ducking_off"), \
             patch.object(audio.ui_bridge, "emit"), patch.object(audio.sd, "play"):
            audio.hablar("hola")

    def test_rms_salida_vuelve_a_cero(self):
        audio._rms_salida = 5.0
        self.hablar(return_value=(np.zeros(4, dtype=np.int16), 22050))
        self.assertEqual(audio._rms_salida, 0.0)

    def test_rms_salida_vuelve_a_cero_si_falla_la_sintesis(self):
        audio._rms_salida = 5.0
        self.hablar(side_effect=RuntimeError("piper roto"))   # no lanza: voz de emergencia (test_modo_degradado)
        self.assertEqual(audio._rms_salida, 0.0)
        self.assertFalse(audio.JARVIS_HABLANDO)


if __name__ == "__main__":
    unittest.main()
