"""Diagnóstico del entorno de JARVIS sin probar por voz.

    venv\\Scripts\\python.exe doctor.py [--json] [--live]
    venv\\Scripts\\python.exe doctor.py --reporte | clip     (informe para pegar en un issue)

Cada comprobación devuelve OK / AVISO / ERROR, un texto corto y, si falla, la acción
para arreglarlo. Código de salida: 0 sin ERROR, 1 con algún ERROR (los AVISO no cuentan).

Solo lectura: no escribe ni borra archivos, no graba audio, no carga Whisper ni genera
texto con Ollama. La única red es la local (Ollama en 127.0.0.1) salvo con --live, que
hace una llamada mínima a Anthropic (max_tokens=1), renueva en memoria los tokens de
Gmail y Calendar con su refresh_token (sin leer correo ni eventos y sin guardar nada),
lee GitHub con GITHUB_TOKEN, consulta el tiempo en Open-Meteo y, si
JARVIS_TTS=elevenlabs, consulta la voz configurada sin sintetizar. Sin --live los
tokens de Google y el tiempo salen como "no verificado". Nunca imprime valores de claves ni rutas
fuera de la carpeta del código, la de secretos y las configuradas en rutas.py.
"""
import argparse
import ast
import platform
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

import rutas

RAIZ      = os.path.dirname(os.path.abspath(__file__))
SECRETOS  = rutas.secretos()
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
def _valor_constante(nodo):
    """Un literal, o una llamada rutas.funcion(literales) que se evalúa con rutas.py (ligero):
    así las rutas configurables se leen sin importar el módulo que las usa."""
    if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
            and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "rutas"
            and not nodo.keywords and not nodo.func.attr.startswith("_")):
        return getattr(rutas, nodo.func.attr)(*[ast.literal_eval(a) for a in nodo.args])
    return ast.literal_eval(nodo)


def constante(archivo, nombre):
    """Valor de NOMBRE = ... en un .py del proyecto, sin importarlo (literal o rutas.xxx(...))."""
    with open(os.path.join(RAIZ, archivo), encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nombre for t in nodo.targets):
            return _valor_constante(nodo.value)
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
        return resultado(OK, f"{SECRETOS} existe")
    return resultado(ERROR, f"No existe {SECRETOS}", f"Crea {SECRETOS} con .env y credentials.json (instalar.cmd la crea)")


def check_env():
    if os.path.isfile(ENV_FILE):
        return resultado(OK, f"{ENV_FILE} presente")
    return resultado(AVISO, f"No hay {ENV_FILE} (solo valdrán las variables del sistema)",
                     f"Crea {ENV_FILE} con ANTHROPIC_API_KEY=...")


def check_anthropic_key():
    clave = variable("ANTHROPIC_API_KEY")
    if not clave:
        return resultado(ERROR, "ANTHROPIC_API_KEY no definida", f"Añádela a {ENV_FILE}")
    return resultado(OK, f"ANTHROPIC_API_KEY definida ({len(clave)} caracteres)")


def check_tavily_key():
    clave = variable("TAVILY_API_KEY")
    if not clave:
        return resultado(AVISO, "TAVILY_API_KEY no definida: buscar_en_web solo usará Wikipedia y DuckDuckGo",
                         f"Añádela a {ENV_FILE} (opcional)")
    return resultado(OK, f"TAVILY_API_KEY definida ({len(clave)} caracteres)")


def check_github_token():
    """GITHUB_TOKEN para el briefing: existe y es fine-grained. Nunca se muestra."""
    token = variable("GITHUB_TOKEN")
    if not token:
        return resultado(AVISO, "GITHUB_TOKEN no definido: el briefing no dirá el estado de GitHub",
                         r"Crea un token fine-grained de solo lectura (Metadata, Pull requests y Actions) "
                         f"y añádelo a {ENV_FILE} (opcional)")
    if not token.startswith("github_pat_"):
        return resultado(AVISO, f"GITHUB_TOKEN definido ({len(token)} caracteres) pero no es fine-grained",
                         "Sustitúyelo por uno fine-grained limitado a los repos del briefing y de solo lectura")
    return resultado(OK, f"GITHUB_TOKEN fine-grained definido ({len(token)} caracteres)")


