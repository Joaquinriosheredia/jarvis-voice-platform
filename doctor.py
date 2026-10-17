"""Diagnóstico del entorno de JARVIS sin probar por voz.

    venv\\Scripts\\python.exe doctor.py [--json] [--live]

Cada comprobación devuelve OK / AVISO / ERROR, un texto corto y, si falla, la acción
para arreglarlo. Código de salida: 0 sin ERROR, 1 con algún ERROR (los AVISO no cuentan).

Solo lectura: no escribe ni borra archivos, no graba audio, no carga Whisper ni genera
texto con Ollama. La única red es la local (Ollama en 127.0.0.1) salvo con --live, que
hace una llamada mínima a Anthropic (max_tokens=1), renueva en memoria los tokens de
Gmail y Calendar con su refresh_token (sin leer correo ni eventos y sin guardar nada)
y, si JARVIS_TTS=elevenlabs, consulta la voz configurada sin sintetizar. Sin --live los
tokens de Google salen como "no verificado". Nunca imprime valores de claves ni rutas
fuera de C:\\Jarvis y C:\\Jarvis-secrets.
"""
import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request

RAIZ      = os.path.dirname(os.path.abspath(__file__))
SECRETOS  = r"C:\Jarvis-secrets"
ENV_FILE  = os.path.join(SECRETOS, ".env")
PUERTO_UI = 8765
VERSION_JSON = 1
OK, AVISO, ERROR = "OK", "AVISO", "ERROR"
LOG_GRANDE = 50 * 1024 * 1024
# Archivos que nunca deben estar en git (nombre, sin ruta)
PATRONES_SECRETOS = (r"\.env(\..*)?", r".*\.pem", r".*\.key", r"id_rsa.*", r"credentials.*\.json",
                     r"token.*\.json", r"elevenlabs\.json")
# Rutas de muestra que .gitignore debe cubrir
DEBEN_IGNORARSE = (".env", "credentials.json", "credentials_backup.json", "token.json", "token_calendar.json", "logs/turnos.jsonl")


def resultado(estado, detalle, accion=""):
    return {"estado": estado, "detalle": detalle, "accion": accion if estado != OK else ""}


# ── Lectura sin efectos ───────────────────────────────────────────
def constante(archivo, nombre):
    """Valor literal de NOMBRE = ... en un .py del proyecto, sin importarlo."""
    with open(os.path.join(RAIZ, archivo), encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nombre for t in nodo.targets):
            return ast.literal_eval(nodo.value)
    raise KeyError(f"{nombre} no está en {archivo}")


def modelo_whisper():
    with open(os.path.join(RAIZ, "jarvis.py"), encoding="utf-8") as f:
        m = re.search(r"whisper\.load_model\(\s*[\"']([\w.-]+)[\"']", f.read())
    if not m:
        raise KeyError("whisper.load_model no está en jarvis.py")
    return m.group(1)


def valores_env():
    """Variables del .env de JARVIS sin tocar os.environ (load_dotenv no pisa las del sistema)."""
    if not os.path.isfile(ENV_FILE):
        return {}
    try:
        from dotenv import dotenv_values
        return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    except ImportError:
        return {}


def variable(nombre):
    """Como la ve JARVIS en ejecución: el entorno del sistema manda sobre el .env."""
    return os.environ.get(nombre) or valores_env().get(nombre) or ""


