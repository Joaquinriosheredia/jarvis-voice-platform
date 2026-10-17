"""Modo degradado: cuando falla una pieza, JARVIS dice una frase útil por voz, no se queda
callado, no repite el error en bucle, no reintenta sin límite y no pierde el turno de memoria.

Fallos simulados con mocks: Anthropic caído o con timeout, Ollama caído, Piper que no
sintetiza, Gmail con el token revocado, GitHub sin red y Whisper sin GPU."""
import atexit
import base64
import contextlib
import io
import os
import shutil
import subprocess
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria ni la API real)

import anthropic  # noqa: E402
import httpx  # noqa: E402
import requests  # noqa: E402
from google.auth import exceptions as gexc  # noqa: E402

import avisos_ci  # noqa: E402
import brain  # noqa: E402
import metrics  # noqa: E402
from skills import github, gmail  # noqa: E402

_HW = {n: MagicMock() for n in ("sounddevice", "piper", "piper.voice", "pycaw", "pycaw.pycaw",
                                "scipy", "scipy.io", "scipy.io.wavfile")}
with patch.dict(sys.modules, _HW), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("audio", None)
    import audio  # noqa: E402

_JV = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _JV), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

PETICION = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def caida():
    return anthropic.APIConnectionError(request=PETICION)


def timeout():
    return anthropic.APITimeoutError(request=PETICION)


def saturado():
    return anthropic.InternalServerError("overloaded", response=httpx.Response(529, request=PETICION), body=None)


def fin(texto):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=texto)], usage=None)


class Base(unittest.TestCase):
    def setUp(self):
        self.salida = io.StringIO()
        p = contextlib.redirect_stdout(self.salida)
        p.__enter__()
        self.addCleanup(p.__exit__, None, None, None)
        brain._olvidar_fallo_claude()
        self.addCleanup(brain._olvidar_fallo_claude)
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        self.addCleanup(setattr, brain, "_ollama_pausado_hasta", 0.0)
        self.addCleanup(setattr, brain, "al_texto_parcial", None)


# ── Anthropic caído o con timeout ─────────────────────────────────
class TestClaudeCaido(Base):
    def pensar(self, error, texto="explícame qué es Kafka"):
        with patch.object(brain.cliente.messages, "create", side_effect=error) as create:
            respuesta = brain.pensar(texto, skills={})
        return respuesta, create

    def test_frase_util_segun_la_causa_y_un_solo_intento_por_turno(self):
        for error, causa in ((caida(), "red"), (timeout(), "timeout"), (saturado(), "saturado"),
                             (RuntimeError("raro"), "desconocida")):
            with self.subTest(causa):
                brain._olvidar_fallo_claude()
                respuesta, create = self.pensar(error)
                self.assertEqual(respuesta, brain.FRASES_FALLO_CLAUDE[causa])
                self.assertEqual(create.call_count, 1)   # los reintentos los acota el SDK
                self.assertEqual(brain.ultima_traza()["stop_final"], "api_error")
        self.assertIn("no hay conexión", brain.FRASES_FALLO_CLAUDE["red"])
        self.assertIn("música", brain.FRASES_FALLO_CLAUDE["red"])   # qué sigue funcionando

    def test_no_repite_el_mismo_parrafo_en_bucle(self):
        primera, _ = self.pensar(caida())
        segunda, _ = self.pensar(caida())
        tercera, _ = self.pensar(timeout())       # otra causa: se explica
        self.assertEqual(primera, brain.FRASES_FALLO_CLAUDE["red"])
        self.assertEqual(segunda, brain.FRASE_FALLO_REPETIDO)
        self.assertEqual(tercera, brain.FRASES_FALLO_CLAUDE["timeout"])
        with patch.object(brain.cliente.messages, "create", return_value=fin("Hola.")):
            brain.pensar("hola", skills={})
        cuarta, _ = self.pensar(timeout())        # tras responder, vuelve a explicarse entero
        self.assertEqual(cuarta, brain.FRASES_FALLO_CLAUDE["timeout"])

    def test_streaming_caido_no_reintenta_sin_streaming(self):
        brain.al_texto_parcial = MagicMock()
        with patch.object(brain.cliente.messages, "stream", side_effect=caida()) as stream, \
             patch.object(brain.cliente.messages, "create") as create:
            respuesta = brain.pensar("explícame qué es Kafka", skills={})
        self.assertEqual(respuesta, brain.FRASES_FALLO_CLAUDE["red"])
        self.assertEqual((stream.call_count, create.call_count), (1, 0))

    def test_cliente_con_timeout_y_reintentos_acotados(self):
        self.assertLessEqual(brain.CLAUDE_TIMEOUT_S, 30)
        self.assertLessEqual(brain.CLAUDE_REINTENTOS, 1)
        with open(brain.__file__, encoding="utf-8") as f:
            self.assertIn("anthropic.Anthropic(timeout=CLAUDE_TIMEOUT_S, max_retries=CLAUDE_REINTENTOS)", f.read())

    def test_el_error_no_sale_en_consola_con_su_texto(self):
        error = anthropic.APIConnectionError(message="fallo con texto-que-no-debe-salir", request=PETICION)
        self.pensar(error)
        self.assertNotIn("texto-que-no-debe-salir", self.salida.getvalue())
        self.assertIn("APIConnectionError (red)", self.salida.getvalue())

    def test_se_dice_por_voz_y_el_turno_queda_en_memoria(self):
        jarvis.audio.reset_mock()
        with patch.object(brain.cliente.messages, "create", side_effect=caida()), \
             patch.object(brain, "_clasificar_peticion", return_value="claude"), \
             patch.object(jarvis.memoria_sesiones, "registrar_turno") as registrar, \
             patch.object(jarvis, "_guardar_si_toca"):
            respuesta, cerrar = jarvis._turno("explícame qué es Kafka")
        frase = brain.FRASES_FALLO_CLAUDE["red"]
        self.assertEqual((respuesta, cerrar), (frase, False))
        dicho = [c.kwargs.get("texto_pantalla") for c in jarvis.audio.hablar_frases.call_args_list] + \
                [c.args[0] for c in jarvis.audio.hablar.call_args_list]
        self.assertIn(frase, dicho)
        texto, respuesta_guardada = registrar.call_args.args[:2]
        self.assertEqual((texto, respuesta_guardada), ("explícame qué es Kafka", frase))