def check_google_credentials():
    ruta = constante(os.path.join("skills", "gmail.py"), "CREDENTIALS_FILE")
    if os.path.isfile(ruta):
        return resultado(OK, f"credentials.json de Google en {SECRETOS}")
    return resultado(AVISO, "Falta credentials.json: Gmail y Calendar no funcionarán",
                     f"Descarga el cliente OAuth de Google Cloud a {rutas.en_secretos('credentials.json')}")


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


def check_token_contactos():
    """Contactos es opcional: sin token no es un aviso, solo se dice cómo activarlo."""
    ruta = constante(os.path.join("skills", "contactos.py"), "TOKEN_CONTACTOS")
    if not os.path.isfile(ruta):
        return resultado(OK, f"Sin {os.path.basename(ruta)}: Contactos no está activado (opcional; "
                             f"{_reautorizar('contacts')} lo activa)")
    return _check_token(ruta, "Contactos", _reautorizar("contacts"))


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
                         f"Defínelas en {ENV_FILE} o en elevenlabs.json")
    return resultado(OK, f"ElevenLabs configurado (clave de {len(clave)} caracteres y voice_id)")


def check_secretos_fuera_del_repo():
    raiz, sec = os.path.normcase(os.path.realpath(RAIZ)), os.path.normcase(os.path.realpath(SECRETOS))
    try:
        dentro = os.path.commonpath([raiz, sec]) == raiz
    except ValueError:  # otra unidad
        dentro = False
    if dentro:
        return resultado(ERROR, f"{SECRETOS} está dentro del repo", f"Muévela fuera de {RAIZ}")
    return resultado(OK, f"{SECRETOS} está fuera del repo")


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
                         'Define "proyectos" en rutas.json con los tuyos ({"alias": "ruta"}, ver Rutas en el '
                         'README) o instala WSL (wsl --install)')
    return resultado(OK, f"{len(operador.PROYECTOS)} proyectos del operador accesibles")


def check_logs():
    carpeta = os.path.join(RAIZ, "logs")
    if not os.path.isdir(carpeta):
        return resultado(AVISO, "No existe logs/: se creará con el primer turno",
                         "Arranca JARVIS una vez o crea la carpeta logs/")
    if not os.access(carpeta, os.W_OK):
        return resultado(ERROR, "logs/ no es escribible", f"Da permisos de escritura a {os.path.join(RAIZ, 'logs')}")
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
                         f"Añádela a {ENV_FILE}")
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


def check_live_github():
    """Lee de verdad, con el token, el repo, los PRs y Actions de cada repo del briefing."""
    token = variable("GITHUB_TOKEN")
    if not token:
        return resultado(AVISO, "GITHUB_TOKEN no definido: no se llama a GitHub", "Ver la comprobación GITHUB_TOKEN")
    from skills import github
    cabeceras = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    fallos = []
    for repo in github.repos():
        for nombre, ruta in (("repo", ""), ("PRs", "/pulls?per_page=1"), ("Actions", "/actions/runs?per_page=1")):
            try:
                codigo = _get(f"{github.API}/repos/{repo}{ruta}", cabeceras)
            except Exception as e:
                return resultado(ERROR, f"Sin respuesta de GitHub ({type(e).__name__})", "Comprueba la conexión")
            if codigo == 401:
                return resultado(ERROR, "GitHub: HTTP 401, el token no es válido o ha caducado",
                                 f"Genera otro y actualiza GITHUB_TOKEN en {ENV_FILE}")
            if codigo != 200:
                fallos.append(f"{repo} {nombre}: HTTP {codigo}")
    if fallos:
        return resultado(ERROR, "GitHub: " + "; ".join(fallos),
                         "Da acceso al repo y permiso de lectura a Pull requests y Actions en el token")
    return resultado(OK, f"GitHub: lectura de PRs y Actions correcta en {len(github.repos())} repo(s)")


