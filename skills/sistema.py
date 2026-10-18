import shutil
import subprocess
import os
import time
from datetime import datetime
import requests as req

import rutas

# ── DISCO ─────────────────────────────────────────────────────────
def espacio_disco():
    total, usado, libre = shutil.disk_usage("C:\\")
    libre_gb = libre // (2**30)
    total_gb = total // (2**30)
    return f"Disco C con {libre_gb} gigas libres de {total_gb} en total"

# ── GPU ───────────────────────────────────────────────────────────
def estado_gpu():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            partes = result.stdout.strip().split(", ")
            temp   = partes[0].strip()
            usado  = partes[1].strip()
            total  = partes[2].strip()
            return f"GPU a {temp} grados con {usado} de {total} megas de VRAM"
    except Exception:
        pass
    return "GPU no disponible"

# ── OLLAMA ────────────────────────────────────────────────────────
def estado_ollama():
    try:
        r = req.get("http://127.0.0.1:11434/api/tags", timeout=3)
        if r.status_code == 200:
            modelos = [m["name"] for m in r.json().get("models", [])]
            return f"Ollama activo con {len(modelos)} modelos cargados"
    except Exception:
        pass
    return "Ollama no responde"

# ── HORA Y FECHA ──────────────────────────────────────────────────
def hora_actual():
    return f"Son las {datetime.now().strftime('%H:%M')}"