# ── Ollama caído ──────────────────────────────────────────────────
class TestOllamaCaido(Base):
    def setUp(self):
        super().setUp()
        p = patch.dict(os.environ, {"JARVIS_OLLAMA_PAUSA_S": "300"})
        p.start()
        self.addCleanup(p.stop)

    def test_un_fallo_pausa_ollama_y_no_se_paga_el_timeout_en_cada_turno(self):
        with patch.object(brain.requests, "post", side_effect=requests.ConnectionError) as post:
            self.assertIsNone(brain._llamar_ollama("qué es un volcán"))
            self.assertIsNone(brain._llamar_ollama("qué es un río"))
            self.assertIsNone(brain._llamar_ollama("qué es un lago"))
        self.assertEqual(post.call_count, 1)
        self.assertFalse(brain.ollama_disponible())
        self.assertEqual(self.salida.getvalue().count("ollama no disponible"), 1)

    def test_la_pregunta_local_la_responde_claude(self):
        skills = {"musica": MagicMock()}
        with patch.object(brain, "_clasificar_peticion", return_value="local"), \
             patch.object(brain.requests, "post", side_effect=requests.Timeout), \
             patch.object(brain.cliente.messages, "create", return_value=fin("Un volcán es una montaña.")):
            self.assertEqual(brain.pensar("qué es un volcán", skills=skills), "Un volcán es una montaña.")

    def test_tras_la_pausa_se_vuelve_a_probar(self):
        with patch.object(brain.requests, "post", side_effect=requests.ConnectionError) as post:
            brain._llamar_ollama("uno")
            brain._ollama_pausado_hasta = 0.0
            brain._llamar_ollama("dos")
        self.assertEqual(post.call_count, 2)

    def test_resumen_de_memoria_va_a_claude_durante_la_pausa(self):
        haiku = MagicMock(content=[MagicMock(text="Resumen.")])
        with patch.object(brain.requests, "post", side_effect=requests.ConnectionError) as post, \
             patch.object(brain.cliente.messages, "create", return_value=haiku):
            self.assertEqual(brain.resumir_sesion("instr", "texto"), "Resumen.")
            self.assertEqual(brain.resumir_sesion("instr", "texto"), "Resumen.")
        self.assertEqual(post.call_count, 1)