def check_live_elevenlabs():
    if not _tts_elevenlabs():
        return resultado(OK, "JARVIS_TTS=piper: no se consulta ElevenLabs")
    clave, voz = _elevenlabs()
    if not clave or not voz:
        return resultado(ERROR, "Falta la clave o el voice_id de ElevenLabs: no se llama a la API",
                         f"Defínelos en {ENV_FILE} o en elevenlabs.json")
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


def check_live_token_contactos():
    ruta = constante(os.path.join("skills", "contactos.py"), "TOKEN_CONTACTOS")
    if not os.path.isfile(ruta):
        return resultado(OK, "Contactos no está activado (opcional): no hay nada que verificar")
    return _check_live_token("contacts")


def check_tiempo():
    from skills import sistema
    host = sistema.URL_TIEMPO.split("/")[2]
    return resultado(OK, f"{host} (no verificado; doctor.py --live lo consulta)")


def check_live_tiempo():
    """Consulta real del tiempo, con el mismo reintento y las mismas causas que el briefing."""
    from skills import sistema
    ok, texto, causa = sistema.consultar_tiempo()
    if ok:
        return resultado(OK, "Servicio del tiempo: respuesta correcta")
    accion = ("Comprueba la conexión a internet" if causa in (sistema.SIN_RED, sistema.SIN_RESPUESTA)
              else "Es un fallo de Open-Meteo: el briefing lo dirá con esta causa; vuelve a probar más tarde")
    return resultado(AVISO, f"{texto} ({causa})", accion)


def check_rutas():
    """Las carpetas configurables (rutas.py): de dónde sale cada una y si existen."""
    faltan, partes = [], []
    for clave, nombre in (("descargas", "Descargas"), ("escritorio", "Escritorio"), ("home", "Home")):
        ruta = rutas.valor(clave)
        partes.append(f"{nombre} {ruta} ({rutas.origen(clave)})")
        if not os.path.isdir(ruta):
            faltan.append(nombre)
    detalle = "; ".join(partes)
    if faltan:
        return resultado(AVISO, f"{detalle}. No existe: {', '.join(faltan)}",
                         f"Ejecuta instalar.cmd (crea {rutas.en_secretos(rutas.ARCHIVO)}) o define "
                         "JARVIS_DESCARGAS / JARVIS_HOME / JARVIS_ESCRITORIO")
    return resultado(OK, detalle)


def check_avisos_ci():
    """Avisos proactivos de CI (JARVIS_AVISOS_CI=1): si están activos, cada cuánto y qué repos."""
    if variable("JARVIS_AVISOS_CI").strip() != "1":
        return resultado(OK, "Avisos de CI desactivados (por defecto); JARVIS_AVISOS_CI=1 los activa")
    import avisos_ci
    from skills import github
    crudo = variable("JARVIS_AVISOS_CI_REPOS").strip() or variable("JARVIS_GITHUB_REPOS")
    lista = github.repos(crudo)
    minutos = avisos_ci.minutos(variable("JARVIS_AVISOS_CI_MIN"))
    detalle = f"Avisos de CI activos: {', '.join(r.split('/', 1)[1] for r in lista)} cada {minutos} min"
    if not variable("GITHUB_TOKEN"):
        return resultado(AVISO, detalle + ", pero sin GITHUB_TOKEN no avisarán de nada",
                         "Añade GITHUB_TOKEN (solo lectura, ver Briefing) o quita JARVIS_AVISOS_CI")
    return resultado(OK, detalle)