def fecha_actual():
    dias   = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses  = ["enero","febrero","marzo","abril","mayo","junio",
               "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    ahora  = datetime.now()
    dia    = dias[ahora.weekday()]
    mes    = meses[ahora.month - 1]
    return f"Hoy es {dia} {ahora.day} de {mes} de {ahora.year}"

# ── DIRECCIÓN DEL VIENTO ──────────────────────────────────────────
def _direccion_viento(grados):
    """Convierte grados a punto cardinal en español."""
    puntos = [
        (0,   "norte"),
        (22,  "nor-noreste"),
        (45,  "noreste"),
        (67,  "este-noreste"),
        (90,  "levante"),       # Este
        (112, "este-sureste"),
        (135, "sureste"),
        (157, "sur-sureste"),
        (180, "sur"),
        (202, "sur-suroeste"),
        (225, "suroeste"),
        (247, "oeste-suroeste"),
        (270, "poniente"),      # Oeste
        (292, "oeste-noroeste"),
        (315, "noroeste"),
        (337, "nor-noroeste"),
        (360, "norte"),
    ]
    grados = grados % 360
    for i in range(len(puntos) - 1):
        mitad = (puntos[i][0] + puntos[i+1][0]) / 2
        if grados < mitad:
            return puntos[i][1]
    return "norte"

# ── TIEMPO METEOROLÓGICO — LA CIUDAD DEL PERFIL ──────────────────
# Open-Meteo (sin clave). Ciudad y coordenadas salen del perfil (rutas.py); sin coordenadas
# el tiempo está desactivado. Un fallo se dice con su causa y los transitorios (sin red,
# sin respuesta, servicio saturado o caído) se reintentan una vez tras PAUSA_TIEMPO.
HOST_TIEMPO = "api.open-meteo.com"
TIEMPO_NO_CONFIGURADO = "El tiempo no está configurado: falta tu ciudad. Ejecuta configurar.py para indicarla."


def url_tiempo():
    """URL de Open-Meteo con las coordenadas del perfil, o None si no hay."""
    coords = rutas.coordenadas()
    if not coords:
        return None
    return (f"https://{HOST_TIEMPO}/v1/forecast"
            f"?latitude={coords[0]!r}&longitude={coords[1]!r}"
            "&current_weather=true"
            "&forecast_days=1")


def tiempo_configurado():
    return url_tiempo() is not None

TIMEOUT_TIEMPO = 5
PAUSA_TIEMPO   = 1.0

SIN_RED, SIN_RESPUESTA, SATURADO, CAIDO, LIMITADO, RECHAZADO, DATOS = (
    "sin_red", "sin_respuesta", "saturado", "caido", "limitado", "rechazado", "datos")
MENSAJES_TIEMPO = {
    SIN_RED:       "No hay conexión para el tiempo",
    SIN_RESPUESTA: "El servicio del tiempo no responde",
    SATURADO:      "El servicio del tiempo está saturado ahora mismo",
    CAIDO:         "El servicio del tiempo está caído ahora mismo",
    LIMITADO:      "El servicio del tiempo ha limitado las consultas por ahora",
    RECHAZADO:     "El servicio del tiempo ha rechazado la consulta",
    DATOS:         "El servicio del tiempo ha devuelto datos que no entiendo",
}
_TRANSITORIOS = {SIN_RED, SIN_RESPUESTA, SATURADO, CAIDO}


def _descripcion_tiempo(code):
    """WMO Weather Code → descripción natural"""
    if code == 0:
        return "cielo despejado"
    if code in (1, 2):
        return "parcialmente nublado"
    if code == 3:
        return "cielo cubierto"
    if code in (45, 48):
        return "hay niebla"
    if code in (51, 53, 55):
        return "llovizna ligera"
    if code in (61, 63):
        return "está lloviendo"
    if code == 65:
        return "lluvia intensa"
    if code in (71, 73, 75):
        return "está nevando"
    if code in (80, 81, 82):
        return "chubascos"
    if code in (95, 96, 99):
        return "tormenta eléctrica"
    return "condiciones variables"


def _pedir_tiempo():
    """(texto, None) o (None, causa) de una sola petición."""
    try:
        r = req.get(url_tiempo(), timeout=TIMEOUT_TIEMPO)
    except req.exceptions.Timeout:
        return None, SIN_RESPUESTA
    except req.exceptions.RequestException:   # DNS, conexión rechazada, sin red...
        return None, SIN_RED
    if r.status_code == 503:
        return None, SATURADO
    if r.status_code == 429:
        return None, LIMITADO
    if r.status_code >= 500:
        return None, CAIDO
    if r.status_code != 200:
        return None, RECHAZADO
    try:
        datos     = r.json()["current_weather"]
        temp      = datos["temperature"]
        viento    = datos["windspeed"]
        direccion = datos.get("winddirection", None)
        desc      = _descripcion_tiempo(datos.get("weathercode", 0))
    except (ValueError, KeyError, TypeError):
        return None, DATOS
    if direccion is not None:
        viento_str = f"viento de {_direccion_viento(direccion)} a {viento} kilómetros por hora"
    else:
        viento_str = f"viento de {viento} kilómetros por hora"
    ciudad = rutas.perfil("ciudad")
    return f"{'En ' + ciudad + ' hay' if ciudad else 'Hay'} {temp} grados, {desc} y {viento_str}", None


def consultar_tiempo():
    """(ok, texto para la voz, causa o None). Reintenta una vez los fallos transitorios.
    Sin coordenadas en el perfil: (False, TIEMPO_NO_CONFIGURADO, "no_configurado"), sin red."""
    if not tiempo_configurado():
        return False, TIEMPO_NO_CONFIGURADO, "no_configurado"
    texto, causa = _pedir_tiempo()
    if causa in _TRANSITORIOS:
        time.sleep(PAUSA_TIEMPO)
        texto, causa = _pedir_tiempo()
    if causa:
        return False, MENSAJES_TIEMPO[causa], causa
    return True, texto, None


def tiempo_local():
    """Tiempo real en la ciudad del perfil. Si falla, la causa concreta ("No hay conexión para
    el tiempo", "...está saturado...") o TIEMPO_NO_CONFIGURADO si no hay ciudad."""
    try:
        return consultar_tiempo()[1]
    except Exception:
        return "No he podido consultar el tiempo por un error interno"

# ── ESTADO COMPLETO ───────────────────────────────────────────────
def estado_completo():
    return f"{hora_actual()}. {espacio_disco()}. {estado_gpu()}. {estado_ollama()}."

# ── APAGAR / REINICIAR ────────────────────────────────────────────
def apagar(minutos=1):
    os.system(f"shutdown /s /t {minutos * 60}")
    return f"El ordenador se apagará en {minutos} minutos"

def cancelar_apagado():
    os.system("shutdown /a")
    return "Apagado cancelado"

# ── CARPETAS ──────────────────────────────────────────────────────
def crear_carpeta(nombre, ubicacion="Desktop"):
    ruta = os.path.join(os.path.expanduser("~"), ubicacion, nombre)
    os.makedirs(ruta, exist_ok=True)
    return f"Carpeta {nombre} creada en {ubicacion}"

# ── BUSCAR ARCHIVOS ───────────────────────────────────────────────
def buscar_archivo(nombre, raiz="C:\\Users"):
    encontrados = []
    try:
        for root, dirs, files in os.walk(raiz):
            dirs[:] = [d for d in dirs if d not in
                       ["Windows", "Program Files", "$Recycle.Bin", "AppData"]]
            for f in files:
                if nombre.lower() in f.lower():
                    encontrados.append(os.path.join(root, f))
                    if len(encontrados) >= 5:
                        return encontrados
    except Exception:
        pass
    return encontrados