# ── Piper falla ───────────────────────────────────────────────────
class TestPiperFalla(Base):
    def setUp(self):
        super().setUp()
        audio._voz_emergencia_avisada = False
        self.addCleanup(setattr, audio, "_voz_emergencia_avisada", False)
        audio.sd.reset_mock()
        audio.sd.get_stream.return_value.active = False
        audio.sd.play.side_effect = None
        for nombre in ("_track_rms_salida", "_escucha_parada", "ducking_on", "ducking_off"):
            p = patch.object(audio, nombre)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(audio.ui_bridge, "emit")
        self.emit = p.start()
        self.addCleanup(p.stop)
        p = patch.object(audio, "voz_emergencia_activa", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def dichos(self, run):
        return [base64.b64decode(c.kwargs["env"]["JARVIS_VOZ_TEXTO"]).decode("utf-8") for c in run.call_args_list]

    def test_habla_con_la_voz_de_windows_y_avisa_una_sola_vez(self):
        with patch.object(audio, "_sintetizar", side_effect=RuntimeError("piper roto")), \
             patch.object(audio.subprocess, "run") as run:
            audio.hablar("Son las diez.")
            audio.hablar("Mañana llueve.")
        self.assertEqual(self.dichos(run), [f"{audio.AVISO_VOZ_EMERGENCIA} Son las diez.", "Mañana llueve."])
        orden = run.call_args.args[0]
        self.assertEqual(orden[0], "powershell")
        self.assertNotIn("Mañana", " ".join(orden))   # el texto nunca va en la orden
        self.assertEqual(run.call_args.kwargs["timeout"], audio.VOZ_EMERGENCIA_TIMEOUT_S)
        self.assertFalse(audio.JARVIS_HABLANDO)

    def test_el_script_de_powershell_parsea(self):
        if not shutil.which("powershell"):
            self.skipTest("sin PowerShell")
        orden = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseInput("
                 "$env:JARVIS_SCRIPT, [ref]$null, [ref]$e) | Out-Null; $e.Count")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", orden], capture_output=True, text=True,
                           timeout=60, env={**os.environ, "JARVIS_SCRIPT": audio._SAPI})
        self.assertEqual(r.stdout.strip(), "0", r.stdout + r.stderr)

    def test_sin_voz_de_emergencia_avisa_en_pantalla_y_no_lanza(self):
        with patch.object(audio, "_sintetizar", side_effect=RuntimeError("piper roto")), \
             patch.object(audio.subprocess, "run", side_effect=OSError):
            audio.hablar("Son las diez.")
        self.emit.assert_any_call("error", message=audio.SIN_VOZ_PANTALLA)
        self.assertNotIn("piper roto", self.salida.getvalue())

    def test_por_frases_dice_el_resto_con_la_voz_de_emergencia(self):
        def sintetizar(frase):
            if frase.startswith("Segunda"):
                raise RuntimeError("piper roto")
            return np.zeros(4, dtype=np.int16), 22050
        with patch.object(audio, "_sintetizar", side_effect=sintetizar), \
             patch.object(audio.subprocess, "run") as run:
            audio.hablar_frases(["Primera frase.", "Segunda frase.", "Tercera frase."])
        self.assertEqual(audio.sd.play.call_count, 1)
        self.assertEqual(self.dichos(run), [f"{audio.AVISO_VOZ_EMERGENCIA} Segunda frase. Tercera frase."])


class TestVozEmergenciaActiva(unittest.TestCase):
    def test_variable_y_sistema(self):
        with patch.dict(os.environ, {"JARVIS_VOZ_EMERGENCIA": "0"}):
            self.assertFalse(audio.voz_emergencia_activa())
        with patch.dict(os.environ, {"JARVIS_VOZ_EMERGENCIA": "1"}):
            self.assertEqual(audio.voz_emergencia_activa(), os.name == "nt")


# ── Gmail con el token revocado ───────────────────────────────────
class TestGmailRevocado(Base):
    def test_cada_tool_dice_que_hay_que_reautorizar_sin_el_error_crudo(self):
        revocado = gexc.RefreshError("invalid_grant: Token has been expired or revoked.")
        llamadas = (lambda: gmail.leer_no_leidos(), lambda: gmail.leer_email(0),
                    lambda: gmail.archivar_email(0), lambda: gmail.buscar_emails("factura"),
                    lambda: gmail.enviar_email("x@example.com", "a", "b")[1],
                    lambda: gmail.leer_recruiters())
        for llamar in llamadas:
            gmail.cache_emails = [{"id": "1", "remitente": "a", "asunto": "b", "ts": 9e18}]
            with patch.object(gmail, "get_service", side_effect=revocado) as servicio:
                texto = llamar()
            with self.subTest(texto[:20]):
                self.assertIn("reautorizar.py gmail", texto)
                self.assertNotIn("invalid_grant", texto)
                self.assertEqual(servicio.call_count, 1)   # un intento, sin bucle de reintentos
        gmail.cache_emails = []

    def test_sin_red_lo_dice(self):
        with patch.object(gmail, "get_service", side_effect=ConnectionError):
            self.assertIn("no hay conexión con Gmail", gmail.leer_no_leidos())