def check_gate_estricto():
    """Modo del gate estricto (JARVIS_GATE_ESTRICTO) y la lista efectiva de tools que confirman."""
    valor = variable("JARVIS_GATE_ESTRICTO").strip()
    if valor == "0":
        return resultado(AVISO, "Gate estricto DESACTIVADO (JARVIS_GATE_ESTRICTO=0): ninguna tool WRITE/EXTERNAL "
                                "pide confirmación", "Quita JARVIS_GATE_ESTRICTO=0 para volver al modo por defecto")
    if valor == "1":
        tools, modo = sorted(constante("brain.py", "CONFIRM_PHRASES_ESTRICTO")), "completo (JARVIS_GATE_ESTRICTO=1)"
    else:
        tools, modo = sorted(constante("brain.py", "CONFIRMAR_POR_DEFECTO")), "por defecto"
    detalle = f"Gate estricto {modo}: {len(tools)} tools piden confirmación por voz ({', '.join(tools)})"
    if valor not in ("", "1"):
        return resultado(AVISO, f"JARVIS_GATE_ESTRICTO={valor!r} no se reconoce; se usa el modo por defecto. "
                                + detalle, "Usa 0, 1 o quítala")
    return resultado(OK, detalle)


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
    ("github_token",      "GITHUB_TOKEN",                 check_github_token),
    ("google_credentials","credentials.json",             check_google_credentials),
    ("token_gmail",       "Token de Gmail",               check_token_gmail),
    ("token_calendar",    "Token de Calendar",            check_token_calendar),
    ("token_contactos",   "Token de Contactos",           check_token_contactos),
    ("elevenlabs",        "ElevenLabs",                   check_elevenlabs),
    ("secretos_fuera",    "Secretos fuera del repo",      check_secretos_fuera_del_repo),
    ("gitignore",         ".gitignore",                   check_gitignore),
    ("secretos_git",      "Secretos en git",              check_secretos_trackeados),
    ("puerto",            "Puerto de la UI",              check_puerto),
    ("zona_horaria",      "Zona horaria",                 check_zona_horaria),
    ("proyectos",         "Proyectos del operador",       check_proyectos),
    ("logs",              "Logs",                         check_logs),
    ("memoria",           "Memoria entre sesiones",       check_memoria),
    ("tiempo",            "Servicio del tiempo",          check_tiempo),
    ("rutas",             "Rutas configurables",          check_rutas),
    ("gate_estricto",     "Gate estricto",                check_gate_estricto),
    ("avisos_ci",         "Avisos de CI",                 check_avisos_ci),
    ("git",               "Git",                          check_git),
]
COMPROBACIONES_LIVE = [
    ("live_anthropic",    "API de Anthropic (--live)",    check_live_anthropic),
    ("live_elevenlabs",   "ElevenLabs (--live)",          check_live_elevenlabs),
    ("live_github",       "GitHub (--live)",              check_live_github),
    ("live_tiempo",       "Servicio del tiempo (--live)", check_live_tiempo),
    ("live_token_gmail",  "Token de Gmail (--live)",      check_live_token_gmail),
    ("live_token_calendar","Token de Calendar (--live)",  check_live_token_calendar),
    ("live_token_contactos","Token de Contactos (--live)", check_live_token_contactos),
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


# ── --reporte: informe para pegar en un issue ─────────────────────
LOG_CONSOLA   = os.path.join(RAIZ, "jarvis.log")
LOG_MAX_BYTES = 2 * 1024 * 1024     # solo el final del log
MAX_ERRORES   = 15
USUARIO, EQUIPO = "<usuario>", "<equipo>"
# Del log solo salen firmas: la etiqueta [X], nombres de excepción, códigos HTTP y los marcos de
# las trazas (archivo, línea, función). El resto de cada línea, que puede llevar texto de un
# turno, nunca sale.
_RE_ETIQUETA  = re.compile(r"^\s*(?:[^\w\s\[]+\s*)?\[([A-Z][A-Z0-9_+ -]{1,20})\]")
_RE_EXCEPCION = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Interrupt|Exit|Warning)|NoConfigurado)\b")
_RE_HTTP      = re.compile(r"\bHTTP (\d{3})\b")
_RE_ES_ERROR  = re.compile(r"❌|error|falló|fallo|fallback|no se pudo|no disponible|exception|traceback", re.I)
_RE_MARCO     = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)')
_IGNORAR      = ("[TRAZA", "[ARNES]", "👂", "🔊", "🎤")   # trazas de tools y turnos: nunca


