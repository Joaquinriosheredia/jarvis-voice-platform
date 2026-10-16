"""doctor.py: cada comprobación en OK / AVISO / ERROR, salida, código de salida y
que nunca imprime secretos. Todo con mocks: sin disco real (salvo directorios
temporales), sin red, sin audio y sin git real."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doctor  # noqa: E402

CLAVE_FALSA = "sk-ant-api03-CLAVEFALSADETEST0123456789abcdef"
CLAVE_EL    = "falsa" * 4  # se construye: un literal con forma de clave dispara gitleaks
CONSTANTES = {
    ("brain.py", "OLLAMA_URL"): "http://127.0.0.1:11434",
    ("brain.py", "OLLAMA_MODEL"): "qwen2.5:3b",
    ("brain.py", "CLAUDE_MODEL"): "claude-haiku-4-5-20251001",
    ("audio.py", "VOICE_MODEL"): r"C:\Jarvis\voices\es_ES-davefx-medium.onnx",
    (os.path.join("skills", "musica.py"), "VLC_PATH"): r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    (os.path.join("skills", "gmail.py"), "CREDENTIALS_FILE"): r"C:\Jarvis-secrets\credentials.json",
    (os.path.join("skills", "gmail.py"), "TOKEN_FILE"): r"C:\Jarvis-secrets\token.json",
    (os.path.join("skills", "calendario.py"), "TOKEN_CAL"): r"C:\Jarvis-secrets\token_calendar.json",
    ("tts_elevenlabs.py", "ELEVENLABS_JSON"): r"C:\Jarvis-secrets\elevenlabs.json",
}


def completado(salida="", codigo=0):
    return subprocess.CompletedProcess([], codigo, salida, "")


class Base(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(doctor, "constante", side_effect=lambda a, n: CONSTANTES[(a, n)]),
                  patch.object(doctor, "valores_env", return_value={}),
                  patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for var in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "JARVIS_TTS", "ELEVENLABS_API_KEY",
                    "ELEVENLABS_VOICE_ID"):
            os.environ.pop(var, None)

    def estado(self, r, esperado):
        self.assertEqual(r["estado"], esperado, r)
        if esperado == doctor.OK:
            self.assertEqual(r["accion"], "")
        else:
            self.assertTrue(r["accion"], r)


def existe(*rutas):
    rutas = {os.path.normcase(r) for r in rutas}
    return lambda r: os.path.normcase(r) in rutas


# ── Python y dependencias ─────────────────────────────────────────
class TestPython(Base):
    def test_venv_aviso_y_version_antigua(self):
        venv = os.path.join(doctor.RAIZ, "venv")
        with patch.object(doctor.sys, "prefix", venv):
            self.estado(doctor.check_python(), doctor.OK)
        with patch.object(doctor.sys, "prefix", r"C:\Python314"):
            self.estado(doctor.check_python(), doctor.AVISO)
        with patch.object(doctor.sys, "version_info", (3, 10, 0)):
            self.estado(doctor.check_python(), doctor.ERROR)


class TestDependencias(Base):
    def correr(self, versiones, especificaciones=True):
        def version(nombre):
            if nombre not in versiones:
                raise doctor.importlib.metadata.PackageNotFoundError(nombre)
            return versiones[nombre]
        with patch.object(doctor, "_requisitos", return_value=[("numpy", "2.4.4"), ("python-dotenv", "1.2.2")]), \
             patch.object(doctor.importlib.metadata, "version", side_effect=version), \
             patch.object(doctor.importlib.metadata, "packages_distributions",
                          return_value={"numpy": ["numpy"], "dotenv": ["python-dotenv"]}), \
             patch.object(doctor.importlib.util, "find_spec", return_value=object() if especificaciones else None):
            return doctor.check_dependencias()

    def test_ok_aviso_error(self):
        self.estado(self.correr({"numpy": "2.4.4", "python-dotenv": "1.2.2"}), doctor.OK)
        r = self.correr({"numpy": "2.3.0", "python-dotenv": "1.2.2"})
        self.estado(r, doctor.AVISO)
        self.assertIn("numpy 2.3.0 (fijada 2.4.4)", r["detalle"])
        r = self.correr({"numpy": "2.4.4"})
        self.estado(r, doctor.ERROR)
        self.assertIn("python-dotenv", r["detalle"])
        self.estado(self.correr({"numpy": "2.4.4", "python-dotenv": "1.2.2"}, especificaciones=False), doctor.ERROR)

    def test_requisitos_lee_nombre_y_version(self):
        reqs = doctor._requisitos()  # el requirements.txt del repo (solo lectura)
        self.assertIn(("anthropic", "0.89.0"), reqs)
        self.assertTrue(all("==" not in n for n, _ in reqs))


# ── Binarios ──────────────────────────────────────────────────────
class TestBinarios(Base):
    def test_ffmpeg(self):
        with patch.object(doctor.shutil, "which", return_value="ffmpeg.exe"):
            self.estado(doctor.check_ffmpeg(), doctor.OK)
        with patch.object(doctor.shutil, "which", return_value=None):
            self.estado(doctor.check_ffmpeg(), doctor.ERROR)

    def test_vlc(self):
        with patch.object(doctor.os.path, "isfile", existe(CONSTANTES[(os.path.join("skills", "musica.py"), "VLC_PATH")])):
            self.estado(doctor.check_vlc(), doctor.OK)
        with patch.object(doctor.os.path, "isfile", return_value=False):
            r = doctor.check_vlc()
        self.estado(r, doctor.AVISO)
        self.assertNotIn("Program Files", r["detalle"])  # sin rutas fuera de C:\Jarvis

    def test_ytdlp_en_path_o_junto_al_python(self):
        with patch.object(doctor.shutil, "which", return_value=None), \
             patch.object(doctor.os.path, "isfile", return_value=True):
            self.estado(doctor.check_ytdlp(), doctor.OK)
        with patch.object(doctor.shutil, "which", return_value=None), \
             patch.object(doctor.os.path, "isfile", return_value=False):
            self.estado(doctor.check_ytdlp(), doctor.AVISO)

    def test_node_y_electron(self):
        with patch.object(doctor.shutil, "which", side_effect=lambda b: b if b == "node" else None):
            r = doctor.check_node()
        self.estado(r, doctor.AVISO)
        self.assertIn("npm", r["detalle"])
        with patch.object(doctor.shutil, "which", return_value="x"):
            self.estado(doctor.check_node(), doctor.OK)
        with patch.object(doctor.os.path, "isdir", return_value=True):
            self.estado(doctor.check_electron(), doctor.OK)
        with patch.object(doctor.os.path, "isdir", return_value=False):
            self.estado(doctor.check_electron(), doctor.AVISO)


# ── Ollama ────────────────────────────────────────────────────────
class TestOllama(Base):
    def test_con_modelo_sin_modelo_y_caido(self):
        tags = json.dumps({"models": [{"name": "qwen2.5:3b"}, {"name": "llama3:8b"}]})
        with patch.object(doctor, "http_get", return_value=(200, tags)) as get:
            self.estado(doctor.check_ollama(), doctor.OK)
        get.assert_called_once_with("http://127.0.0.1:11434/api/tags")  # solo lista, no genera
        with patch.object(doctor, "http_get", return_value=(200, json.dumps({"models": []}))):
            r = doctor.check_ollama()
        self.estado(r, doctor.AVISO)
        self.assertIn("ollama pull qwen2.5:3b", r["accion"])
        with patch.object(doctor, "http_get", side_effect=ConnectionRefusedError()):
            self.estado(doctor.check_ollama(), doctor.AVISO)
        with patch.object(doctor, "http_get", return_value=(500, "")):
            self.estado(doctor.check_ollama(), doctor.AVISO)


# ── Audio, Piper, Whisper, CUDA ───────────────────────────────────
class TestAudio(Base):
    def con_sd(self, entrada, salida):
        sd = types.ModuleType("sounddevice")
        sd.query_devices = MagicMock(side_effect=lambda kind: {"input": entrada, "output": salida}[kind])
        sd.rec = MagicMock(side_effect=AssertionError("no debe grabar"))
        return patch.dict(sys.modules, {"sounddevice": sd})

    def test_dispositivos(self):
        with self.con_sd({"name": "Mic"}, {"name": "Altavoz"}):
            self.estado(doctor.check_audio(), doctor.OK)
        with self.con_sd(None, {"name": "Altavoz"}):
            r = doctor.check_audio()
        self.estado(r, doctor.ERROR)
        self.assertIn("entrada", r["detalle"])

    def test_sin_portaudio(self):
        with patch.dict(sys.modules, {"sounddevice": None}):
            self.estado(doctor.check_audio(), doctor.ERROR)

    def test_piper(self):
        voz = CONSTANTES[("audio.py", "VOICE_MODEL")]
        with patch.object(doctor.importlib.util, "find_spec", return_value=object()), \
             patch.object(doctor.os.path, "isfile", existe(voz, voz + ".json")):
            self.estado(doctor.check_piper(), doctor.OK)
        with patch.object(doctor.importlib.util, "find_spec", return_value=object()), \
             patch.object(doctor.os.path, "isfile", existe(voz)):
            self.estado(doctor.check_piper(), doctor.ERROR)  # falta el .json
        with patch.object(doctor.importlib.util, "find_spec", return_value=None):
            self.estado(doctor.check_piper(), doctor.ERROR)

    def test_whisper_cache_sin_descargar(self):
        with patch.object(doctor, "modelo_whisper", return_value="small"), \
             patch.object(doctor.importlib.util, "find_spec", return_value=object()), \
             patch.dict(os.environ, {"XDG_CACHE_HOME": r"C:\cache"}):
            with patch.object(doctor.os.path, "isfile", existe(os.path.join(r"C:\cache", "whisper", "small.pt"))):
                self.estado(doctor.check_whisper(), doctor.OK)
            with patch.object(doctor.os.path, "isfile", return_value=False):
                r = doctor.check_whisper()
        self.estado(r, doctor.AVISO)
        self.assertNotIn("cache", r["detalle"].lower().replace("caché", ""))  # sin la ruta del usuario
        with patch.object(doctor, "modelo_whisper", return_value="small"), \
             patch.object(doctor.importlib.util, "find_spec", return_value=None):
            self.estado(doctor.check_whisper(), doctor.ERROR)

    def test_modelo_whisper_sale_de_jarvis_py(self):
        self.assertEqual(doctor.modelo_whisper(), "small")  # lectura del fuente, sin importar jarvis

    def test_cuda_es_aviso_no_error(self):
        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        with patch.object(doctor.importlib.util, "find_spec", return_value=object()), \
             patch.dict(sys.modules, {"torch": torch}):
            self.estado(doctor.check_cuda(), doctor.AVISO)
            torch.cuda.is_available = lambda: True
            self.estado(doctor.check_cuda(), doctor.OK)
        with patch.object(doctor.importlib.util, "find_spec", return_value=None):
            self.estado(doctor.check_cuda(), doctor.AVISO)


# ── Credenciales ──────────────────────────────────────────────────
class TestCredenciales(Base):
    def test_carpeta_y_env(self):
        with patch.object(doctor.os.path, "isdir", return_value=True):
            self.estado(doctor.check_secretos_carpeta(), doctor.OK)
        with patch.object(doctor.os.path, "isdir", return_value=False):
            self.estado(doctor.check_secretos_carpeta(), doctor.ERROR)
        with patch.object(doctor.os.path, "isfile", return_value=True):
            self.estado(doctor.check_env(), doctor.OK)
        with patch.object(doctor.os.path, "isfile", return_value=False):
            self.estado(doctor.check_env(), doctor.AVISO)

    def test_anthropic_key_solo_longitud(self):
        self.estado(doctor.check_anthropic_key(), doctor.ERROR)
        os.environ["ANTHROPIC_API_KEY"] = CLAVE_FALSA
        r = doctor.check_anthropic_key()
        self.estado(r, doctor.OK)
        self.assertIn(f"{len(CLAVE_FALSA)} caracteres", r["detalle"])
        self.assertNotIn("sk-", r["detalle"])

    def test_la_clave_del_env_cuenta(self):
        with patch.object(doctor, "valores_env", return_value={"ANTHROPIC_API_KEY": CLAVE_FALSA}):
            self.estado(doctor.check_anthropic_key(), doctor.OK)

    def test_tavily_es_aviso(self):
        self.estado(doctor.check_tavily_key(), doctor.AVISO)
        os.environ["TAVILY_API_KEY"] = "tvly-x" * 5
        self.estado(doctor.check_tavily_key(), doctor.OK)

    def test_credentials_json(self):
        with patch.object(doctor.os.path, "isfile", return_value=True):
            self.estado(doctor.check_google_credentials(), doctor.OK)
        with patch.object(doctor.os.path, "isfile", return_value=False):
            self.estado(doctor.check_google_credentials(), doctor.AVISO)


class TestTokens(unittest.TestCase):
    def token(self, datos):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta = os.path.join(tmp.name, "token.json")
        if datos is not None:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f)
        return doctor._check_token(ruta, "Gmail", "comando-de-reautorizacion")

    def test_estados(self):
        futuro = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        self.assertEqual(self.token({"token": "x", "expiry": futuro})["estado"], doctor.OK)
        r = self.token({"token": "x", "expiry": pasado, "refresh_token": "r"})
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("se renovará", r["detalle"])
        r = self.token({"token": "x", "expiry": pasado})
        self.assertEqual(r["estado"], doctor.AVISO)
        self.assertIn("comando-de-reautorizacion", r["accion"])
        self.assertEqual(self.token(None)["estado"], doctor.AVISO)

    def test_no_imprime_el_token(self):
        r = self.token({"token": "ya29.SECRETO-DEL-TOKEN", "refresh_token": "1//REFRESCO", "expiry": None})
        self.assertNotIn("ya29", json.dumps(r))
        self.assertNotIn("REFRESCO", json.dumps(r))


class TestElevenLabs(Base):
    def test_solo_si_jarvis_tts_elevenlabs(self):
        self.estado(doctor.check_elevenlabs(), doctor.OK)
        os.environ["JARVIS_TTS"] = "elevenlabs"
        with patch.object(doctor.os.path, "isfile", return_value=False):
            self.estado(doctor.check_elevenlabs(), doctor.AVISO)
        os.environ.update(ELEVENLABS_API_KEY=CLAVE_EL, ELEVENLABS_VOICE_ID="voz123")
        r = doctor.check_elevenlabs()
        self.estado(r, doctor.OK)
        self.assertNotIn(CLAVE_EL, r["detalle"])


# ── Seguridad del repo ────────────────────────────────────────────
class TestSeguridad(Base):
    def test_secretos_dentro_del_repo(self):
        with patch.object(doctor, "SECRETOS", os.path.join(doctor.RAIZ, "secretos")):
            self.estado(doctor.check_secretos_fuera_del_repo(), doctor.ERROR)
        with patch.object(doctor, "SECRETOS", os.path.join(os.path.dirname(doctor.RAIZ), "Jarvis-secrets")):
            self.estado(doctor.check_secretos_fuera_del_repo(), doctor.OK)

    def test_gitignore(self):
        with patch.object(doctor, "git", return_value=completado()):
            self.estado(doctor.check_gitignore(), doctor.OK)
        with patch.object(doctor, "git", side_effect=lambda *a: completado(codigo=1 if a[-1] == "token_calendar.json" else 0)):
            r = doctor.check_gitignore()
        self.estado(r, doctor.AVISO)
        self.assertIn("token_calendar.json", r["detalle"])

    def test_secretos_trackeados(self):
        with patch.object(doctor, "git", return_value=completado("brain.py\ntests/test_secretos.py\nskills/operador.py\n")):
            self.estado(doctor.check_secretos_trackeados(), doctor.OK)
        with patch.object(doctor, "git", return_value=completado("brain.py\nconfig/.env\ntoken_calendar.json\n")):
            r = doctor.check_secretos_trackeados()
        self.estado(r, doctor.ERROR)
        self.assertIn("config/.env", r["detalle"])
        with patch.object(doctor, "git", return_value=completado(codigo=128)):
            self.estado(doctor.check_secretos_trackeados(), doctor.AVISO)


# ── Puerto, zona horaria, proyectos, logs, git ────────────────────
class TestPuerto(Base):
    def sock(self, codigo):
        s = MagicMock()
        s.__enter__.return_value.connect_ex.return_value = codigo
        return patch.object(doctor.socket, "socket", return_value=s)

    def test_libre_jarvis_y_otro(self):
        with self.sock(10061):
            self.estado(doctor.check_puerto(), doctor.OK)
        with self.sock(0), patch.object(doctor, "_duenio_del_puerto",
                                        return_value=(42, "python.exe", r"C:\Jarvis\venv\Scripts\python.exe jarvis.py")):
            self.estado(doctor.check_puerto(), doctor.OK)
        with self.sock(0), patch.object(doctor, "_duenio_del_puerto", return_value=(7, "node.exe", "node server.js")):
            r = doctor.check_puerto()
        self.estado(r, doctor.ERROR)
        self.assertIn("node.exe", r["detalle"])
        with self.sock(0), patch.object(doctor, "_duenio_del_puerto", side_effect=ImportError):
            self.estado(doctor.check_puerto(), doctor.ERROR)


class TestVarios(Base):
    def test_zona_horaria(self):
        import zoneinfo  # sin depender de que tzdata esté instalado (en CI no lo está)
        with patch.object(zoneinfo, "ZoneInfo", return_value=object()) as zona:
            self.estado(doctor.check_zona_horaria(), doctor.OK)
        zona.assert_called_once_with("Europe/Madrid")
        with patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("x")):
            self.estado(doctor.check_zona_horaria(), doctor.ERROR)

    def test_proyectos(self):
        from skills import operador
        proyectos = {"jarvis": r"C:\Jarvis", "wsl": "\\\\wsl.localhost\\Ubuntu\\home\\u\\x"}
        with patch.dict(operador.PROYECTOS, proyectos, clear=True), \
             patch.object(doctor.shutil, "which", return_value="wsl.exe"), \
             patch.object(doctor.os.path, "isdir", return_value=True):
            self.estado(doctor.check_proyectos(), doctor.OK)
        with patch.dict(operador.PROYECTOS, proyectos, clear=True), \
             patch.object(doctor.shutil, "which", return_value=None), \
             patch.object(doctor.os.path, "isdir", return_value=True):
            r = doctor.check_proyectos()
        self.estado(r, doctor.AVISO)
        self.assertIn("sin WSL: wsl", r["detalle"])
        self.assertNotIn("home", r["detalle"])  # solo alias, nunca la ruta
        with patch.dict(operador.PROYECTOS, {"jarvis": r"C:\Jarvis"}, clear=True), \
             patch.object(doctor.os.path, "isdir", return_value=False):
            self.estado(doctor.check_proyectos(), doctor.AVISO)

    def test_logs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(doctor, "RAIZ", tmp):
            self.estado(doctor.check_logs(), doctor.AVISO)  # no existe logs/
            os.makedirs(os.path.join(tmp, "logs"))
            with open(os.path.join(tmp, "logs", "turnos.jsonl"), "w") as f:
                f.write("x" * 2048)
            r = doctor.check_logs()
            self.estado(r, doctor.OK)
            self.assertIn("2 KB", r["detalle"])
            with patch.object(doctor.os.path, "getsize", return_value=doctor.LOG_GRANDE + 1):
                self.estado(doctor.check_logs(), doctor.AVISO)
            with patch.object(doctor.os, "access", return_value=False):
                self.estado(doctor.check_logs(), doctor.ERROR)

    def test_git(self):
        def git(salidas):
            return patch.object(doctor, "git", side_effect=lambda *a: salidas[a[0]])
        with git({"status": completado(""), "rev-list": completado("0\t0\n")}):
            self.estado(doctor.check_git(), doctor.OK)
        with git({"status": completado(" M brain.py\n"), "rev-list": completado("2\t1\n")}):
            r = doctor.check_git()
        self.estado(r, doctor.AVISO)
        self.assertIn("1 archivo(s) con cambios; 2 commit(s) sin subir; 1 commit(s) por bajar", r["detalle"])
        with git({"status": completado(codigo=128), "rev-list": completado()}):
            self.estado(doctor.check_git(), doctor.AVISO)


# ── --live ────────────────────────────────────────────────────────
class TestLive(Base):
    def test_sin_clave_no_llama_a_la_red(self):
        with patch.object(doctor, "_post") as post:
            self.estado(doctor.check_live_anthropic(), doctor.ERROR)
        post.assert_not_called()
        os.environ["JARVIS_TTS"] = "elevenlabs"
        with patch.object(doctor.os.path, "isfile", return_value=False), patch.object(doctor, "_get") as get:
            self.estado(doctor.check_live_elevenlabs(), doctor.ERROR)
        get.assert_not_called()

    def test_llamada_minima_y_codigos(self):
        os.environ["ANTHROPIC_API_KEY"] = CLAVE_FALSA
        with patch.object(doctor, "_post", return_value=200) as post:
            r = doctor.check_live_anthropic()
        self.estado(r, doctor.OK)
        url, cabeceras, cuerpo = post.call_args.args
        self.assertEqual(cuerpo["max_tokens"], 1)
        self.assertEqual(cuerpo["model"], "claude-haiku-4-5-20251001")
        self.assertNotIn(CLAVE_FALSA, r["detalle"])
        with patch.object(doctor, "_post", return_value=401):
            r = doctor.check_live_anthropic()
        self.estado(r, doctor.ERROR)
        self.assertIn("HTTP 401", r["detalle"])
        with patch.object(doctor, "_post", side_effect=OSError("x-api-key: " + CLAVE_FALSA)):
            r = doctor.check_live_anthropic()
        self.estado(r, doctor.ERROR)
        self.assertNotIn(CLAVE_FALSA, json.dumps(r))  # solo el tipo de excepción

    def test_elevenlabs_consulta_la_voz_sin_sintetizar(self):
        os.environ.update(JARVIS_TTS="elevenlabs", ELEVENLABS_API_KEY=CLAVE_EL,
                          ELEVENLABS_VOICE_ID="voz123")
        with patch.object(doctor, "_get", return_value=200) as get, patch.object(doctor, "_post") as post:
            self.estado(doctor.check_live_elevenlabs(), doctor.OK)
        self.assertEqual(get.call_args.args[0], "https://api.elevenlabs.io/v1/voices/voz123")
        post.assert_not_called()
        os.environ["JARVIS_TTS"] = "piper"
        with patch.object(doctor, "_get") as get:
            self.estado(doctor.check_live_elevenlabs(), doctor.OK)
        get.assert_not_called()

    def test_live_solo_con_el_flag(self):
        with patch.object(doctor, "COMPROBACIONES", []), \
             patch.object(doctor, "COMPROBACIONES_LIVE", [("l", "L", lambda: doctor.resultado(doctor.OK, "x"))]):
            self.assertEqual(doctor.ejecutar(live=False), [])
            self.assertEqual([r["id"] for r in doctor.ejecutar(live=True)], ["l"])


# ── Ejecución, salida y código de salida ──────────────────────────
class TestSalida(Base):
    def falsas(self, *estados):
        return [(f"c{i}", f"Comprobación {i}", (lambda e=e: doctor.resultado(e, f"detalle {e}", "arréglalo")))
                for i, e in enumerate(estados)]

    def main(self, comprobaciones, *args):
        salida = io.StringIO()
        with patch.object(doctor, "COMPROBACIONES", comprobaciones), contextlib.redirect_stdout(salida):
            codigo = doctor.main(list(args))
        return codigo, salida.getvalue()

    def test_codigo_de_salida(self):
        self.assertEqual(self.main(self.falsas(doctor.OK, doctor.AVISO))[0], 0)
        self.assertEqual(self.main(self.falsas(doctor.OK, doctor.ERROR))[0], 1)

    def test_tabla(self):
        _, texto = self.main(self.falsas(doctor.OK, doctor.AVISO, doctor.ERROR))
        self.assertIn("Comprobación 1", texto)
        self.assertIn("→ arréglalo", texto)
        self.assertIn("ERROR: 1 OK, 1 AVISO, 1 ERROR", texto)

    def test_json_valido_y_estable(self):
        comprobaciones = self.falsas(doctor.OK, doctor.AVISO)
        _, a = self.main(comprobaciones, "--json")
        _, b = self.main(comprobaciones, "--json")
        self.assertEqual(a, b)
        datos = json.loads(a)
        self.assertEqual(list(datos), ["version", "estado", "resumen", "comprobaciones"])
        self.assertEqual(datos["version"], 1)
        self.assertEqual(datos["estado"], doctor.AVISO)
        self.assertEqual(datos["resumen"], {"OK": 1, "AVISO": 1, "ERROR": 0})
        self.assertEqual(list(datos["comprobaciones"][0]), ["id", "nombre", "estado", "detalle", "accion"])
        self.assertTrue(a.isascii())

    def test_una_excepcion_no_tumba_el_resto(self):
        def rota():
            raise ValueError("detalle interno " + CLAVE_FALSA)
        comprobaciones = [("rota", "Rota", rota)] + self.falsas(doctor.OK)
        codigo, texto = self.main(comprobaciones, "--json")
        datos = json.loads(texto)
        self.assertEqual(codigo, 1)
        self.assertEqual([c["estado"] for c in datos["comprobaciones"]], [doctor.ERROR, doctor.OK])
        self.assertIn("ValueError", datos["comprobaciones"][0]["detalle"])
        self.assertNotIn(CLAVE_FALSA, texto)

    def test_red_de_seguridad_enmascara_secretos_en_detalles(self):
        comprobaciones = [("x", "X", lambda: doctor.resultado(doctor.AVISO, f"API_KEY = \"{CLAVE_FALSA}\"", "a"))]
        _, texto = self.main(comprobaciones)
        self.assertNotIn(CLAVE_FALSA, texto)


class TestSinSecretosDeExtremoAExtremo(Base):
    def test_todas_las_comprobaciones_con_clave_falsa_no_la_imprimen(self):
        """Doctor completo con todo el entorno simulado: ni la tabla ni el JSON llevan la clave."""
        os.environ.update(ANTHROPIC_API_KEY=CLAVE_FALSA, TAVILY_API_KEY="tvly-" + CLAVE_FALSA,
                          JARVIS_TTS="elevenlabs", ELEVENLABS_API_KEY=CLAVE_FALSA, ELEVENLABS_VOICE_ID="voz")
        sd = types.ModuleType("sounddevice")
        sd.query_devices = lambda kind: {"name": kind}
        parches = [patch.object(doctor, "http_get", side_effect=OSError()),
                   patch.object(doctor, "git", return_value=completado("")),
                   patch.object(doctor, "_post", return_value=200),
                   patch.object(doctor, "_get", return_value=200),
                   patch.object(doctor, "modelo_whisper", return_value="small"),
                   patch.object(doctor.shutil, "which", return_value=None),
                   patch.object(doctor.os.path, "isfile", return_value=False),
                   patch.dict(sys.modules, {"sounddevice": sd})]
        for p in parches:
            p.start()
        try:
            for args in ([], ["--json"], ["--live"], ["--json", "--live"]):
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida):
                    doctor.main(args)
                texto = salida.getvalue()
                self.assertNotIn(CLAVE_FALSA, texto, args)
                self.assertNotIn("CLAVEFALSA", texto, args)
                if "--json" in args:
                    json.loads(texto)
        finally:
            for p in reversed(parches):
                p.stop()


class TestSoloLectura(unittest.TestCase):
    def test_el_doctor_no_escribe_ni_borra(self):
        with open(doctor.__file__, encoding="utf-8") as f:
            fuente = f.read()
        for prohibido in ('open(ruta, "w"', "os.remove", "os.unlink", "shutil.rmtree", "os.makedirs",
                          ".write(", "load_model(", "sd.rec", "/api/generate"):
            self.assertNotIn(prohibido, fuente)


if __name__ == "__main__":
    unittest.main()