# ── GitHub sin red ────────────────────────────────────────────────
class TestGitHubSinRed(Base):
    def setUp(self):
        super().setUp()
        p = patch.dict(os.environ, {"GITHUB_TOKEN": "x" * 8})
        p.start()
        self.addCleanup(p.stop)

    def test_un_solo_intento_y_frase_util(self):
        with patch.object(github.requests, "get", side_effect=requests.ConnectionError("con texto")) as get:
            datos = github.estado(["a/uno", "a/dos", "a/tres"])
        self.assertEqual(get.call_count, 1)   # sin red se corta: no se prueba repo a repo
        self.assertEqual(datos["causa"], github.SIN_RED)
        self.assertEqual(github.detalle(datos), github.MENSAJES[github.SIN_RED])
        self.assertNotIn("con texto", github.detalle(datos))

    def test_la_tool_de_voz_lo_dice(self):
        with patch.object(github.requests, "get", side_effect=requests.ConnectionError):
            res = brain._ejecutar("github_estado", {}, {}, "cómo va el CI")
        self.assertIn("no hay conexión", res["message"])

    def test_avisos_de_ci_lo_registran_una_vez_y_no_avisan(self):
        registro = []
        vigilante = avisos_ci.Vigilante(["a/uno"], consultar=MagicMock(side_effect=github.ErrorGitHub(github.SIN_RED)),
                                        registrar=registro.append)
        for _ in range(5):
            vigilante.comprobar()
        self.assertEqual(len(registro), 1)
        self.assertFalse(vigilante.pendientes)
        self.assertFalse(vigilante.parar)   # vuelve a intentarlo en la siguiente pasada (cada 5 min)


# ── Whisper sin GPU ───────────────────────────────────────────────
class TestWhisperSinGPU(Base):
    def test_cuda_rota_al_cargar_carga_en_cpu_y_avisa(self):
        cpu = NS(device="cpu")
        with patch.object(jarvis.whisper, "load_model", side_effect=[RuntimeError("CUDA error"), cpu]) as cargar:
            modelo, aviso = jarvis._cargar_whisper()
        self.assertIs(modelo, cpu)
        self.assertEqual(cargar.call_args.kwargs, {"device": "cpu"})
        self.assertEqual(aviso, jarvis.AVISO_WHISPER_CPU)

    def test_sin_gpu_avisa_y_con_gpu_no(self):
        with patch.object(jarvis.whisper, "load_model", return_value=NS(device="cpu")):
            self.assertEqual(jarvis._cargar_whisper()[1], jarvis.AVISO_WHISPER_CPU)
        with patch.object(jarvis.whisper, "load_model", return_value=NS(device="cuda:0")):
            self.assertEqual(jarvis._cargar_whisper()[1], "")

    def test_el_aviso_va_en_el_saludo_por_voz(self):
        with patch.object(jarvis, "aviso_whisper", jarvis.AVISO_WHISPER_CPU):
            self.assertEqual(jarvis._avisos_de_voz(("Google caducó.", "pantalla")),
                             f"Google caducó. {jarvis.AVISO_WHISPER_CPU}")
            self.assertEqual(jarvis._avisos_de_voz(None), jarvis.AVISO_WHISPER_CPU)
        with patch.object(jarvis, "aviso_whisper", ""):
            self.assertEqual(jarvis._avisos_de_voz(None), "")


# ── Fallos repetidos en el bucle principal ────────────────────────
class TestBucle(Base):
    def setUp(self):
        super().setUp()
        jarvis._bucle_sano()
        self.addCleanup(jarvis._bucle_sano)
        jarvis.audio.reset_mock()

    def test_lo_dice_una_vez_y_espera_cada_vez_mas_con_tope(self):
        with patch.object(jarvis.time, "sleep") as dormir:
            for _ in range(8):
                jarvis._fallo_en_bucle(OSError("micrófono desconectado"))
        jarvis.audio.hablar.assert_called_once_with(jarvis.FRASE_FALLO_TURNO)
        esperas = [c.args[0] for c in dormir.call_args_list]
        self.assertEqual(esperas, [1, 2, 4, 8, 16, 30, 30])

    def test_tras_un_turno_sano_vuelve_a_decirlo(self):
        with patch.object(jarvis.time, "sleep"):
            jarvis._fallo_en_bucle(OSError())
            jarvis._bucle_sano()
            jarvis._fallo_en_bucle(OSError())
        self.assertEqual(jarvis.audio.hablar.call_count, 2)

    def test_la_voz_rota_no_rompe_el_bucle(self):
        jarvis.audio.hablar.side_effect = RuntimeError("sin audio")
        self.addCleanup(setattr, jarvis.audio.hablar, "side_effect", None)
        jarvis._fallo_en_bucle(OSError())   # no lanza


if __name__ == "__main__":
    unittest.main()