def _ruta_relativa(ruta):
    """Ruta del marco de una traza sin carpetas del usuario: relativa al repo o al paquete."""
    normal = ruta.replace("/", os.sep)
    raiz = RAIZ.rstrip(os.sep) + os.sep
    if normal.lower().startswith(raiz.lower()):
        return normal[len(raiz):].replace(os.sep, "/")
    for marca in ("site-packages", "lib" + os.sep + "python", "Lib"):
        i = normal.find(os.sep + marca + os.sep)
        if i >= 0:
            return "<python>/" + normal[i + len(marca) + 2:].replace(os.sep, "/")
    return "<fuera del repo>/" + os.path.basename(normal)


def errores_del_log(ruta=LOG_CONSOLA, maximo=MAX_ERRORES):
    """[(firma, veces)] de los últimos errores de la consola (jarvis.log), del más antiguo al más
    reciente por su última aparición. Firmas, nunca el texto de las líneas."""
    try:
        with open(ruta, "rb") as f:
            f.seek(max(0, os.path.getsize(ruta) - LOG_MAX_BYTES))
            lineas = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    cuenta, orden, marcos = {}, [], []

    def anotar(firma):
        cuenta[firma] = cuenta.get(firma, 0) + 1
        if firma in orden:
            orden.remove(firma)
        orden.append(firma)

    for linea in lineas:
        if any(i in linea for i in _IGNORAR):
            continue
        marco = _RE_MARCO.match(linea)
        if marco:
            marcos.append(f"{_ruta_relativa(marco.group(1))}:{marco.group(2)} en {marco.group(3)}")
            continue
        if marcos and not linea.startswith((" ", "\t")):     # fin de la traza: su excepción
            tipo = _RE_EXCEPCION.search(linea)
            anotar(f"Traza {tipo.group(1) if tipo else '(excepción sin tipo)'} ← {' ← '.join(reversed(marcos[-3:]))}")
            marcos = []
            continue
        if "Traceback (most recent call last)" in linea or not _RE_ES_ERROR.search(linea):
            continue
        etiqueta = _RE_ETIQUETA.match(linea)
        partes = ([f"[{etiqueta.group(1)}]"] if etiqueta else ["❌"] if "❌" in linea else []) + \
                 list(dict.fromkeys(_RE_EXCEPCION.findall(linea))) + \
                 [f"HTTP {c}" for c in dict.fromkeys(_RE_HTTP.findall(linea))]
        if len(partes) > (1 if etiqueta or "❌" in linea else 0):
            anotar(" ".join(partes))
    return [(f, cuenta[f]) for f in orden[-maximo:]]


def anonimizar(texto):
    """Secretos y direcciones (direccion.redactar: redactar_secretos + <email>), y además las
    carpetas configuradas en rutas.py, rutas con el nombre del usuario, el nombre del equipo y
    el del perfil."""
    try:
        from skills import direccion
        texto = direccion.redactar(texto)
    except Exception:
        texto = _sanear(texto)
    carpetas = []
    for clave in ("descargas", "escritorio", "home", "wsl_home"):
        try:
            carpetas.append((rutas.valor(clave), f"<{clave}>"))
        except Exception:
            pass
    for valor, marca in sorted(carpetas, key=lambda c: len(c[0] or ""), reverse=True):
        if valor and len(valor) >= 4:
            texto = re.sub(rf"(?i){re.escape(valor)}(?=$|[\\/\s|;,)])", lambda m: marca, texto)
    texto = re.sub(r"(?i)\b([a-z]:\\users\\)[^\\/\s\"'<>|]+", lambda m: m.group(1) + USUARIO, texto)
    texto = re.sub(r"(?i)(\\\\wsl(?:\.localhost|\$)\\[^\\\s]+\\home\\)[^\\/\s\"'<>|]+",
                   lambda m: m.group(1) + USUARIO, texto)
    texto = re.sub(r"(/home/|/Users/|/mnt/[a-z]/Users/)[^/\s\"'<>|]+", lambda m: m.group(1) + USUARIO, texto)
    sustituir = [(os.environ.get(v, ""), USUARIO) for v in ("USERNAME", "USER")] + \
                [(os.environ.get("COMPUTERNAME", ""), EQUIPO)] + \
                [(rutas.perfil(c), USUARIO) for c in ("nombre_completo", "nombre")]
    for valor, marca in sustituir:
        valor = (valor or "").strip()
        if len(valor) >= 3:   # tal cual (un nombre como «Usuario» no puede comerse texto normal)
            texto = re.sub(rf"(?<![\w<]){re.escape(valor)}(?![\w>])", marca, texto)
    return texto