def git(*args):
    return subprocess.run(["git", "-C", RAIZ, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=15)


def http_get(url, timeout=2):
    """(código, cuerpo) de una URL local."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


# ── Comprobaciones ────────────────────────────────────────────────
def check_python():
    v = tuple(sys.version_info[:3])
    version = ".".join(map(str, v))
    venv = os.path.normcase(os.path.join(RAIZ, "venv"))
    if v[:2] < (3, 11):
        return resultado(ERROR, f"Python {version}: hace falta 3.11 o superior",
                         "Instala Python 3.11+ y recrea el venv: python -m venv venv")
    if os.path.normcase(os.path.abspath(sys.prefix)) != venv:
        return resultado(AVISO, f"Python {version}, pero no es el venv del proyecto",
                         r"Ejecuta con venv\Scripts\python.exe doctor.py")
    return resultado(OK, f"Python {version} del venv del proyecto")


def _requisitos():
    reqs = []
    with open(os.path.join(RAIZ, "requirements.txt"), encoding="utf-8") as f:
        for linea in f:
            linea = linea.split("#", 1)[0].strip()
            m = re.fullmatch(r"([A-Za-z0-9_.\-]+)\s*==\s*([^\s;]+)", linea)
            if m:
                reqs.append((m.group(1), m.group(2)))
    return reqs


def _normalizar(nombre):
    return re.sub(r"[-_.]+", "-", nombre).lower()


def check_dependencias():
    reqs = _requisitos()
    modulos = {}  # distribución normalizada -> módulos de primer nivel
    for modulo, dists in importlib.metadata.packages_distributions().items():
        for d in dists:
            modulos.setdefault(_normalizar(d), []).append(modulo)
    faltan, no_importables, otra_version = [], [], []
    for nombre, fijada in reqs:
        try:
            instalada = importlib.metadata.version(nombre)
        except importlib.metadata.PackageNotFoundError:
            faltan.append(nombre)
            continue
        mods = [m for m in modulos.get(_normalizar(nombre), []) if not m.startswith("_")]
        if mods and not any(importlib.util.find_spec(m) for m in mods):
            no_importables.append(nombre)
        if instalada != fijada:
            otra_version.append(f"{nombre} {instalada} (fijada {fijada})")
    if faltan or no_importables:
        return resultado(ERROR, f"Faltan {len(faltan) + len(no_importables)} de {len(reqs)}: "
                         + ", ".join(faltan + no_importables)[:300],
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    if otra_version:
        return resultado(AVISO, f"{len(reqs)} instaladas; {len(otra_version)} con otra versión: "
                         + ", ".join(otra_version)[:300],
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    return resultado(OK, f"{len(reqs)}/{len(reqs)} paquetes de requirements.txt instalados")


def check_ffmpeg():
    if shutil.which("ffmpeg"):
        return resultado(OK, "ffmpeg en el PATH")
    return resultado(ERROR, "ffmpeg no está en el PATH: Whisper no puede leer el audio",
                     "Instala ffmpeg (winget install Gyan.FFmpeg) y reabre la terminal")


def check_vlc():
    if os.path.isfile(constante(os.path.join("skills", "musica.py"), "VLC_PATH")):
        return resultado(OK, "VLC en la ruta que usa skills/musica.py")
    return resultado(AVISO, "VLC no está en la ruta que usa skills/musica.py: la música no sonará",
                     "Instala VLC (winget install VideoLAN.VLC) en la ruta por defecto")


def check_ytdlp():
    # subprocess en Windows busca primero junto al python.exe (venv\Scripts) y luego en el PATH
    junto = os.path.join(os.path.dirname(sys.executable), "yt-dlp.exe")
    if shutil.which("yt-dlp") or os.path.isfile(junto):
        return resultado(OK, "yt-dlp disponible")
    return resultado(AVISO, "yt-dlp no encontrado: la música no sonará",
                     r"venv\Scripts\python.exe -m pip install yt-dlp")


def check_node():
    falta = [b for b in ("node", "npm") if not shutil.which(b)]
    if falta:
        return resultado(AVISO, f"No está en el PATH: {', '.join(falta)} (la UI de Electron no arranca)",
                         "Instala Node.js LTS (winget install OpenJS.NodeJS.LTS)")
    return resultado(OK, "node y npm en el PATH")


def check_electron():
    if os.path.isdir(os.path.join(RAIZ, "app", "node_modules", "electron")):
        return resultado(OK, "Electron instalado en app/node_modules")
    return resultado(AVISO, "Electron no está instalado en app/node_modules",
                     "cd app && npm install")


def check_ollama():
    url, modelo = constante("brain.py", "OLLAMA_URL"), constante("brain.py", "OLLAMA_MODEL")
    try:
        codigo, cuerpo = http_get(f"{url}/api/tags")
    except OSError:
        return resultado(AVISO, "Ollama no responde en el puerto local: todo irá a Claude",
                         "Arranca Ollama (ollama serve) o abre la app de Ollama")
    if codigo != 200:
        return resultado(AVISO, f"Ollama responde HTTP {codigo}", "Reinicia Ollama")
    nombres = {m.get("name") for m in json.loads(cuerpo).get("models", [])}
    if modelo in nombres or f"{modelo}:latest" in nombres:
        return resultado(OK, f"Ollama responde y tiene {modelo}")
    return resultado(AVISO, f"Ollama responde pero no tiene {modelo}", f"ollama pull {modelo}")


def check_audio():
    try:
        import sounddevice as sd
    except Exception as e:  # sin PortAudio: OSError al importar
        return resultado(ERROR, f"sounddevice no carga ({type(e).__name__})",
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    faltan = []
    for tipo, nombre in (("input", "entrada"), ("output", "salida")):
        try:
            if not sd.query_devices(kind=tipo):
                faltan.append(nombre)
        except Exception:
            faltan.append(nombre)
    if faltan:
        return resultado(ERROR, f"Sin dispositivo de {' ni de '.join(faltan)} por defecto",
                         "Conecta el micrófono/altavoces y elígelos como predeterminados en Windows")
    return resultado(OK, "Dispositivos de entrada y salida por defecto")


def check_piper():
    voz = constante("audio.py", "VOICE_MODEL")
    if importlib.util.find_spec("piper") is None:
        return resultado(ERROR, "piper-tts no está instalado",
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    nombre = os.path.basename(voz)
    if not os.path.isfile(voz) or not os.path.isfile(voz + ".json"):
        return resultado(ERROR, f"Falta la voz de Piper voices/{nombre} (o su .json)",
                         f"Descarga {nombre} y {nombre}.json de rhasspy/piper-voices en voices/")
    return resultado(OK, f"Piper y la voz voices/{nombre}")


def check_whisper():
    modelo = modelo_whisper()
    cache = os.path.join(os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")),
                         "whisper", f"{modelo}.pt")
    if importlib.util.find_spec("whisper") is None:
        return resultado(ERROR, "openai-whisper no está instalado",
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    if not os.path.isfile(cache):
        return resultado(AVISO, f"El modelo {modelo} no está en la caché de Whisper: se descargará al arrancar",
                         "Arranca JARVIS con conexión la primera vez")
    return resultado(OK, f"Modelo {modelo} en la caché de Whisper")


def check_cuda():
    if importlib.util.find_spec("torch") is None:
        return resultado(AVISO, "torch no está instalado: Whisper no podrá cargar",
                         r"venv\Scripts\python.exe -m pip install -r requirements.txt")
    import torch
    if torch.cuda.is_available():
        return resultado(OK, "CUDA disponible: Whisper usará la GPU")
    return resultado(AVISO, "CUDA no disponible: Whisper irá en CPU (más lento)",
                     "Instala el torch con CUDA de requirements.txt y los drivers de NVIDIA")


def check_secretos_carpeta():
    if os.path.isdir(SECRETOS):
        return resultado(OK, r"C:\Jarvis-secrets existe")
    return resultado(ERROR, r"No existe C:\Jarvis-secrets", r"Crea C:\Jarvis-secrets con .env y credentials.json")


def check_env():
    if os.path.isfile(ENV_FILE):
        return resultado(OK, r"C:\Jarvis-secrets\.env presente")
    return resultado(AVISO, r"No hay C:\Jarvis-secrets\.env (solo valdrán las variables del sistema)",
                     r"Crea C:\Jarvis-secrets\.env con ANTHROPIC_API_KEY=...")


def check_anthropic_key():
    clave = variable("ANTHROPIC_API_KEY")
    if not clave:
        return resultado(ERROR, "ANTHROPIC_API_KEY no definida", r"Añádela a C:\Jarvis-secrets\.env")
    return resultado(OK, f"ANTHROPIC_API_KEY definida ({len(clave)} caracteres)")


def check_tavily_key():
    clave = variable("TAVILY_API_KEY")
    if not clave:
        return resultado(AVISO, "TAVILY_API_KEY no definida: buscar_en_web solo usará Wikipedia y DuckDuckGo",
                         r"Añádela a C:\Jarvis-secrets\.env (opcional)")
    return resultado(OK, f"TAVILY_API_KEY definida ({len(clave)} caracteres)")


def check_google_credentials():
    ruta = constante(os.path.join("skills", "gmail.py"), "CREDENTIALS_FILE")
    if os.path.isfile(ruta):
        return resultado(OK, r"credentials.json de Google en C:\Jarvis-secrets")
    return resultado(AVISO, "Falta credentials.json: Gmail y Calendar no funcionarán",
                     r"Descarga el cliente OAuth de Google Cloud a C:\Jarvis-secrets\credentials.json")


def _reautorizar(servicio):
    return rf"venv\Scripts\python.exe reautorizar.py {servicio}"


def _check_token(ruta, servicio, comando):
    """Sin red: solo si el archivo existe y se puede renovar. Si sirve o no lo dice --live."""
    nombre = os.path.basename(ruta)
    if not os.path.isfile(ruta):
        return resultado(AVISO, f"No hay {nombre}: {servicio} no está autorizado", comando)
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        datos = None
    if not isinstance(datos, dict):
        return resultado(AVISO, f"{nombre} está dañado: hay que reautorizar {servicio}", comando)
    if not datos.get("refresh_token"):
        return resultado(AVISO, f"{nombre} sin refresh_token: hay que reautorizar {servicio}", comando)
    return resultado(OK, f"{nombre} presente (no verificado; doctor.py --live lo comprueba con Google)")


def check_token_gmail():
    return _check_token(constante(os.path.join("skills", "gmail.py"), "TOKEN_FILE"), "Gmail",
                        _reautorizar("gmail"))


def check_token_calendar():
    return _check_token(constante(os.path.join("skills", "calendario.py"), "TOKEN_CAL"), "Calendar",
                        _reautorizar("calendar"))


def _elevenlabs():
    """(api_key, voice_id) como los resuelve tts_elevenlabs: entorno y luego elevenlabs.json."""
    clave, voz = variable("ELEVENLABS_API_KEY"), variable("ELEVENLABS_VOICE_ID")
    ruta = constante("tts_elevenlabs.py", "ELEVENLABS_JSON")
    if (not clave or not voz) and os.path.isfile(ruta):
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        clave, voz = clave or datos.get("api_key", ""), voz or datos.get("voice_id", "")
    return clave, voz


def _tts_elevenlabs():
    return variable("JARVIS_TTS").strip().lower() == "elevenlabs"


def check_elevenlabs():
    if not _tts_elevenlabs():
        return resultado(OK, "JARVIS_TTS=piper: ElevenLabs no se usa")
    clave, voz = _elevenlabs()
    falta = [n for n, v in (("ELEVENLABS_API_KEY", clave), ("ELEVENLABS_VOICE_ID", voz)) if not v]
    if falta:
        return resultado(AVISO, f"JARVIS_TTS=elevenlabs pero falta {' y '.join(falta)}: se usará Piper",
                         r"Defínelas en C:\Jarvis-secrets\.env o en elevenlabs.json")
    return resultado(OK, f"ElevenLabs configurado (clave de {len(clave)} caracteres y voice_id)")


def check_secretos_fuera_del_repo():
    raiz, sec = os.path.normcase(os.path.realpath(RAIZ)), os.path.normcase(os.path.realpath(SECRETOS))
    try:
        dentro = os.path.commonpath([raiz, sec]) == raiz
    except ValueError:  # otra unidad
        dentro = False
    if dentro:
        return resultado(ERROR, r"C:\Jarvis-secrets está dentro del repo", "Muévela fuera de C:\\Jarvis")
    return resultado(OK, r"C:\Jarvis-secrets está fuera del repo")


def check_gitignore():
    sin_cubrir = [r for r in DEBEN_IGNORARSE if git("check-ignore", "-q", "--no-index", r).returncode != 0]
    if sin_cubrir:
        return resultado(AVISO, f".gitignore no cubre: {', '.join(sin_cubrir)}",
                         "Añade a .gitignore: .env, credentials*.json, token*.json y logs/")
    return resultado(OK, ".gitignore cubre .env, credentials*.json, token*.json y logs/")


def check_secretos_trackeados():
    r = git("ls-files")
    if r.returncode != 0:
        return resultado(AVISO, "No se pudo listar los archivos de git", "Comprueba que git está instalado")
    trackeados = [p for p in r.stdout.splitlines()
                  if any(re.fullmatch(patron, os.path.basename(p), re.I) for patron in PATRONES_SECRETOS)]
    if trackeados:
        return resultado(ERROR, f"Archivos de secretos en git: {', '.join(trackeados)[:200]}",
                         "git rm --cached <archivo>, añádelo a .gitignore y rota la clave")
    return resultado(OK, "Ningún archivo de secretos trackeado por git")


def _duenio_del_puerto(puerto):
    import psutil
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == puerto and c.status == psutil.CONN_LISTEN and c.pid:
            p = psutil.Process(c.pid)
            return p.pid, p.name(), " ".join(p.cmdline())
    return None


def check_puerto():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        libre = s.connect_ex(("127.0.0.1", PUERTO_UI)) != 0
    if libre:
        return resultado(OK, f"Puerto {PUERTO_UI} libre")
    try:
        duenio = _duenio_del_puerto(PUERTO_UI)
    except Exception:
        duenio = None
    if duenio and "jarvis" in duenio[2].lower():
        return resultado(OK, f"Puerto {PUERTO_UI} en uso por JARVIS (pid {duenio[0]})")
    quien = f"{duenio[1]} (pid {duenio[0]})" if duenio else "otro proceso"
    return resultado(ERROR, f"Puerto {PUERTO_UI} en uso por {quien}: la UI no podrá conectar",
                     f"Cierra ese proceso (taskkill /pid {duenio[0]})" if duenio else "Cierra el proceso que lo usa")


def check_zona_horaria():
    import zoneinfo
    try:
        zoneinfo.ZoneInfo("Europe/Madrid")
    except zoneinfo.ZoneInfoNotFoundError:
        return resultado(ERROR, "zoneinfo no encuentra Europe/Madrid",
                         r"venv\Scripts\python.exe -m pip install tzdata")
    return resultado(OK, "Europe/Madrid disponible")


def check_proyectos():
    from skills import operador
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    faltan, sin_wsl = [], []
    for alias, ruta in operador.PROYECTOS.items():
        if ruta.lower().startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")) and not wsl:
            sin_wsl.append(alias)
        elif not os.path.isdir(ruta):
            faltan.append(alias)
    if sin_wsl or faltan:
        partes = ([f"sin WSL: {', '.join(sin_wsl)}"] if sin_wsl else []) + \
                 ([f"no existen: {', '.join(faltan)}"] if faltan else [])
        return resultado(AVISO, f"Proyectos del operador {'; '.join(partes)}",
                         "Instala WSL (wsl --install) o corrige PROYECTOS en skills/operador.py")
    return resultado(OK, f"{len(operador.PROYECTOS)} proyectos del operador accesibles")


def check_logs():
    carpeta = os.path.join(RAIZ, "logs")
    if not os.path.isdir(carpeta):
        return resultado(AVISO, "No existe logs/: se creará con el primer turno",
                         "Arranca JARVIS una vez o crea la carpeta logs/")
    if not os.access(carpeta, os.W_OK):
        return resultado(ERROR, "logs/ no es escribible", "Da permisos de escritura a C:\\Jarvis\\logs")
    turnos = os.path.join(carpeta, "turnos.jsonl")
    tam = os.path.getsize(turnos) if os.path.isfile(turnos) else 0
    if tam > LOG_GRANDE:
        return resultado(AVISO, f"logs/turnos.jsonl ocupa {tam // (1024 * 1024)} MB",
                         "Archiva o borra logs/turnos.jsonl")
    return resultado(OK, f"logs/ escribible; turnos.jsonl {tam // 1024} KB")


def check_memoria():
    """Memoria entre sesiones: existe, se puede leer y cuántas sesiones guarda (sin contenido)."""
    import memoria_sesiones
    existe, legible, _, detalle = memoria_sesiones.estado()
    if not existe:
        return resultado(AVISO, detalle, "Normal en la primera ejecución; se crea al cerrar JARVIS")
    if not legible:
        return resultado(ERROR, detalle, "Revisa permisos o borra la base (perderás la memoria entre sesiones)")
    return resultado(OK, detalle)


def check_git():
    estado = git("status", "--porcelain")
    if estado.returncode != 0:
        return resultado(AVISO, "No es un repo git o git no está instalado", "Instala git")
    cambios = len([l for l in estado.stdout.splitlines() if l.strip()])
    cuenta = git("rev-list", "--left-right", "--count", "HEAD...@{u}")  # sin fetch: contra el último conocido
    delante = detras = 0
    if cuenta.returncode == 0 and cuenta.stdout.split():
        delante, detras = (int(x) for x in cuenta.stdout.split()[:2])
    avisos = ([f"{cambios} archivo(s) con cambios"] if cambios else []) + \
             ([f"{delante} commit(s) sin subir"] if delante else []) + \
             ([f"{detras} commit(s) por bajar"] if detras else [])
    if avisos:
        return resultado(AVISO, "; ".join(avisos), "git status / git pull / git push")
    return resultado(OK, "Árbol limpio y al día con origin (según el último fetch)")


# ── --live ────────────────────────────────────────────────────────
def _post(url, cabeceras, cuerpo, timeout=15):
    import requests
    return requests.post(url, headers=cabeceras, json=cuerpo, timeout=timeout).status_code


def _get(url, cabeceras, timeout=15):
    import requests
    return requests.get(url, headers=cabeceras, timeout=timeout).status_code


def check_live_anthropic():
    clave = variable("ANTHROPIC_API_KEY")
    if not clave:
        return resultado(ERROR, "ANTHROPIC_API_KEY no definida: no se llama a la API",
                         r"Añádela a C:\Jarvis-secrets\.env")
    try:
        codigo = _post("https://api.anthropic.com/v1/messages",
                       {"x-api-key": clave, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       {"model": constante("brain.py", "CLAUDE_MODEL"), "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ok"}]})
    except Exception as e:
        return resultado(ERROR, f"Sin respuesta de la API de Anthropic ({type(e).__name__})",
                         "Comprueba la conexión a internet")
    if codigo == 200:
        return resultado(OK, "API de Anthropic: HTTP 200")
    accion = "Revisa ANTHROPIC_API_KEY" if codigo in (401, 403) else "Revisa el modelo y el saldo de la cuenta"
    return resultado(ERROR, f"API de Anthropic: HTTP {codigo}", accion)


def check_live_elevenlabs():
    if not _tts_elevenlabs():
        return resultado(OK, "JARVIS_TTS=piper: no se consulta ElevenLabs")
    clave, voz = _elevenlabs()
    if not clave or not voz:
        return resultado(ERROR, "Falta la clave o el voice_id de ElevenLabs: no se llama a la API",
                         r"Defínelos en C:\Jarvis-secrets\.env o en elevenlabs.json")
    try:
        codigo = _get(f"https://api.elevenlabs.io/v1/voices/{voz}", {"xi-api-key": clave})
    except Exception as e:
        return resultado(ERROR, f"Sin respuesta de ElevenLabs ({type(e).__name__})", "Comprueba la conexión")
    if codigo == 200:
        return resultado(OK, "ElevenLabs (clave y voice_id): HTTP 200")
    return resultado(ERROR, f"ElevenLabs: HTTP {codigo}", "Revisa ELEVENLABS_API_KEY y ELEVENLABS_VOICE_ID")


def _check_live_token(servicio):
    """La misma comprobación que el arranque de JARVIS: renueva el token en memoria."""
    from skills import google_auth
    r = google_auth.comprobar_servicio(servicio)
    if r["ok"]:
        estado = OK
    elif r["estado"] in (google_auth.SIN_RED, google_auth.SIN_TOKEN, google_auth.SIN_CREDENCIALES):
        estado = AVISO  # sin red no se sabe; sin token o sin credentials.json ya avisan otras líneas
    else:
        estado = ERROR
    return resultado(estado, google_auth.mensaje(r), google_auth.accion(r))


def check_live_token_gmail():
    return _check_live_token("gmail")


def check_live_token_calendar():
    return _check_live_token("calendar")


COMPROBACIONES = [
    ("python",            "Python",                       check_python),
    ("dependencias",      "Dependencias",                 check_dependencias),
    ("ffmpeg",            "ffmpeg",                       check_ffmpeg),
    ("vlc",               "VLC",                          check_vlc),
    ("yt_dlp",            "yt-dlp",                       check_ytdlp),
    ("node",              "Node y npm",                   check_node),
    ("electron",          "Electron",                     check_electron),
    ("ollama",            "Ollama",                       check_ollama),
    ("audio",             "Audio",                        check_audio),
    ("piper",             "Piper",                        check_piper),
    ("whisper",           "Whisper",                      check_whisper),
    ("cuda",              "CUDA",                         check_cuda),
    ("secretos",          "Carpeta de secretos",          check_secretos_carpeta),
    ("env",               ".env",                         check_env),
    ("anthropic_key",     "ANTHROPIC_API_KEY",            check_anthropic_key),
    ("tavily_key",        "TAVILY_API_KEY",               check_tavily_key),
    ("google_credentials","credentials.json",             check_google_credentials),
    ("token_gmail",       "Token de Gmail",               check_token_gmail),
    ("token_calendar",    "Token de Calendar",            check_token_calendar),
    ("elevenlabs",        "ElevenLabs",                   check_elevenlabs),
    ("secretos_fuera",    "Secretos fuera del repo",      check_secretos_fuera_del_repo),
    ("gitignore",         ".gitignore",                   check_gitignore),
    ("secretos_git",      "Secretos en git",              check_secretos_trackeados),
    ("puerto",            "Puerto de la UI",              check_puerto),
    ("zona_horaria",      "Zona horaria",                 check_zona_horaria),
    ("proyectos",         "Proyectos del operador",       check_proyectos),
    ("logs",              "Logs",                         check_logs),
    ("memoria",           "Memoria entre sesiones",       check_memoria),
    ("git",               "Git",                          check_git),
]
COMPROBACIONES_LIVE = [
    ("live_anthropic",    "API de Anthropic (--live)",    check_live_anthropic),
    ("live_elevenlabs",   "ElevenLabs (--live)",          check_live_elevenlabs),
    ("live_token_gmail",  "Token de Gmail (--live)",      check_live_token_gmail),
    ("live_token_calendar","Token de Calendar (--live)",  check_live_token_calendar),
]


def _sanear(texto):
    """Red de seguridad: ningún valor que parezca un secreto sale del doctor."""
    try:
        from skills.secretos import redactar_secretos
        return redactar_secretos(texto)
    except Exception:
        return texto


def ejecutar(live=False):
    resultados = []
    for id_, nombre, funcion in COMPROBACIONES + (COMPROBACIONES_LIVE if live else []):
        try:
            r = funcion()
        except Exception as e:  # una comprobación rota no tumba el resto
            r = resultado(ERROR, f"La comprobación falló ({type(e).__name__})",
                          "Revisa la instalación de este componente")
        resultados.append({"id": id_, "nombre": nombre, "estado": r["estado"],
                           "detalle": _sanear(r["detalle"]), "accion": _sanear(r["accion"])})
    return resultados


def resumen(resultados):
    cuenta = {e: sum(r["estado"] == e for r in resultados) for e in (OK, AVISO, ERROR)}
    return cuenta, (ERROR if cuenta[ERROR] else AVISO if cuenta[AVISO] else OK)


def a_json(resultados):
    cuenta, global_ = resumen(resultados)
    return json.dumps({"version": VERSION_JSON, "estado": global_, "resumen": cuenta,
                       "comprobaciones": resultados}, ensure_ascii=True, indent=2)


def a_tabla(resultados):
    ancho = max(len(r["nombre"]) for r in resultados)
    lineas = [f"{'ESTADO':<6}  {'COMPROBACIÓN':<{ancho}}  DETALLE", "-" * (ancho + 60)]
    for r in resultados:
        lineas.append(f"{r['estado']:<6}  {r['nombre']:<{ancho}}  {r['detalle']}")
        if r["accion"]:
            lineas.append(f"{'':<6}  {'':<{ancho}}  → {r['accion']}")
    cuenta, global_ = resumen(resultados)
    lineas += ["-" * (ancho + 60), f"{global_}: {cuenta[OK]} OK, {cuenta[AVISO]} AVISO, {cuenta[ERROR]} ERROR"]
    return "\n".join(lineas)


def _imprimir(texto):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(texto.encode(enc, "replace").decode(enc))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnóstico del entorno de JARVIS")
    parser.add_argument("--json", action="store_true", help="salida JSON estable")
    parser.add_argument("--live", action="store_true", help="valida las claves con llamadas mínimas (coste despreciable)")
    args = parser.parse_args(argv)
    resultados = ejecutar(live=args.live)
    _imprimir(a_json(resultados) if args.json else a_tabla(resultados))
    return 1 if any(r["estado"] == ERROR for r in resultados) else 0


if __name__ == "__main__":
    sys.exit(main())