def _versiones():
    """[(nombre, valor)]: repo, Python, Windows, torch y CUDA. Nunca rutas ni nombres."""
    commit = git("rev-parse", "--short", "HEAD")
    commit = commit.stdout.strip() if commit.returncode == 0 else "desconocido (no es un repo git)"
    rama = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "?"
    cambios = git("status", "--porcelain")
    sucio = cambios.returncode == 0 and any(l.strip() for l in cambios.stdout.splitlines())
    sistema = f"{platform.system()} {platform.release()} ({platform.version()})"
    datos = [("Commit", f"{commit} en {rama}" + (" con cambios locales" if sucio else "")),
             ("Python", f"{platform.python_version()} ({platform.architecture()[0]})"),
             ("Sistema", sistema)]
    try:
        import torch
        cuda = torch.version.cuda or "sin CUDA (build de CPU)"
        if torch.cuda.is_available():
            gpu = f"disponible, {torch.cuda.get_device_name(0)}"
        else:
            gpu = "no disponible"
        datos += [("torch", torch.__version__), ("CUDA", f"{cuda}; GPU {gpu}")]
    except Exception as e:
        datos += [("torch", f"no se puede importar ({type(e).__name__})"), ("CUDA", "desconocido")]
    return datos


def reporte(live=False, ruta_log=LOG_CONSOLA):
    """Informe en Markdown para un issue: versiones, cada comprobación y los últimos errores."""
    resultados = ejecutar(live=live)
    cuenta, global_ = resumen(resultados)
    lineas = ["### Informe de diagnóstico (doctor.py --reporte" + (" --live" if live else "") + ")", ""]
    lineas += [f"- **{n}:** {v}" for n, v in _versiones()]
    lineas += ["", f"**Doctor: {global_}** ({cuenta[OK]} OK, {cuenta[AVISO]} AVISO, {cuenta[ERROR]} ERROR)", "",
               "| Estado | Comprobación | Detalle |", "|---|---|---|"]
    for r in resultados:
        detalle = r["detalle"] + (f" → {r['accion']}" if r["accion"] else "")
        lineas.append(f"| {r['estado']} | {r['nombre']} | {detalle.replace('|', '/')} |")
    errores = errores_del_log(ruta_log)
    lineas += ["", f"**Últimos errores de jarvis.log** (solo su firma, sin texto de turnos; "
                   f"{'ninguno' if not errores else len(errores)}):"]
    if errores:
        lineas += ["", "```"] + [f"{veces}× {firma}" for firma, veces in errores] + ["```"]
    lineas += ["", "_Generado sin claves, direcciones, rutas personales ni texto de turnos. "
                   "Revísalo antes de publicarlo._"]
    return anonimizar("\n".join(lineas))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnóstico del entorno de JARVIS")
    parser.add_argument("--json", action="store_true", help="salida JSON estable")
    parser.add_argument("--live", action="store_true", help="valida las claves con llamadas mínimas (coste despreciable)")
    parser.add_argument("--reporte", action="store_true",
                        help="informe en Markdown para pegar en un issue (sin claves, direcciones, rutas "
                             "con tu usuario ni texto de turnos)")
    args = parser.parse_args(argv)
    if args.reporte:
        _imprimir(reporte(live=args.live))
        return 0
    resultados = ejecutar(live=args.live)
    _imprimir(a_json(resultados) if args.json else a_tabla(resultados))
    return 1 if any(r["estado"] == ERROR for r in resultados) else 0


if __name__ == "__main__":
    sys.exit(main())
